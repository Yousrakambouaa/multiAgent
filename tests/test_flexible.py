import asyncio
import json
import os
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
from studio.flexible import (FlexibleComponent, FlexibleObject, FlexibleProperty, GameUIPlan, GameSpecV2, Implementation, load_game_spec,
                            enforce_requested_dimension, explicit_requested_dimension, requirement_report,
                            validate_implementation, validate_prompt_trace)
from studio.generation import autowire_named_component_references, build_flexible, save_implementation
from studio.schemas import ScriptFile, UIPlan
from studio.state import Job, write_json
from studio.supervisor import Supervisor, TASKS
from studio.unity import UnityFailure, UnityRunner


class FlexibleContractTests(unittest.TestCase):
    def test_explicit_prompt_dimension_overrides_planner_drift_and_rejects_wrong_physics(self):
        self.assertEqual(explicit_requested_dimension("Create a 3D racing game"),"3D")
        self.assertEqual(explicit_requested_dimension("Make a two-dimensional puzzle game"),"2D")
        self.assertEqual(explicit_requested_dimension("Not a 2D game; make it 3D"),"3D")
        self.assertEqual(explicit_requested_dimension("A 2D game with 3D character models"),"2D")

        request,spec,plan=examples("racing")
        spec.dimension="2D"
        self.assertEqual(enforce_requested_dimension("Create a 3D racing game",spec),
            {"requested":"3D","planned":"2D"})
        self.assertEqual(spec.dimension,"3D")
        self.assertEqual(enforce_requested_dimension("Create a 2D game",spec),
            {"requested":"2D","planned":"3D"})
        self.assertEqual(spec.dimension,"2D")
        spec.dimension="3D"

        car=next(obj for obj in plan.scenes.scenes[0].objects if obj.id=="car")
        physics={"Rigidbody","BoxCollider","SphereCollider","CapsuleCollider","MeshCollider","CharacterController",
            "Rigidbody2D","BoxCollider2D","CircleCollider2D","CapsuleCollider2D","PolygonCollider2D","EdgeCollider2D","CompositeCollider2D"}
        for obj in plan.scenes.scenes[0].objects:
            obj.components=[component for component in obj.components if component.type.rsplit(".",1)[-1] not in physics]
        car.components.append(FlexibleComponent(type="UnityEngine.Rigidbody2D"))
        with self.assertRaisesRegex(ValueError,"3D game object.*2D physics"):
            validate_implementation(spec,plan)

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

    def test_probe_display_name_resolves_to_scene_id(self):
        _,spec,plan=examples("racing")
        next(o for o in plan.scenes.scenes[0].objects if o.id=="car").name="Race Car"
        plan.probes[0].assertions[0].object_id="Race Car"
        validate_implementation(spec,plan)
        self.assertEqual(plan.probes[0].assertions[0].object_id,"car")

    def test_probe_error_identifies_missing_target_and_scene(self):
        _,spec,plan=examples("racing")
        plan.probes[0].assertions[0].object_id="missing_car"
        with self.assertRaisesRegex(ValueError,"missing_car.*drive_to_finish.*Game.*assertions\\[0\\].*Available IDs:.*car"):
            validate_implementation(spec,plan)

    def test_probe_object_lookup_only_for_actions_that_use_it(self):
        _,spec,plan=examples("racing")
        plan.probes[0].steps[0].object_id="Keyboard"
        validate_implementation(spec,plan)
        plan.probes[0].assertions[0].object_id=""
        with self.assertRaisesRegex(ValueError,"Unknown probe object"):
            validate_implementation(spec,plan)

    def test_probe_target_cannot_resolve_from_another_scene(self):
        _,spec,plan=examples("racing")
        other=deepcopy(plan.scenes.scenes[0]);other.name="Other"
        other.objects[0].id="other_camera";other.objects[0].name="Other Camera"
        spec.scenes.append("Other");plan.scenes.scenes.append(other)
        plan.probes[0].assertions[0].object_id="other_camera"
        with self.assertRaisesRegex(ValueError,"Unknown probe object"):
            validate_implementation(spec,plan)

    def test_developer_repairs_invalid_probe_before_unity_and_saves_diagnostics(self):
        request,spec,plan=examples("racing")
        bad=plan.model_dump();bad["probes"][0]["assertions"][0]["object_id"]="missing_car"
        with tempfile.TemporaryDirectory() as temp:
            job=Job.create(request,2,root=Path(temp))
            service=SimpleNamespace(job=job,json=AsyncMock(side_effect=[bad,plan.model_dump()]))
            result=asyncio.run(UnityDeveloper().implement(service,spec,{},{}))
            self.assertEqual(result,plan)
            self.assertEqual(service.json.await_count,2)
            payload=service.json.await_args.args[2]
            self.assertIn("missing_car",payload["validation_errors"])
            self.assertEqual(payload["previous_implementation"],bad)
            saved=json.loads((job.directory/"implementation_response_1.json").read_text())
            self.assertEqual(saved,json.loads(json.dumps(bad)))
            self.assertTrue((job.directory/"implementation_diagnostics_1.json").is_file())

    def test_developer_contract_retries_are_bounded_and_do_not_swallow_service_errors(self):
        request,spec,plan=examples("racing")
        bad=plan.model_dump();bad["probes"][0]["assertions"][0]["object_id"]="missing_car"
        service=SimpleNamespace(job=SimpleNamespace(data={"request":request}),json=AsyncMock(return_value=bad))
        with self.assertRaisesRegex(ValueError,"after 3 attempts.*missing_car"):
            asyncio.run(UnityDeveloper().implement(service,spec,{},{}))
        self.assertEqual(service.json.await_count,3)
        service.json=AsyncMock(side_effect=RuntimeError("authentication rejected"))
        with self.assertRaisesRegex(RuntimeError,"authentication rejected"):
            asyncio.run(UnityDeveloper().implement(service,spec,{},{}))
        service.json.assert_awaited_once()

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
            else:candidate.probes[0].assertions[0].object_id="missing"
            with self.subTest(change=change),self.assertRaises(ValueError):validate_implementation(spec,candidate)

    def test_scene_validation_requires_a_camera_and_one_graphic_per_ui_object(self):
        _,spec,plan=examples("puzzle")
        plan.scenes.scenes[0].objects[0].components=[]
        with self.assertRaisesRegex(ValueError,"must explicitly declare a Camera"):
            validate_implementation(spec,plan)
        _,spec,plan=examples("puzzle")
        puzzle=next(obj for obj in plan.scenes.scenes[0].objects if obj.id=="puzzle")
        puzzle.components.extend([FlexibleComponent(type="Image"),FlexibleComponent(type="Text")])
        with self.assertRaisesRegex(ValueError,"multiple UGUI Graphic"):
            validate_implementation(spec,plan)

    def test_preflight_rejects_invalid_transforms_colliders_and_component_references(self):
        _,spec,plan=examples("racing")
        car=next(obj for obj in plan.scenes.scenes[0].objects if obj.id=="car")
        car.scale=(1,0,2)
        with self.assertRaisesRegex(ValueError,"zero transform scale"):
            validate_implementation(spec,plan)
        car.scale=(1,1,2)
        car.components.append(FlexibleComponent(type="UnityEngine.BoxCollider",properties=[
            dict(name="size",kind="vector3",value="1,0,1")]))
        with self.assertRaisesRegex(ValueError,"Collider dimensions must be positive"):
            validate_implementation(spec,plan)
        car.components[-1].properties[0].value="1,1,1"
        camera=next(obj for obj in plan.scenes.scenes[0].objects if obj.id=="camera")
        camera.components[1].properties[0].value="car:UnityEngine.Camera"
        with self.assertRaisesRegex(ValueError,"component not declared"):
            validate_implementation(spec,plan)

    def test_mixed_dimension_scenes_must_explicitly_choose_camera_projection(self):
        _,spec,plan=examples("puzzle")
        spec.dimension="mixed"
        plan.scenes.dimension="mixed"
        camera=next(obj for obj in plan.scenes.scenes[0].objects if obj.id=="camera")
        camera.components[0].properties.clear()
        with self.assertRaisesRegex(ValueError,"must explicitly set its Camera projection"):
            validate_implementation(spec,plan)
        camera.components[0].properties.append(FlexibleProperty(name="orthographic",kind="bool",value="true"))
        validate_implementation(spec,plan)

    def test_screen_ui_rectangles_must_fit_inside_the_canvas_reference_frame(self):
        _,spec,plan=examples("puzzle")
        scene=plan.scenes.scenes[0]
        scene.objects.extend([
            FlexibleObject.model_validate(dict(id="canvas",name="Canvas",position=[0,0,0],components=[
                dict(type="Canvas",properties=[dict(name="renderMode",kind="enum",value="ScreenSpaceOverlay")]),
                dict(type="CanvasScaler",properties=[dict(name="referenceResolution",kind="vector2",value="[800,600]")]),
            ])),
            FlexibleObject.model_validate(dict(id="scoreText",name="ScoreText",parent="canvas",position=[-350,220,0],components=[
                dict(type="Text"),dict(type="UnityEngine.RectTransform",properties=[dict(name="sizeDelta",kind="vector2",value="[300,60]")]),
            ])),
        ])
        with self.assertRaisesRegex(ValueError,"scoreText.*extends beyond"):
            validate_implementation(spec,plan)
        scene.objects[-1].position=(-230,220,0)
        validate_implementation(spec,plan)

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

    def test_unity_stages_refuse_a_scene_dimension_that_differs_from_project_setup(self):
        _,spec,plan=examples("racing")
        with tempfile.TemporaryDirectory() as temp:
            job=Job.create("dimension mismatch",2,root=Path(temp))
            project=job.directory/"UnityProject"
            save_implementation(job,project,spec,plan)
            write_json(project/"Assets/Resources/studio_dimension.json",{"dimension":"2D"})
            with self.assertRaisesRegex(UnityFailure,"does not match"):
                UnityRunner(job,project).validate_dimension()

    def test_save_implementation_autowires_named_required_component_references(self):
        request,spec,plan=examples("puzzle")
        scene=plan.scenes.scenes[0]
        scene.objects.extend([
            FlexibleObject.model_validate(dict(id="collector",name="Collector",position=[0,0,0],components=[dict(type="CoinCollector")])),
            FlexibleObject.model_validate(dict(id="coin",name="Coin",position=[0,0,0],components=[dict(type="Coin")])),
            FlexibleObject.model_validate(dict(id="scoreText",name="Score",position=[0,0,0],components=[dict(type="Text")])),
            FlexibleObject.model_validate(dict(id="winText",name="Win",position=[0,0,0],components=[dict(type="UnityEngine.UI.Text")])),
            FlexibleObject.model_validate(dict(id="touchButton",name="Touch Button",position=[0,0,0],components=[dict(type="Image"),dict(type="Button")])),
        ])
        plan.scripts.scripts.extend([
            ScriptFile(filename="CoinCollector.cs",content="using UnityEngine; using UnityEngine.UI; public class CoinCollector : MonoBehaviour { public Text scoreText; public Text winText; }"),
            ScriptFile(filename="Coin.cs",content="using UnityEngine; public class Coin : MonoBehaviour { public CoinCollector collector; }"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            job=Job.create(request,2,root=Path(temp));project=job.directory/"UnityProject"
            save_implementation(job,project,spec,plan)
            props={obj.id:{prop.name:prop.value for component in obj.components for prop in component.properties}
                   for obj in scene.objects}
            self.assertEqual(props["collector"]["scoreText"],"scoreText:Text")
            self.assertEqual(props["collector"]["winText"],"winText:UnityEngine.UI.Text")
            self.assertEqual(props["coin"]["collector"],"collector:CoinCollector")
            ui_components={component.type.rsplit(".",1)[-1] for obj in scene.objects for component in obj.components}
            self.assertIn("EventSystem",ui_components)
            self.assertIn("InputSystemUIInputModule",ui_components)
            self.assertEqual(autowire_named_component_references(plan),[])


class FlexibleWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        backend=patch.dict(os.environ,{"STUDIO_UNITY_MODE":"batch","STUDIO_UNITY_MCP_ENABLED":"0"})
        backend.start();self.addCleanup(backend.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.studio=Supervisor(self.root)

    async def test_resume_uses_saved_files_without_regenerating_the_game(self):
        request,spec,plan=examples("racing")
        job=Job.create(request,2,root=self.root);self.studio.job=job
        job.data["tasks"]=[dict(role=r,name=n,dependencies=d,status="pending") for r,n,d in TASKS]
        project=job.directory/"UnityProject"
        save_implementation(job,project,spec,plan)
        file=project/"Assets/Studio/Generated/RaceCar.cs"
        file.write_text(file.read_text()+"\n// saved local repair")
        write_json(job.directory/"art_manifest.json",[])
        write_json(job.directory/"ui_plan.json", {"controls": spec.controls, "screens": [], "layout": [], "accessibility": []})
        job.data["resume_implementation"]=True
        write_json(project/"Artifacts/runtime_checks.json", {"checks": [{"id": "drive_to_finish", "passed": True}]})
        runner=SimpleNamespace(compile=AsyncMock(return_value={"status":"passed"}),
            assemble=AsyncMock(return_value={"success":True}),tests=AsyncMock(return_value={"status":"passed"}))
        with patch.object(self.studio.agents["art"],"create",AsyncMock()) as art,patch.object(
                self.studio.agents["developer"],"implement",AsyncMock()) as generate,patch.object(
                self.studio.agents["qa"],"review_implementation",AsyncMock(side_effect=RuntimeError("stop after resume inspection"))) as review,patch(
                "studio.generation.UnityRunner",return_value=runner):
            with self.assertRaisesRegex(RuntimeError,"stop after resume inspection"):
                await build_flexible(self.studio,SimpleNamespace(job=job),spec,project,None,False)
        art.assert_not_awaited();generate.assert_not_awaited()
        self.assertIn("saved local repair",review.await_args.args[2].scripts.scripts[0].content)

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
        write_json(project/"Assets/Resources/studio_dimension.json",{"dimension":spec.dimension})
        runner=UnityRunner(job,project)
        async def fake_run(*args,**kwargs):
            write_json(project/"Artifacts/runtime_checks.json",{"checks":[]})
            (project/"Artifacts/PlayMode.xml").write_text('<test-run result="Passed"><test-case fullname="PromptRuntimeTests.ExecutesEveryRequirementLinkedGameplayProbe" result="Passed"/></test-run>')
        with patch.object(runner,"run",fake_run),self.assertRaisesRegex(UnityFailure,"missing or failed"):
            await runner.tests("PlayMode")

    async def test_reverification_replaces_stale_legacy_dimension_limitations(self):
        request,spec,plan=examples("racing")
        job=Job.create(request,2,root=self.root);self.studio.job=job
        project=job.directory/"UnityProject"
        save_implementation(job,project,spec,plan)
        write_json(job.directory/"art_manifest.json",[])
        write_json(job.directory/"validation_report.json",{
            "limitations":["MVP supports 2D platformers only."],"old_report":True})
        async def tests(mode):
            if mode=="PlayMode":
                write_json(project/"Artifacts/runtime_checks.json",{
                    "checks":[dict(id="drive_to_finish",passed=True)]})
            return {"status":"passed"}
        runner=SimpleNamespace(compile=AsyncMock(return_value={"status":"passed"}),
            assemble=AsyncMock(return_value={"success":True}),tests=tests)
        from studio.verification import verify_flexible
        with patch("studio.verification.UnityRunner",return_value=runner),patch(
                "studio.verification.package",return_value=[Path("verified.zip")]):
            report=await verify_flexible(self.studio,spec,project,None,False)
        self.assertEqual(report["dimension"],"3D")
        self.assertNotIn("MVP supports 2D platformers only.",report["limitations"])

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
        plan_ui.assert_awaited_once()
        expected_ui=ui.model_dump()
        self.assertEqual(implement.await_args.args[2],expected_ui)
        self.assertEqual(json.loads((job.directory/"ui_plan.json").read_text()),expected_ui)
        self.assertEqual(job.data["status"],"partial")
        report=json.loads((job.directory/"validation_report.json").read_text())
        self.assertEqual(report["requirements"][0]["verification_result"],"manual_review_required")
        self.assertEqual(report["dimension"],spec.dimension)

    async def test_actionable_qa_concerns_trigger_bounded_repair_and_fresh_review(self):
        request,spec,plan=examples("racing")
        job=Job.create(request,2,root=self.root);self.studio.job=job
        job.data["tasks"]=[dict(role=r,name=n,dependencies=d,status="pending") for r,n,d in TASKS]
        project=job.directory/"UnityProject";service=SimpleNamespace(job=job)
        repaired=plan.model_copy(deep=True)
        repaired.scripts.scripts[0].content += "\n// QA repair"
        ui=GameUIPlan(controls=spec.controls)
        from studio.flexible import PlanReview
        review=AsyncMock(side_effect=[PlanReview(concerns=["A scene object is missing its intended collision shape."]),
                                      PlanReview()])
        async def art(*args):
            write_json(job.directory/"art_manifest.json",[])
            return {}
        async def tests(mode):
            if mode=="PlayMode":
                write_json(project/"Artifacts/runtime_checks.json",{"checks":[dict(id="drive_to_finish",passed=True)]})
            return {"status":"passed"}
        runner=SimpleNamespace(compile=AsyncMock(return_value={"status":"passed"}),
            assemble=AsyncMock(return_value={"success":True}),tests=tests)
        with patch.object(self.studio.agents["designer"],"plan_ui",AsyncMock(return_value=ui)),patch.object(
                self.studio.agents["art"],"create",art),patch.object(
                self.studio.agents["developer"],"implement",AsyncMock(return_value=plan)),patch.object(
                self.studio.agents["qa"],"review_implementation",review),patch.object(
                self.studio.agents["qa"],"repair_implementation",AsyncMock(return_value=repaired)) as repair,patch(
                "studio.generation.UnityRunner",return_value=runner),patch(
                "studio.generation.package",return_value=[Path("completed.zip")]):
            await build_flexible(self.studio,service,spec,project,None,False)
        repair.assert_awaited_once()
        self.assertEqual(review.await_count,2)
        self.assertEqual(job.data["repairs"],1)
        report=json.loads((job.directory/"validation_report.json").read_text())
        self.assertEqual(report["qa_review"]["concerns"],[])
        self.assertNotIn("A scene object is missing its intended collision shape.",report["limitations"])
        self.assertEqual(report["model_quality"]["status"],"insufficient_evidence")

    async def test_failed_runtime_probe_is_sent_to_qa_and_all_unity_stages_rerun(self):
        request,spec,plan=examples("racing")
        job=Job.create(request,2,root=self.root);self.studio.job=job
        job.data["tasks"]=[dict(role=r,name=n,dependencies=d,status="pending") for r,n,d in TASKS]
        project=job.directory/"UnityProject";service=SimpleNamespace(job=job)
        original_probes=[probe.model_dump() for probe in plan.probes]
        repaired=plan.model_copy(deep=True)
        repaired.scripts.scripts[0].content += "\n// repaired after Unity probe failure"
        ui=GameUIPlan(controls=spec.controls)
        from studio.flexible import PlanReview

        async def art(*args):
            write_json(job.directory/"art_manifest.json",[])
            return {}

        playmode_calls=0
        async def tests(mode):
            nonlocal playmode_calls
            if mode=="PlayMode":
                playmode_calls += 1
                passed=playmode_calls > 1
                evidence={"checks":[dict(id="drive_to_finish",passed=passed,
                    error="" if passed else "Vehicle did not reach the finish.")]}
                write_json(project/"Artifacts/runtime_checks.json",evidence)
                if not passed:
                    raise UnityFailure("PlayMode","Requirement-linked runtime checks are missing or failed","qa")
            return {"status":"passed"}

        runner=SimpleNamespace(compile=AsyncMock(return_value={"status":"passed"}),
            assemble=AsyncMock(return_value={"success":True}),tests=AsyncMock(side_effect=tests))
        with patch.object(self.studio.agents["designer"],"plan_ui",AsyncMock(return_value=ui)),patch.object(
                self.studio.agents["art"],"create",art),patch.object(
                self.studio.agents["developer"],"implement",AsyncMock(return_value=plan)),patch.object(
                self.studio.agents["qa"],"review_implementation",AsyncMock(return_value=PlanReview())),patch.object(
                self.studio.agents["qa"],"repair_implementation",AsyncMock(return_value=repaired)) as repair,patch(
                "studio.generation.UnityRunner",return_value=runner),patch(
                "studio.generation.package",return_value=[Path("completed.zip")]):
            await build_flexible(self.studio,service,spec,project,None,False)
        self.assertEqual(runner.compile.await_count,2)
        self.assertEqual(runner.assemble.await_count,2)
        self.assertEqual(runner.tests.await_count,4)
        self.assertEqual(playmode_calls,2)
        repair.assert_awaited_once()
        diagnostics=json.loads(repair.await_args.args[3])
        self.assertEqual(diagnostics["failed_probe_ids"],["drive_to_finish"])
        self.assertIn("Vehicle did not reach the finish.",json.dumps(diagnostics))
        self.assertEqual([probe.model_dump() for probe in plan.probes],original_probes)
        report=json.loads((job.directory/"validation_report.json").read_text())
        self.assertEqual(report["requirements"][0]["verification_result"],"passed")


if __name__=="__main__":unittest.main()
