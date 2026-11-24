"""Regressions for the implementation/transparent-art failures in saved jobs."""
import asyncio
import base64
import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from pydantic import ValidationError

from game_examples import examples
from studio.agents import UnityDeveloper, contract_diagnostics
from studio.api import ModelService, ServiceRejected
from studio.assets import ArtDesigner, asset_summary
from studio.failures import failure_report
from studio.flexible import (FlexibleAsset, GameCodeBundle, Implementation, apply_implementation_edits,
                             normalize_implementation_response, preserves_existing_probes, Requirement,
                             validate_implementation)
from studio.generation import with_progress
from studio.registry import Registry
from studio.schemas import CodeBundle
from studio.state import Job


class ContractRecoveryTests(unittest.TestCase):
    def test_full_game_can_use_more_than_eight_distinct_components(self):
        scripts = [{"filename": f"Component{i}.cs",
                    "content": f"using UnityEngine; public class Component{i} : MonoBehaviour {{}}"}
                   for i in range(10)]
        self.assertEqual(len(GameCodeBundle(scripts=scripts).scripts), 10)
        with self.assertRaises(ValidationError):
            CodeBundle(scripts=scripts)
        with self.assertRaises(ValidationError):
            GameCodeBundle(scripts=scripts + scripts[:1])

    def test_lossless_envelopes_and_typed_property_maps_preserve_contract(self):
        _, spec, plan = examples("racing")
        value = plan.model_dump()
        for key in ("probes", "implemented_requirement_ids", "limitations"):
            value["scenes"][key] = value.pop(key)
        camera = next(c for obj in value["scenes"]["scenes"][0]["objects"]
                      for c in obj["components"] if c["type"].endswith("Camera"))
        camera["properties"] = {p["name"]: {k: v for k, v in p.items() if k != "name"}
                                for p in camera["properties"]}
        original = deepcopy(value)
        result = Implementation.model_validate(normalize_implementation_response(value, spec))
        self.assertEqual(result, plan)
        self.assertEqual(value, original)
        value["probes"] = []
        with self.assertRaisesRegex(ValueError, "Conflicting implementation field: probes"):
            normalize_implementation_response(value, spec)

    def test_script_and_scene_envelopes_are_normalized_losslessly(self):
        _, spec, plan = examples("racing")
        value = plan.model_dump()
        script_bundle = value["scripts"]
        for key in ("probes", "implemented_requirement_ids", "limitations"):
            script_bundle[key] = value.pop(key)
        script_bundle["scripts"][0]["requirement_ids"] = ["R1"]
        scene_bundle = value["scenes"]
        scene = scene_bundle["scenes"][0]
        for key in ("version", "title", "dimension", "orientation", "materials"):
            scene[key] = scene_bundle.pop(key)
        original = deepcopy(value)

        normalized = normalize_implementation_response(value, spec)
        self.assertEqual(Implementation.model_validate(normalized), plan)
        self.assertEqual(value, original)

    def test_pipeline_requirements_are_not_gameplay_probe_requirements(self):
        requirement = Requirement(
            id="R1",
            original_instruction="Produce an Android APK and verify it builds and installs successfully.",
            implementation="Build and verify the requested Android APK.",
            owner="qa",
            verification_method="runtime",
        )
        self.assertEqual(requirement.verification_method, "pipeline")

    def test_raw_properties_and_unknown_fields_still_require_correction(self):
        _, spec, plan = examples("racing")
        value = plan.model_dump()
        value["scenes"]["scenes"][0]["objects"][0]["components"][0]["properties"] = {"someField": 1}
        with self.assertRaises(ValidationError):
            Implementation.model_validate(normalize_implementation_response(value, spec))

    def test_contract_edits_are_data_only_and_existing_probes_are_immutable(self):
        _, _, plan = examples("racing")
        original = plan.model_dump()
        edited = apply_implementation_edits(original, {"edits": [{"op": "replace",
            "path": "/scenes/scenes/0/objects/0/name", "value": "Updated Display Name"}]})
        self.assertEqual(edited["scenes"]["scenes"][0]["objects"][0]["name"], "Updated Display Name")
        self.assertNotEqual(original, edited)
        self.assertEqual(edited["probes"], original["probes"])
        added_probe = deepcopy(original["probes"][0])
        added_probe["id"] = "additional_probe"
        appended = apply_implementation_edits(original, {"edits": [{"op": "add",
            "path": "/probes/-", "value": added_probe}]})
        self.assertEqual(appended["probes"][:-1], original["probes"])
        self.assertEqual(appended["probes"][-1], added_probe)
        self.assertTrue(preserves_existing_probes(original, appended))
        invalid_edits = [
            {"op": "remove", "path": "/probes/0"},
            {"op": "replace", "path": "/probes/0/requirement_ids/0", "value": "R99"},
            {"op": "remove", "path": "/probes/-"},
            {"op": "remove", "path": "/scripts/scripts/-1"},
            {"op": "remove", "path": "/scenes/missing/path"},
        ]
        for edit in invalid_edits:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                apply_implementation_edits(original, {"edits": [edit]})


    def test_diagnostics_include_missing_scenes_before_reference_validation_finishes(self):
        _, spec, plan = examples("racing")
        value = plan.model_dump()
        value["scenes"]["scenes"] = []
        value["probes"] = []
        text = contract_diagnostics(ValueError("A reference failed"), value, spec)
        self.assertIn("Scene names/order must be", text)
        self.assertIn("Missing behavioral probe coverage", text)

    def test_repeated_display_names_are_valid_but_probe_aliases_cannot_be_ambiguous(self):
        _, spec, plan = examples("racing")
        value = plan.model_dump()
        objects = value["scenes"]["scenes"][0]["objects"]
        objects[0]["name"] = objects[1]["name"] = "Repeated Label"
        parsed = Implementation.model_validate(value)
        validate_implementation(spec, parsed)
        assertion = next(a for p in parsed.probes for a in p.assertions if a.object_id)
        assertion.object_id = "Repeated Label"
        with self.assertRaisesRegex(ValueError, "Unknown probe object"):
            validate_implementation(spec, parsed)


class GenerationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = Job.create("test", 2, root=self.root / "jobs")

    async def test_developer_contract_failure_switches_models_and_reports_actual_repairs(self):
        request, spec, plan = examples("racing")
        self.job.data["request"] = request
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "STUDIO_UNITY_MCP_ENABLED": "0"}):
            registry = Registry()
            registry.candidates = Mock(return_value=["first", "second"])
            registry.models = {mid: {"pricing": {"prompt": "0", "completion": "0"}}
                               for mid in ("first", "second")}
            registry.record_outcome = Mock()
            service = ModelService(registry, self.job)
            service.unity_context = AsyncMock(return_value={})
            invalid = plan.model_dump()
            invalid["scenes"]["scenes"][0]["objects"][0]["components"][0]["wrong"] = True
            service.post = AsyncMock(side_effect=[
                {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}
                for value in (invalid, plan.model_dump())])
            result = await UnityDeveloper().implement(service, spec, {}, {})
        self.assertEqual(result, plan)
        self.assertEqual([c.args[1]["model"] for c in service.post.await_args_list], ["first", "second"])
        self.assertEqual(self.job.data["implementation_repairs"], 1)
        calls = registry.record_outcome.call_args_list
        self.assertTrue(any(c.args == ("first", "developer", False) and c.kwargs == {"kind": "contract"} for c in calls))
        self.assertTrue(any(c.args == ("second", "developer", True) and c.kwargs == {"kind": "contract"} for c in calls))
        failure_report(ValueError("Implementation validation failed after 3 attempts: invalid data"), self.job)
        report = self.job.data["failure_report"]
        self.assertEqual(report["type"], "AI agent output contract")
        self.assertEqual(report["repairs_attempted"], 1)
        self.assertIn("before Unity scene assembly", report["needed"])
        original_response = (self.job.directory / "implementation_response_1.json").read_bytes()
        service.post.side_effect = [{"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(plan.model_dump())}}]}]
        await UnityDeveloper().implement(service, spec, {}, {})
        self.assertEqual((self.job.directory / "implementation_response_1.json").read_bytes(), original_response)
        self.assertTrue((self.job.directory / "implementation_response_3.json").is_file())

    async def test_progress_reports_waiting_and_cancels_the_original_operation(self):
        supervisor = SimpleNamespace(notify=AsyncMock())
        started, stopped = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        task = asyncio.create_task(with_progress(supervisor, None, operation(), "Still generating", interval=.01))
        await started.wait()
        for _ in range(100):
            if supervisor.notify.await_count:
                break
            await asyncio.sleep(.005)
        self.assertGreater(supervisor.notify.await_count, 0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stopped.is_set())

    async def test_small_schema_defect_is_repaired_without_regenerating_all_scripts(self):
        request, spec, plan = examples("racing")
        self.job.data["request"] = request
        invalid = plan.model_dump()
        invalid["scenes"]["scenes"][0]["objects"][0]["unexpected"] = True
        service = SimpleNamespace(job=self.job, json=AsyncMock(side_effect=[invalid,
            {"edits": [{"op": "remove", "path": "/scenes/scenes/0/objects/0/unexpected"}]}]))
        repaired = await UnityDeveloper().implement(service, spec, {}, {})
        self.assertEqual(repaired, plan)
        self.assertEqual(service.json.await_args.args[3], 2400)
        self.assertEqual(repaired.probes, plan.probes)
        self.assertTrue((self.job.directory / "implementation_edits_2.json").is_file())

    async def test_semantic_scene_error_uses_focused_edits_and_preserves_probes(self):
        request, spec, plan = examples("racing")
        self.job.data["request"] = request
        invalid = plan.model_dump()
        car = next(obj for obj in invalid["scenes"]["scenes"][0]["objects"] if obj["id"] == "car")
        car["components"].extend([
            {"type": "UnityEngine.UI.Image", "properties": []},
            {"type": "UnityEngine.UI.Text", "properties": []},
        ])
        label = {
            "id": "carLabel",
            "name": "Car Label",
            "parent": "car",
            "position": [0, 0, 0],
            "components": [
                {"type": "UnityEngine.RectTransform", "properties": []},
                {"type": "UnityEngine.UI.Text", "properties": []},
            ],
        }
        edits = {"edits": [
            {"op": "remove", "path": "/scenes/scenes/0/objects/3/components/3"},
            {"op": "add", "path": "/scenes/scenes/0/objects/-", "value": label},
        ]}
        service = SimpleNamespace(job=self.job, json=AsyncMock(side_effect=[invalid, edits]))

        repaired = await UnityDeveloper().implement(service, spec, {}, {})

        self.assertEqual(service.json.await_count, 2)
        self.assertEqual(service.json.await_args_list[1].args[3], 2400)
        self.assertEqual(repaired.probes, plan.probes)
        self.assertIn("carLabel", {obj.id for obj in repaired.scenes.scenes[0].objects})

    async def test_rejected_transparency_is_not_retried_for_every_sprite(self):
        registry = Mock()
        registry.candidates.return_value = ["seed"]
        registry.image_endpoint.return_value = ({"provider_tag": "seed", "supported_parameters": {
            "background": {}, "output_format": {}}}, .02)
        image = Image.new("RGBA", (64, 64), "blue")
        stream = io.BytesIO()
        image.save(stream, "PNG")
        service = SimpleNamespace(job=self.job, registry=registry, post=AsyncMock(side_effect=[
            ServiceRejected(400, "transparent images require a reference image"),
            {"data": [{"b64_json": base64.b64encode(stream.getvalue()).decode()}]}]))
        assets = [SimpleNamespace(id=name, kind="sprite" if transparent else "texture", transparent=transparent,
                    width=64, height=64, pixels_per_unit=128, requirement_ids=["R1"],
                    prompt="a game sprite asset", model_dump=lambda: {})
                  for name, transparent in (("player", True), ("star", True), ("backdrop", False))]
        with patch("studio.assets.CACHE", self.root / "cache"):
            await ArtDesigner().create(service, SimpleNamespace(required_assets=assets,
                art_style="colorful cartoon", palette=[]), self.root / "project", strict=True)
        self.assertEqual(service.post.await_count, 2)
        manifest = json.loads((self.job.directory / "art_manifest.json").read_text())
        self.assertIn("require a reference image", manifest[1]["reason"])
        summary = asset_summary([dict(entry, status="ready" if entry.get("path") else "unavailable") for entry in manifest])
        self.assertIn("1 assets ready, 2 unavailable", summary)
        self.assertIn("Unavailable: star:", summary)

    async def test_placeholder_images_require_explicit_permission_and_keep_failure_reason(self):
        _, spec, _ = examples("puzzle")
        spec.required_assets = [FlexibleAsset(id="piece", kind="sprite", workflow="image",
            prompt="a colorful puzzle piece", transparent=True, requirement_ids=["R1"], width=64, height=64)]
        registry = Mock()
        registry.candidates.return_value = []
        service = SimpleNamespace(job=self.job, registry=registry, post=AsyncMock())
        for index, (request, allowed) in enumerate([
                ("Use custom artwork. No placeholders.", False),
                ("Generate working placeholder assets when custom artwork is unavailable.", True)]):
            self.job.data["request"] = request
            with patch("studio.assets.CACHE", self.root / "cache"):
                art = await ArtDesigner().create(service, spec, self.root / f"project{index}")
            self.assertEqual(art["piece"]["status"], "ready" if allowed else "unavailable")
            if allowed:
                entry = art["piece"]
                self.assertTrue(entry["is_placeholder"])
                self.assertIn("No compatible image endpoint", entry["reason"])
                with Image.open(self.root / f"project{index}" / entry["path"]) as image:
                    self.assertEqual(image.size, (64, 64))
                    self.assertEqual(image.getextrema()[3][0], 0)
        service.post.assert_not_awaited()


class ImageRoutingTests(unittest.TestCase):
    def test_configured_task_preference_beats_unused_extra_context(self):
        from studio.model_selection import rank_key
        coder = {"context_length": 64000, "supported_parameters": ["reasoning"]}
        general = {"context_length": 1000000, "supported_parameters": ["reasoning"]}
        self.assertLess(rank_key(coder, "developer", {}, .5, 0),
                        rank_key(general, "developer", {}, .01, 2))
        measured = {"developer": [{"kind": "unity", "passed": True}] * 3}
        self.assertLess(rank_key(general, "developer", measured, .01, 2),
                        rank_key(coder, "developer", {}, .5, 0))

    def test_transparency_filter_precedes_cheapest_candidate_limit(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "STUDIO_ALLOW_PAID_MODELS": "1",
                                     "STUDIO_FREE_ONLY": "0", "STUDIO_MODEL_SELECTION": "dynamic",
                                     "STUDIO_AUTO_MODEL_FALLBACK": "1"}):
            registry = Registry()
            def endpoint(cost, supported):
                return {"endpoints": [{"provider_tag": "test", "supported_parameters": supported,
                        "pricing": [{"billable": "output_image", "unit": "image", "cost_usd": cost}]}]}
            registry.images = {f"opaque{i}": endpoint(.001 + i * .001, {}) for i in range(5)}
            registry.images["transparent"] = endpoint(.02, {"background": {"values": ["transparent", "opaque"]}})
            self.assertNotIn("transparent", list(registry.candidates("art")))
            self.assertEqual(list(registry.candidates("art", transparent=True)), ["transparent"])


if __name__ == "__main__":
    unittest.main()
