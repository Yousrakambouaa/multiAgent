import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from contextlib import ExitStack
from types import SimpleNamespace
from game_examples import examples

from pydantic import ValidationError

from studio.agents import demo_spec
from studio.api import ModelService
from studio.assets import procedural
from studio.config import safe_path
from studio.costs import BudgetExceeded, Ledger
from studio.registry import Registry
from studio.scenes import assemble_scenes
from studio.schemas import CodeBundle, SceneSpec, ScriptFile, UIPlan
from studio.state import Job, JobCancelled, ProcessLock, write_json
from studio.supervisor import Supervisor, TASKS
from studio.unity import UnityFailure, UnityRunner, parse_test_report


class ContractsTests(unittest.TestCase):
    def setUp(self):
        self.spec=demo_spec()
        self.ui=UIPlan(title=self.spec.name,subtitle="Follow the sun",play_label="Play",accent="#F5BD58",touch_button_size=100)
        self.art={k:f"Assets/Studio/Art/{k}.png" for k in ["player","ground","coin","enemy","background"]}

    def test_shared_spec_and_multiple_scenes(self):
        bundle=assemble_scenes(self.spec,self.ui,self.art)
        self.assertEqual(len(bundle.scenes),3)
        level=next(s for s in bundle.scenes if s.kind=="level")
        self.assertTrue({"player","ground","coin","exit"}.issubset({o.role for o in level.objects}))
        player=next(o for o in level.objects if o.role=="player")
        self.assertTrue(any(p.kind=="reference" for c in player.components for p in c.properties))

    def test_old_nested_scene_shape_rejected(self):
        with self.assertRaises(ValidationError): SceneSpec.model_validate({"scenes":{"GameScene":{}}})

    def test_broken_references_and_cycles_rejected(self):
        level=next(s for s in assemble_scenes(self.spec,self.ui,self.art).scenes if s.kind=="level")
        for change in ("reference","cycle"):
            value=level.model_dump()
            if change=="cycle": value["objects"][0]["parent"]=value["objects"][0]["id"]
            else:
                value["objects"][0]["components"][0]["properties"].append({"name":"x","kind":"reference","value":"missing"})
            with self.subTest(change=change),self.assertRaises(ValidationError): SceneSpec.model_validate(value)

    def test_paths_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for path in ("../secret","a/../../secret",str(root.parent/"secret")):
                with self.subTest(path=path),self.assertRaises(ValueError): safe_path(root,path)

    def test_original_fallback_assets_are_real_images(self):
        for kind in self.art:
            image=procedural(kind,self.spec.palette)
            self.assertEqual(image.mode,"RGBA"); self.assertGreater(image.width,0)
            if kind=="player": self.assertLess(image.getextrema()[3][0],255)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.job=Job.create("test",.02,root=Path(self.temp.name)); self.ledger=Ledger(self.job)

    def test_cap_prevents_next_paid_request(self):
        self.ledger.reserve("art","model",.018)
        with self.assertRaises(BudgetExceeded): self.ledger.reserve("qa","model",.003)
        self.assertEqual(len(self.job.data["costs"]),1)

    def test_exact_cost_distinguished_from_estimate(self):
        entry=self.ledger.reserve("designer","a",.01); self.ledger.settle(entry,{"cost":.001})
        other=self.ledger.reserve("qa","b",.005); self.ledger.settle(other,{"completion_tokens":10})
        self.assertIn("API-reported: $0.001000",self.ledger.summary())
        self.assertIn("$0.005000",self.ledger.summary())

    def test_ambiguous_requests_stay_charged_on_restart(self):
        self.ledger.reserve("developer","a",.018)
        restored=Ledger(Job.load(self.job.directory/"job.json"))
        with self.assertRaises(BudgetExceeded): restored.reserve("qa","b",.005)

    def test_overspend_report_stops_job(self):
        entry=self.ledger.reserve("art","a",.01)
        with self.assertRaises(BudgetExceeded): self.ledger.settle(entry,{"cost":.03})

    def test_price_jump_stops_calls_even_with_budget_remaining(self):
        entry=self.ledger.reserve("art","a",.001)
        with self.assertRaises(BudgetExceeded): self.ledger.settle(entry,{"cost":.002})
        with self.assertRaises(BudgetExceeded): self.ledger.reserve("qa","b",.001)

    def test_os_lock_excludes_second_process_handle(self):
        path=self.job.directory/"test.lock"
        with ProcessLock(path):
            with self.assertRaises((RuntimeError,OSError)): ProcessLock(path).__enter__()


