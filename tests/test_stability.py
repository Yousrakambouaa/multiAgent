import json
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from game_examples import examples
from studio.coordination import finish_stage, invalidate_validation, share_game, update_work_items
from studio.failures import model_quality_report
from studio.flexible import FlexibleComponent, FlexibleProperty, WorkItem, validate_implementation
from studio.generation import (normalize_ground_check_anchors, normalize_ground_contact_queries,
                               normalize_implicit_2d_box_collider_sizes, save_implementation)
from studio.model_selection import capabilities
from studio.registry import Registry
from studio.state import Job, write_json
from studio.supervisor import Supervisor, TASKS
from studio.agents import GameDesigner, QADebugger
from studio.unity import UnityRunner


def text_model(mid, price="0", context=65536, **extra):
    model=dict(id=mid, pricing={"prompt":price,"completion":price},
        supported_parameters=["response_format"], context_length=context,
        architecture={"modality":"text->text","input_modalities":["text"],
                      "output_modalities":["text"]})
    model.update(extra)
    return model


class DynamicModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings=patch.dict(os.environ,{"AI_PROVIDER":"openrouter","OPENROUTER_API_KEY":"test-key",
            "STUDIO_MODEL_SELECTION":"dynamic","STUDIO_AUTO_MODEL_FALLBACK":"1",
            "STUDIO_ALLOW_PAID_MODELS":"0","STUDIO_FREE_ONLY":"0"})
        settings.start();self.addCleanup(settings.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        cache=patch("studio.registry.CACHE",Path(self.temp.name));cache.start();self.addCleanup(cache.stop)
        self.registry=Registry();self.registry.roles={"developer":["pinned"],"designer":[],"art":[]}
        self.registry.verified_at="live"

    def test_real_task_evidence_beats_model_name_and_configured_order(self):
        self.registry.models={mid:text_model(mid) for mid in ("pinned","new-model","claims-to-be-best-coder")}
        self.registry.evidence={"new-model":{"developer":[{"kind":"unity","passed":True}]}}
        self.assertEqual(next(self.registry.candidates("developer")),"new-model")
        self.registry.evidence["new-model"]["developer"]=[{"kind":"unity","passed":False}]*5
        self.assertNotEqual(next(self.registry.candidates("developer")),"new-model")

    def test_discovered_paid_models_are_eligible_and_explicit_free_only_overrides_pins(self):
        self.registry.models={"pinned":text_model("pinned","0.000001"),
            "unknown-paid":text_model("unknown-paid","0.000001"),"free:free":text_model("free:free")}
        self.assertIn("unknown-paid",list(self.registry.candidates("developer")))
        with patch.dict(os.environ,{"STUDIO_ALLOW_PAID_MODELS":"1"}):
            self.assertIn("unknown-paid",list(self.registry.candidates("developer")))
        self.registry.free_only=True
        self.assertEqual(list(self.registry.candidates("developer")),["free:free"])

    def test_capability_separation_and_request_fit(self):
        model=text_model("vision",architecture={"input_modalities":["text","image"],"output_modalities":["text"]})
        model["supported_parameters"] += ["tools","reasoning"]
        self.assertEqual({key:capabilities(model)[key] for key in ("vision","image_generation","tools","reasoning")},
                         {"vision":True,"image_generation":False,"tools":True,"reasoning":True})
        self.registry.models={"vision":model,"image":text_model("image",architecture={"output_modalities":["image"]})}
        self.assertFalse(self.registry.text_eligible("image"))
        self.registry.models["vision"]["top_provider"]={"max_completion_tokens":100}
        self.assertFalse(self.registry.request_fits("vision",[],101))
        self.assertTrue(self.registry.request_fits("vision",[],100))

    async def test_cached_catalog_still_checks_key_and_key_change_invalidates_cache(self):
        calls=[];original=httpx.AsyncClient
        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/key"):
                return httpx.Response(200,json={"data":{"is_management_key":False}})
            return httpx.Response(200,json={"data":[text_model("live:free")]})
        with patch("studio.registry.httpx.AsyncClient",side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))):
            await self.registry.refresh();await self.registry.refresh()
            self.assertEqual([r.url.path for r in calls],["/api/v1/key", "/api/v1/credits", "/api/v1/models/user",
                "/api/v1/images/models", "/api/v1/key", "/api/v1/credits"])
            with patch.dict(os.environ,{"OPENROUTER_API_KEY":"new-key"}):
                await self.registry.refresh()
        self.assertEqual(len(calls),10)
        self.assertNotIn("test-key",(Path(self.temp.name)/"models.json").read_text())

    async def test_image_catalog_discovery_and_bounded_price_are_used(self):
        self.registry.roles["art"]=["pinned-image"]
        original=httpx.AsyncClient
        def handle(request):
            path=request.url.path
            if path.endswith("/key"):return httpx.Response(200,json={"data":{}})
            if path.endswith("/models/user"):return httpx.Response(200,json={"data":[]})
            if path.endswith("/images/models"):
                return httpx.Response(200,json={"data":[{"id":"new-image","architecture":{"output_modalities":["image"]}}]})
            if path.endswith("new-image/endpoints"):
                return httpx.Response(200,json={"endpoints":[{"provider_tag":"live","supported_parameters":{},
                    "pricing":[{"billable":"output_image","unit":"image","cost_usd":0.02}]}]})
            return httpx.Response(404)
        with patch.dict(os.environ,{"STUDIO_ALLOW_PAID_MODELS":"1"}),patch(
                "studio.registry.httpx.AsyncClient",side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))):
            await self.registry.refresh()
            self.assertEqual(list(self.registry.candidates("art")),["new-image"])
        self.assertEqual(list(self.registry.candidates("art")),["new-image"])
        self.registry.free_only=True
        self.assertEqual(list(self.registry.candidates("art")),[])


class CoordinationAndUnityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.request,self.spec,self.plan=examples("racing")
        self.job=Job.create(self.request,2,root=self.root)
        self.job.data["tasks"]=[dict(role=r,name=n,dependencies=d,status="pending") for r,n,d in TASKS]

    def test_stage_dependencies_are_enforced_and_repairs_invalidate_old_evidence(self):
        with self.assertRaisesRegex(RuntimeError,"unfinished dependencies"):
            finish_stage(self.job,"assembly")
        finish_stage(self.job,"game_spec")
        finish_stage(self.job,"scripts",evidence="implementation.json")
        for item in self.job.data["tasks"]:
            if item["name"] in ("compilation","assembly","EditMode","PlayMode"):
                item.update(status="passed",evidence="old")
        invalidate_validation(self.job)
        self.assertTrue(all(t["status"]=="pending" and "evidence" not in t for t in self.job.data["tasks"] if t["name"]=="PlayMode"))

    def test_shared_context_and_work_dependencies_do_not_claim_unverified_work(self):
        self.spec.tasks.append(WorkItem(id="T2",owner="qa",description="Validate playability",dependencies=["T1"],requirement_ids=["R2"]))
        share_game(self.job,self.spec,"dahl")
        self.assertEqual(self.job.data["shared_context"]["ownership"]["scene"],"developer")
        results=[dict(id="R1",verification_result="not_run",implementation_status="planned"),
                 dict(id="R2",verification_result="passed",implementation_status="implemented_unverified")]
        update_work_items(self.job,results)
        self.assertEqual(self.job.data["work_items"][1]["status"],"blocked")
        results[0]["verification_result"]="passed"
        update_work_items(self.job,results)
        self.assertTrue(all(item["status"]=="verified" for item in self.job.data["work_items"]))

    def test_paid_model_recommendations_require_comparative_unity_evidence(self):
        for passed in (True, False, False):
            self.job.event("developer","model_outcome",provider="openrouter",model="free-coder:free",
                           kind="unity",passed=passed)
        for _ in range(3):
            self.job.event("developer","model_outcome",provider="openrouter",model="paid-coder",
                           kind="unity",passed=True)
        report=model_quality_report(self.job)
        self.assertEqual(report["paid_model_recommendations"][0]["model"],"paid-coder")
        self.assertEqual(report["paid_model_recommendations"][0]["evidence"]["free_samples"],3)

        insufficient=Job.create("insufficient evidence",2,root=self.root)
        insufficient.event("developer","model_outcome",provider="openrouter",model="paid-coder",
                           kind="unity",passed=False)
        self.assertEqual(model_quality_report(insufficient)["paid_model_recommendations"],[])

    def test_provider_failures_are_reported_separately_from_model_quality(self):
        self.job.event("developer","model_failed",provider="openrouter",model="free-coder:free",
                       error="OpenRouter error 429: rate limit")
        report=model_quality_report(self.job)
        self.assertIn("Provider/API failures",report["assessment"])
        self.assertEqual(report["models"][0]["api_failures"],1)
        self.assertEqual(report["paid_model_recommendations"],[])

    def test_missing_rigidbody_is_rejected_before_unity_launch(self):
        car=next(o for o in self.plan.scenes.scenes[0].objects if o.id=="car")
        car.components=[c for c in car.components if c.type!="UnityEngine.Rigidbody"]
        with self.assertRaisesRegex(ValueError,"missing required components: Rigidbody"):
            save_implementation(self.job,self.job.directory/"UnityProject",self.spec,self.plan)

    def test_mixed_physics_and_missing_runtime_coverage_are_rejected(self):
        car=next(o for o in self.plan.scenes.scenes[0].objects if o.id=="car")
        car.components.append(FlexibleComponent(type="UnityEngine.Rigidbody2D"))
        with self.assertRaisesRegex(ValueError,"mixes 2D and 3D"):
            validate_implementation(self.spec,self.plan)
        car.components.pop();self.plan.probes[0].requirement_ids=["R1"]
        with self.assertRaisesRegex(ValueError,"Missing behavioral probes.*R2"):
            validate_implementation(self.spec,self.plan)

    def test_ground_check_anchor_is_world_positioned_below_and_parented_to_actor(self):
        scene=self.plan.scenes.scenes[0]
        actor,anchor=scene.objects[:2]
        actor.position=(0,-.5,0);actor.scale=(1,1,1)
        actor.components.extend([
            FlexibleComponent(type="UnityEngine.BoxCollider2D"),
            FlexibleComponent(type="GroundProbe",properties=[
                FlexibleProperty(name="groundCheck",kind="reference",value=anchor.id+":UnityEngine.Transform"),
                FlexibleProperty(name="groundCheckRadius",kind="float",value="0.2")])])
        anchor.parent="";anchor.position=(0,-.55,0)
        fixes=normalize_ground_check_anchors(self.plan)
        self.assertEqual(len(fixes),1)
        self.assertEqual(anchor.parent,actor.id)
        self.assertAlmostEqual(anchor.position[1],-1.05)
        self.assertEqual(normalize_ground_check_anchors(self.plan),[])

    def test_ground_overlap_uses_real_contact_fallback_without_weakening_upward_guard(self):
        script=self.plan.scripts.scripts[0]
        script.content=("class Grounded { BoxCollider2D box; LayerMask groundLayer; Rigidbody2D rb;\n "
            "private bool CheckGrounded() {\n Collider2D[] hits = Physics2D.OverlapCircleAll(origin, radius, groundLayer); "
            "return hits.Length > 0;\n } }")
        changes=normalize_ground_contact_queries(self.plan)
        self.assertEqual(len(changes),1)
        self.assertIn("rb.linearVelocity.y > 0.05f",script.content)
        self.assertIn("box.IsTouchingLayers(groundLayer)",script.content)
        self.assertLess(script.content.index("linearVelocity.y > 0.05f"),script.content.index("IsTouchingLayers"))
        self.assertEqual(normalize_ground_contact_queries(self.plan),[])

    def test_sprite_less_2d_box_colliders_get_safe_default_bounds_only_when_unspecified(self):
        actor=self.plan.scenes.scenes[0].objects[0]
        actor.sprite=""
        box=FlexibleComponent(type="UnityEngine.BoxCollider2D")
        actor.components.append(box)
        changes=normalize_implicit_2d_box_collider_sizes(self.plan)
        self.assertEqual(len(changes),1)
        self.assertEqual([(p.name,p.kind,p.value) for p in box.properties],[
            ("size","vector2","1,1")])
        self.assertEqual(normalize_implicit_2d_box_collider_sizes(self.plan),[])

        authored=FlexibleComponent(type="UnityEngine.BoxCollider2D",properties=[
            FlexibleProperty(name="size",kind="vector2",value="2,3")])
        actor.components[-1]=authored
        self.assertEqual(normalize_implicit_2d_box_collider_sizes(self.plan),[])
        self.assertEqual(authored.properties[0].value,"2,3")

        actor.sprite="Assets/Studio/Art/player.png"
        actor.components[-1]=FlexibleComponent(type="UnityEngine.BoxCollider2D")
        self.assertEqual(normalize_implicit_2d_box_collider_sizes(self.plan),[])

    def test_interrupted_planning_and_cancellation_do_not_auto_resume_as_builds(self):
        (self.root/"current.txt").write_text(self.job.data["id"])
        self.job.save()
        restored=Supervisor(self.root)
        self.assertEqual(restored.job.data["status"],"failed")
        self.job.data.update(status="building",cancel_requested=True);self.job.save()
        self.assertEqual(Supervisor(self.root).job.data["status"],"cancelled")


class HandoffContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_unity_never_inherits_provider_or_telegram_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);job=Job.create("Engine audit",0,root=root)
            project=job.directory/"UnityProject";project.mkdir()
            editor=root/"Unity.exe";editor.touch()
            async def launch(*command,**kwargs):
                self.assertTrue(all(key not in kwargs["env"] for key in
                    ("OPENROUTER_API_KEY","DAHL_API_KEY","TELEGRAM_BOT_TOKEN")))
                Path(command[command.index("-logFile")+1]).write_text("Application.AssetDatabase Initial Refresh End")
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{"STUDIO_UNITY_MODE":"batch","STUDIO_UNITY_MCP_ENABLED":"0",
                    "OPENROUTER_API_KEY":"test-or","DAHL_API_KEY":"test-dahl","TELEGRAM_BOT_TOKEN":"test-bot"}),patch(
                    "studio.unity.UNITY",editor),patch("studio.unity.asyncio.create_subprocess_exec",side_effect=launch):
                self.assertEqual((await UnityRunner(job,project).compile())["status"],"passed")

    async def test_cancelled_planning_releases_state_for_a_new_game(self):
        with tempfile.TemporaryDirectory() as temp:
            studio=Supervisor(Path(temp));studio.registry.refresh=AsyncMock()
            entered=asyncio.Event()
            async def planning(*args):
                entered.set()
                await asyncio.Event().wait()
            studio.agents["designer"].plan=planning
            task=asyncio.create_task(studio.submit("A 3D driving game"))
            await entered.wait();studio.cancel();task.cancel()
            job=await task
            self.assertEqual(job.data["status"],"cancelled")
            self.assertFalse(studio.busy())

    async def test_unity_lock_conflict_preserves_job_instead_of_marking_it_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            studio=Supervisor(Path(temp));_,spec,_=examples("racing")
            job=Job.create("racing",2,root=Path(temp));job.data["status"]="interrupted"
            write_json(job.directory/"game_spec.json",spec.model_dump());studio.job=job
            notify=AsyncMock()
            with patch("studio.supervisor.ProcessLock.__enter__",side_effect=RuntimeError("Another studio process holds .unity.lock")):
                result=await studio.approve(notify=notify)
            self.assertEqual(result,[])
            self.assertEqual(job.data["status"],"interrupted")
            self.assertEqual(job.data["stage"],"run_deferred")
            self.assertNotIn("failure_report",job.data)
            self.assertIn("preserved",notify.await_args.args[0])

    async def test_planning_contract_is_corrected_once_then_stops(self):
        request,spec,_=examples("puzzle")
        bad=spec.model_dump();bad["requirements"][0]["original_instruction"]="not in prompt"
        service=SimpleNamespace(job=SimpleNamespace(data={"cap_usd":2}),json=AsyncMock(side_effect=[bad,spec.model_dump()]))
        with patch.dict(os.environ,{"STUDIO_DESIGNER_BACKEND":"direct"}):
            result=await GameDesigner().plan(service,request)
        self.assertEqual(result,spec);self.assertEqual(service.json.await_count,2)
        self.assertIn("validation_errors",service.json.await_args.args[2])
        service.json=AsyncMock(return_value=bad)
        with patch.dict(os.environ,{"STUDIO_DESIGNER_BACKEND":"direct"}),self.assertRaisesRegex(ValueError,"quote"):
            await GameDesigner().plan(service,request)
        self.assertEqual(service.json.await_count,2)

    async def test_v2_ui_has_its_own_task_profile_without_platformer_fields(self):
        _,spec,_=examples("racing")
        service=SimpleNamespace(json=AsyncMock(return_value={"screens":["Race"],"controls":spec.controls,
            "layout":["Lap count in top-left"],"accessibility":["High contrast"]}))
        plan=await GameDesigner().plan_ui(service,spec)
        self.assertEqual(service.json.await_args.args[0],"ui")
        self.assertNotIn("touch_button_size",plan.model_dump())

    async def test_no_change_qa_implementation_repair_is_rejected(self):
        _,spec,plan=examples("racing")
        service=SimpleNamespace(job=SimpleNamespace(data={"request":"racing"}),json=AsyncMock(return_value=plan.model_dump()))
        with patch.dict(os.environ,{"STUDIO_QA_BACKEND":"direct"}),self.assertRaisesRegex(ValueError,"no implementation changes"):
            await QADebugger().repair_implementation(service,spec,plan,"Repair the failure")

    async def test_qa_contract_failure_is_recorded_and_retried_with_same_probes(self):
        _,spec,plan=examples("racing")
        changed=plan.model_copy(deep=True)
        changed.probes[0].steps.append(changed.probes[0].steps[0].model_copy(deep=True))
        repaired=plan.model_copy(deep=True)
        repaired.scripts.scripts[0].content += "\n// repair"
        service=SimpleNamespace(job=SimpleNamespace(data={"request":"racing"}),
            json=AsyncMock(side_effect=[changed.model_dump(),repaired.model_dump()]),
            record_contract_failure=Mock())
        with patch.dict(os.environ,{"STUDIO_QA_BACKEND":"direct"}):
            result=await QADebugger().repair_implementation(service,spec,plan,"Repair the failure")
        self.assertEqual(result,repaired)
        self.assertEqual(service.json.await_count,2)
        service.record_contract_failure.assert_called_once_with("qa")


if __name__=="__main__":unittest.main()
