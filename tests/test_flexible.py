import asyncio
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from game_examples import REQUESTS, examples
from studio.agents import GameDesigner, UnityDeveloper, demo_spec
from studio.assets import ArtDesigner
from studio.flexible import (GameSpecV2, Implementation, load_game_spec,
                            requirement_report, validate_implementation, validate_prompt_trace)
from studio.generation import build_flexible, save_implementation
from studio.schemas import UIPlan
from studio.state import Job, write_json
from studio.supervisor import Supervisor, TASKS
from studio.unity import UnityFailure, UnityRunner


class FlexibleContractTests(unittest.TestCase):
    def test_three_genres_produce_different_architectures(self):
        signatures=[]
        for genre in REQUESTS:
            request,spec,plan=examples(genre)
            validate_prompt_trace(spec,request)
            validate_implementation(spec,plan)
            signatures.append({c.type for s in plan.scenes.scenes for o in s.objects for c in o.components})
            self.assertNotIn("GeneratedRules",signatures[-1])
            self.assertEqual(len(plan.scenes.scenes),1)
        self.assertIn("UnityEngine.Rigidbody2D",signatures[0])
        self.assertIn("UnityEngine.Rigidbody",signatures[1])
        self.assertNotIn("UnityEngine.Rigidbody2D",signatures[1])
        self.assertNotIn("UnityEngine.Rigidbody2D",signatures[2])
        self.assertIn("TogglePuzzle",signatures[2])

    def test_old_platformer_spec_is_still_readable(self):
        old=demo_spec()
        self.assertEqual(load_game_spec(old.model_dump()),old)

    def test_arbitrary_genres_targets_and_single_scene_are_allowed(self):
        _,spec,_=examples("puzzle")
        value=spec.model_dump();value.update(genre="turn based ecological simulation",target_platform="WebGL")
        result=GameSpecV2.model_validate(value)
        self.assertEqual(result.required_assets,[])
        self.assertEqual(result.scenes,["Game"])

    def test_invented_prompt_excerpts_are_rejected(self):
        request,spec,_=examples("racing")
        spec.requirements[0].original_instruction="collect coins"
        with self.assertRaisesRegex(ValueError,"quote"):validate_prompt_trace(spec,request)

    def test_task_dependency_cycles_and_unknown_requirements_are_rejected(self):
        _,spec,_=examples("puzzle")
        for field in ("dependencies","requirement_ids"):
            value=spec.model_dump();value["tasks"][0][field]=["T1"] if field=="dependencies" else ["R99"]
            with self.subTest(field=field),self.assertRaises(ValidationError):GameSpecV2.model_validate(value)

    def test_missing_task_links_are_filled_without_dropping_requirements(self):
        _,spec,_=examples("puzzle")
        value=spec.model_dump()
        value["tasks"]=[dict(id="T1",description="Implement puzzle",owner="developer",dependencies=[],requirement_ids=["R1"])]
        result=GameSpecV2.model_validate(value)
        covered={rid for task in result.tasks for rid in task.requirement_ids}
        self.assertEqual(covered,{requirement.id for requirement in spec.requirements})
        self.assertEqual(value["tasks"][0]["requirement_ids"],["R1"])

    def test_developer_accepts_flattened_script_list(self):
        request,spec,plan=examples("racing")
        response=plan.model_dump()
        response["scripts"]=response["scripts"]["scripts"]
        service=SimpleNamespace(job=SimpleNamespace(data={"request":request}),json=AsyncMock(return_value=response))
        result=asyncio.run(UnityDeveloper().implement(service,spec,{"controls":spec.controls}, {}))
        self.assertEqual(result,plan)

    def test_developer_accepts_flattened_scene_list_and_keeps_scene_requirement_links(self):
        request,spec,plan=examples("racing")
        response=plan.model_dump()
        scene_bundle=response.pop("scenes")
        response.update(title=scene_bundle["title"],dimension=scene_bundle["dimension"],
                        orientation=scene_bundle["orientation"],materials=scene_bundle["materials"])
        response["scenes"]=scene_bundle["scenes"]
        response["scenes"][0]["requirement_ids"]=["R1","R2"]
        service=SimpleNamespace(job=SimpleNamespace(data={"request":request}),json=AsyncMock(return_value=response))
        result=asyncio.run(UnityDeveloper().implement(service,spec,{"controls":spec.controls}, {}))
        self.assertEqual(result.scenes.dimension,spec.dimension)
        self.assertEqual(result.scenes.scenes[0].requirement_ids,["R1","R2"])

    def test_3d_models_cannot_use_image_workflow(self):
        _,spec,_=examples("racing")
        value=spec.model_dump()
        value["required_assets"]=[dict(id="car",kind="model",prompt="detailed car model",workflow="image",requirement_ids=["R1"])]
        with self.assertRaisesRegex(ValidationError,"cannot produce models"):GameSpecV2.model_validate(value)

    def test_dimension_scene_orientation_and_probe_reference_changes_are_rejected(self):
        _,spec,plan=examples("racing")
        for change in ("dimension","scene","orientation","probe","scene_requirement"):
            candidate=deepcopy(plan)
            if change=="dimension":candidate.scenes.dimension="2D"
            elif change=="scene":candidate.scenes.scenes[0].name="Substitute"
            elif change=="orientation":candidate.scenes.orientation="portrait"
            elif change=="scene_requirement":candidate.scenes.scenes[0].requirement_ids=["R99"]
            else:candidate.probes[0].steps[0].object_id="missing"
            with self.subTest(change=change),self.assertRaises(ValueError):validate_implementation(spec,candidate)

    def test_claimed_implementation_cannot_pass_without_runtime_evidence(self):
        _,spec,plan=examples("puzzle")
        results=requirement_report(spec,plan)
        self.assertTrue(all(r["verification_result"]=="not_run" for r in results))
        evidence={"checks":[dict(id="solve",passed=True)]}
        self.assertTrue(all(r["verification_result"]=="passed" for r in requirement_report(spec,plan,evidence)))

    def test_failed_missing_and_unavailable_asset_evidence_cannot_pass(self):
        _,spec,plan=examples("platformer")
        runtime={"checks":[dict(id="jump",passed=False),dict(id="collect",passed=True)]}
        result=requirement_report(spec,plan,runtime,[dict(status="unavailable",requirement_ids=["R2"])])
        self.assertEqual(result[0]["verification_result"],"failed")
        self.assertEqual(result[1]["verification_result"],"asset_unavailable")
        self.assertEqual(result[1]["implementation_status"],"incomplete")

    def test_visual_requirements_are_not_auto_verified(self):
        _,spec,plan=examples("puzzle");spec.requirements[0].verification_method="manual"
        result=requirement_report(spec,plan,{"checks":[dict(id="solve",passed=True)]})
        self.assertEqual(result[0]["verification_result"],"manual_review_required")

    def test_writes_multiple_scripts_without_platformer_names_and_rejects_scope_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            job=Job.create("racing",2,root=Path(temp));project=job.directory/"UnityProject"
            _,spec,plan=examples("racing")
            save_implementation(job,project,spec,plan)
            self.assertTrue((project/"Assets/Studio/Generated/RaceCar.cs").is_file())
            self.assertFalse((project/"Assets/Studio/Generated/GeneratedRules.cs").exists())
            self.assertEqual(json.loads((project/"Assets/Resources/studio_scenes.json").read_text())["dimension"],"3D")
            plan.scripts.scripts[0].content+=" // System.IO.File"
            with self.assertRaises(ValueError):save_implementation(job,project,spec,plan)


class FlexibleWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.studio=Supervisor(self.root)

    async def test_create_plans_all_genres_and_preserves_distinct_game_specs(self):
        specs=[]
        for genre in REQUESTS:
            request,spec,_=examples(genre)
            with patch.object(self.studio.registry,"refresh",AsyncMock()),patch.object(self.studio.agents["designer"],"plan",AsyncMock(return_value=spec)),patch.object(self.studio,"approve",AsyncMock()) as approve:
                job=await self.studio.submit(request)
            self.assertEqual(job.data["status"],"awaiting_approval")
            self.assertFalse((job.directory/"UnityProject").exists())
            self.assertIn(spec.genre,self.studio.plan_text())
            approve.assert_not_awaited()
            specs.append(spec)
            self.studio.cancel()
        self.assertEqual(len({spec.genre for spec in specs}),3)
        self.assertEqual(len({tuple(spec.mechanics) for spec in specs}),3)

    async def test_planner_questions_are_never_returned_as_user_clarification(self):
        request,spec,_=examples("puzzle");spec.clarification_questions=["Which puzzle rules?"]
        notifications=[]
        async def notify(text):notifications.append(text)
        with patch.object(self.studio.registry,"refresh",AsyncMock()),patch.object(self.studio.agents["designer"],"plan",AsyncMock(return_value=spec)),patch.object(self.studio,"approve",AsyncMock()) as approve:
            job=await self.studio.submit(request,notify=notify)
        self.assertEqual(job.data["status"],"awaiting_approval")
        self.assertNotIn("Please clarify", "\n".join(notifications))
        saved=json.loads((job.directory/"game_spec.json").read_text())
        self.assertEqual(saved["clarification_questions"],[])
        self.assertIn("/approve", "\n".join(notifications))
        approve.assert_not_awaited()

    async def test_unavailable_3d_asset_does_not_call_image_generation_or_create_placeholder(self):
        _,spec,_=examples("racing")
        from studio.flexible import FlexibleAsset
        spec.required_assets=[FlexibleAsset(id="car",kind="model",prompt="realistic detailed car",workflow="unavailable",requirement_ids=["R1"])]
        job=Job.create("test",2,root=self.root)
        service=SimpleNamespace(job=job,post=AsyncMock())
        art=await ArtDesigner().create(service,spec,job.directory/"UnityProject")
        service.post.assert_not_called()
        self.assertEqual(art["car"]["status"],"unavailable")
        self.assertEqual(art["car"]["path"],"")

    async def test_unity_requires_fresh_complete_probe_evidence_even_when_nunit_passes(self):
        _,spec,plan=examples("puzzle")
        job=Job.create("test",2,root=self.root);project=job.directory/"UnityProject"
        save_implementation(job,project,spec,plan)
        runner=UnityRunner(job,project)
        async def fake_run(*args,**kwargs):
            write_json(project/"Artifacts/runtime_checks.json",{"checks":[]})
            (project/"Artifacts/PlayMode.xml").write_text('<test-run result="Passed"><test-case fullname="PromptRuntimeTests.ExecutesEveryRequirementLinkedGameplayProbe" result="Passed"/></test-run>')
        with patch.object(runner,"run",fake_run),self.assertRaisesRegex(UnityFailure,"missing or failed"):
            await runner.tests("PlayMode")

    async def test_completed_runtime_with_visual_review_is_packaged_as_partial(self):
        request,spec,plan=examples("puzzle")
        spec.requirements[0].verification_method="manual"
        job=Job.create(request,2,root=self.root);self.studio.job=job
        job.data["tasks"]=[dict(role=r,name=n,dependencies=d,status="pending") for r,n,d in TASKS]
        project=job.directory/"UnityProject";service=SimpleNamespace(job=job)
        ui=UIPlan(title=spec.name,subtitle="Puzzle",play_label="Play",accent="#112233",touch_button_size=100)
        async def art(*args):write_json(job.directory/"art_manifest.json",[]);return {}
        async def runtime(mode):
            if mode=="PlayMode":write_json(project/"Artifacts/runtime_checks.json",{"checks":[dict(id="solve",passed=True)]})
            return {"status":"passed"}
        runner=SimpleNamespace(compile=AsyncMock(return_value={"status":"passed"}),assemble=AsyncMock(return_value={"success":True}),tests=runtime)
        from studio.flexible import PlanReview
        with patch.object(self.studio.agents["designer"],"plan_ui",AsyncMock(return_value=ui)) as plan_ui,patch.object(self.studio.agents["art"],"create",art),patch.object(self.studio.agents["developer"],"implement",AsyncMock(return_value=plan)) as implement,patch.object(self.studio.agents["qa"],"review_implementation",AsyncMock(return_value=PlanReview())),patch("studio.generation.UnityRunner",return_value=runner),patch("studio.generation.package",return_value=[Path("partial.zip")]):
            await build_flexible(self.studio,service,spec,project,None,False)
        plan_ui.assert_not_awaited()
        expected_ui={"ui_requirements":spec.ui_requirements,"controls":spec.controls,"target_platform":spec.target_platform}
        self.assertEqual(implement.await_args.args[2],expected_ui)
        self.assertEqual(json.loads((job.directory/"ui_plan.json").read_text()),expected_ui)
        self.assertEqual(job.data["status"],"partial")
        report=json.loads((job.directory/"validation_report.json").read_text())
        self.assertEqual(report["requirements"][0]["verification_result"],"manual_review_required")


if __name__=="__main__":unittest.main()