class VerificationTests(unittest.TestCase):
    def test_empty_or_skipped_test_report_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"tests.xml"
            for xml in ['<test-run result="Passed"/>','<test-run result="Passed"><test-case result="Skipped"/></test-run>']:
                path.write_text(xml)
                with self.assertRaises(UnityFailure): parse_test_report(path,"PlayMode")


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        backend=patch.dict(os.environ,{"STUDIO_UNITY_MODE":"batch","STUDIO_UNITY_MCP_ENABLED":"0"})
        backend.start();self.addCleanup(backend.stop)
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.studio=Supervisor(self.root)

    async def test_create_saves_plan_and_waits_for_optional_user_approval(self):
        with patch.object(self.studio.registry,"refresh",AsyncMock()):
            with patch("studio.supervisor.GameDesigner.plan",AsyncMock(return_value=demo_spec())):
                with patch.object(self.studio,"approve",AsyncMock()) as approve:
                    job=await self.studio.submit("Create a woodland platformer")
        self.assertEqual(job.data["status"],"awaiting_approval")
        self.assertFalse((job.directory/"UnityProject").exists())
        approve.assert_not_awaited()

    async def test_ordinary_unrelated_message_is_rejected(self):
        with patch.object(self.studio.registry,"refresh",AsyncMock()):
            with patch("studio.supervisor.ModelService.json",AsyncMock(return_value={"decision":"reject"})):
                job=await self.studio.submit("please write a poem",explicit_create=False)
        self.assertEqual(job.data["status"],"rejected"); self.assertFalse((job.directory/"UnityProject").exists())

    async def test_clarification_answer_during_planning_is_applied_without_restarting_original_prompt(self):
        original="Create a minimalist 3D puzzle game for PC"
        _,spec,_=examples("puzzle")
        spec.clarification_questions=["What rules should the puzzle use?"]
        prompts=[]
        async def plan(service,prompt):
            prompts.append(prompt)
            if len(prompts)==1:
                self.studio.add_instruction("Use a grid and rotate pieces with Q and E")
            return spec
        with patch.object(self.studio.registry,"refresh",AsyncMock()),patch("studio.supervisor.GameDesigner.plan",side_effect=plan),patch.object(self.studio,"approve",AsyncMock()) as approve:
            job=await self.studio.submit(original)
        self.assertEqual(len(prompts),2)
        self.assertIn(original,prompts[1]); self.assertIn("rotate pieces with Q and E",prompts[1])
        self.assertEqual(job.data["request"],original)
        self.assertEqual(job.data["additional_instructions"][0]["status"],"applied")
        saved=json.loads((job.directory/"game_spec.json").read_text())
        self.assertEqual(saved["clarification_questions"],[])
        self.assertEqual(job.data["status"],"awaiting_approval")
        approve.assert_not_awaited()

    async def test_late_instruction_is_persisted_and_does_not_replace_original(self):
        original="Create a 3D racing game"
        job=Job.create(original,2,root=self.root)
        job.data["status"]="building";job.save();self.studio.job=job
        response=self.studio.add_instruction("Make the track snowy")
        saved=Job.load(job.directory/"job.json")
        self.assertIn("queued",response)
        self.assertEqual(saved.data["request"],original)
        self.assertEqual(saved.data["additional_instructions"][0],{"text":"Make the track snowy","status":"queued"})

    async def test_persistent_interruption_and_cancellation(self):
        job=Job.create("test",2,root=self.root); job.data["status"]="building"; job.save()
        (self.root/"current.txt").write_text(job.data["id"])
        studio=Supervisor(self.root); self.assertEqual(studio.job.data["status"],"interrupted")
        studio.cancel(); self.assertEqual(studio.job.data["status"],"cancelled")
        with self.assertRaises(JobCancelled): studio.job.check_cancelled()

    async def test_budget_failure_happens_before_network_post(self):
        job=Job.create("test",.00001,root=self.root)
        service=ModelService(Registry(),job)
        with patch("studio.api.httpx.AsyncClient") as client:
            with self.assertRaises(BudgetExceeded): await service.post("images",{},"art","model",.01)
            client.assert_not_called()


    async def exercise_repair(self,failures,assembly_failures=None):
        job=Job.create("platformer",2,root=self.root)
        job.data["status"]="awaiting_approval"
        job.data["tasks"]=[dict(role=r,name=n,dependencies=d,status="pending") for r,n,d in TASKS]
        job.save();self.studio.job=job;self.studio.registry.models={"available":{}}
        spec=demo_spec();write_json(job.directory/"game_spec.json",spec.model_dump())
        project=job.directory/"UnityProject";(project/"Assets/Studio/Generated").mkdir(parents=True)
        ui=UIPlan(title=spec.name,subtitle="Play",play_label="Play",accent="#F5BD58",touch_button_size=100)
        bundle=CodeBundle(scripts=[ScriptFile(filename="GeneratedRules.cs",content="public class GeneratedRules : Studio.Runtime.LevelRules { }")])
        art={a.kind:f"Assets/Studio/Art/{a.id}.png" for a in spec.required_assets}
        async def fake_art(*args):
            write_json(job.directory/"art_manifest.json",[dict(kind=a.kind,path=art[a.kind],source="original procedural fallback") for a in spec.required_assets])
            return art
        runner=SimpleNamespace(compile=AsyncMock(side_effect=failures),assemble=AsyncMock(side_effect=assembly_failures,return_value={"success":True}),tests=AsyncMock(return_value={"status":"passed"}),android=AsyncMock(return_value={"status":"passed","device_test":"unverified"}))
        runner.scene_repair=AsyncMock(return_value=assemble_scenes(spec,ui,art))
        repair=AsyncMock(return_value=bundle)
        with ExitStack() as stack:
            for target,value in {
                "Registry.refresh":AsyncMock(),
                "bootstrap":lambda job,dimension="2D":project,"UnityDeveloper.generate":AsyncMock(return_value=bundle),
                "GameDesigner.plan_ui":AsyncMock(return_value=ui),"ArtDesigner.create":fake_art,
                "UnityDeveloper.repair_scenes":runner.scene_repair,
                "validate_files":lambda *args:None,"UnityRunner":lambda *args:runner,
                "QADebugger.repair":repair,"package":lambda *args:[job.directory/"result.zip"],
            }.items(): stack.enter_context(patch("studio.supervisor."+target,value))
            await self.studio.approve()
        return job,runner,repair

    async def test_real_failure_is_repaired_and_all_affected_stages_rerun(self):
        job,runner,repair=await self.exercise_repair([UnityFailure("compile","error CS1002"),{"status":"passed"}])
        self.assertEqual(job.data["status"],"completed");self.assertEqual(job.data["repairs"],1)
        self.assertEqual(runner.compile.await_count,2);self.assertEqual(runner.tests.await_count,2)
        repair.assert_awaited_once()

    async def test_repair_stops_after_three_rounds(self):
        job,runner,repair=await self.exercise_repair([UnityFailure("compile","error CS1002") for _ in range(4)])
        self.assertEqual(job.data["status"],"failed");self.assertEqual(repair.await_count,3)
        runner.assemble.assert_not_awaited()

    async def test_scene_failure_is_repaired_by_the_existing_unity_developer(self):
        job,runner,code_repair=await self.exercise_repair(
            [{"status":"passed"},{"status":"passed"}],
            [UnityFailure("assembly","Missing serialized reference","scene"),{"success":True}])
        self.assertEqual(job.data["status"],"completed")
        runner.scene_repair.assert_awaited_once()
        code_repair.assert_not_awaited()
        self.assertEqual(runner.compile.await_count,2)
        self.assertEqual(runner.assemble.await_count,2)

    async def test_infrastructure_error_does_not_spend_on_qa(self):
        job,runner,repair=await self.exercise_repair([UnityFailure("compile","Internal build system error","infrastructure")])
        self.assertEqual(job.data["status"],"failed");repair.assert_not_awaited()

    async def test_tt_is_rejected_without_network(self):
        with patch.object(self.studio.registry,"refresh",AsyncMock()) as refresh:
            job=await self.studio.submit("tt")
        self.assertEqual(job.data["status"],"rejected");refresh.assert_not_awaited()

    async def test_low_disk_space_prevents_android_process(self):
        job=Job.create("test",2,root=self.root);runner=UnityRunner(job,job.directory)
        with patch("studio.unity.available_space",return_value=100):
            with patch.object(runner,"run",AsyncMock()) as run:
                with self.assertRaises(UnityFailure): await runner.android()
                run.assert_not_awaited()

    async def test_framework_wrapped_cancellation_still_marks_cancelled(self):
        job=Job.create("test",2,root=self.root);job.data["cancel_requested"]=True
        self.studio.fail(job,RuntimeError("framework stopped"))
        self.assertEqual(job.data["status"],"cancelled")


class FailedJobRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        backend=patch.dict(os.environ,{"STUDIO_UNITY_MODE":"batch","STUDIO_UNITY_MCP_ENABLED":"0"})
        backend.start();self.addCleanup(backend.stop)

    async def test_approve_retries_failed_job_from_its_saved_spec(self):
        _,spec,_=examples("racing")
        job=SimpleNamespace(directory=Path("virtual-job"),data={"id":"retry-test","request":"Create a 3D racing game",
            "status":"failed","error":"old shape error","effective_request":"Create a 3D racing game",
            "additional_instructions":[],"instruction_revision":0,"costs":[],"cap_usd":2},
            save=Mock(),event=Mock(),check_cancelled=Mock())
        studio=Supervisor.__new__(Supervisor)
        studio.root=Path("virtual-root")
        studio.job=job
        studio.lock=asyncio.Lock()
        studio.registry=SimpleNamespace(refresh=AsyncMock(), provider=SimpleNamespace(name="dahl"))
        service=SimpleNamespace(unity_context=AsyncMock())
        class NoopLock:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
        real_is_file=Path.is_file
        def has_saved_spec(path):
            if path==job.directory/"game_spec.json":return True
            return real_is_file(path)
        with patch("studio.coordination.share_game"),patch("studio.supervisor.ProcessLock",NoopLock),patch("studio.supervisor.ModelService",return_value=service),patch("studio.supervisor.load_game_spec",return_value=spec),patch("studio.supervisor.write_json"),patch("studio.supervisor.asyncio.to_thread",new=AsyncMock(return_value=job.directory/"UnityProject")),patch("studio.generation.build_flexible",AsyncMock(return_value=["artifact"])) as build,patch.object(Path,"is_file",has_saved_spec),patch.object(Path,"read_text",return_value="{}"):
            await studio.approve()
        self.assertEqual(job.data["status"],"building")
        self.assertEqual(job.data["error"],"")
        build.assert_awaited_once()


if __name__=="__main__": unittest.main()
