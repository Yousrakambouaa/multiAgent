import json
import os
import shutil
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from studio.failures import failure_report, failure_type
from studio.state import Job
from studio.unity_live import run_live
from studio.unity import UnityFailure, UnityRunner, unity_failure_owner


class AndroidAndFailureTests(unittest.IsolatedAsyncioTestCase):
    def make_root(self):
        root = Path(__file__).parent / ".test-tmp" / ("android-live-" + uuid4().hex)
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    async def test_live_android_build_runs_through_unity_mcp_and_verifies_apk(self):
        root = self.make_root()
        job = Job.create("Build APK", 2, root=root)
        project = job.directory / "UnityProject"
        (project / "Artifacts").mkdir(parents=True)
        apk = project / "Artifacts/prototype.apk"
        evidence = project / "Artifacts/android.json"
        facade = SimpleNamespace()

        async def call(tool, args):
            self.assertEqual(tool, "execute_menu_item")
            self.assertEqual(args["menu_path"], "Studio/Build Android APK")
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", "verified fixture")
                archive.writestr("assets/padding", "x" * 12000)
            evidence.write_text(json.dumps({"success": True}), encoding="utf-8")
            return {"ok": True}

        facade.call = AsyncMock(side_effect=call)
        client = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), verify_project=AsyncMock(return_value={}),
            for_agent=lambda role, path: facade,
            read_resource=AsyncMock(return_value={"editor": {"play_mode": {"is_playing": False}}}))
        runner = SimpleNamespace(job=job, project=project)
        with patch("studio.mcp_client.UnityMCPClient.from_environment", return_value=client):
            result = await run_live(runner, "android", [], timeout=10)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["device_test"], "unverified")
        facade.call.assert_awaited_once()

    async def test_live_android_failure_is_not_reported_as_completed_artifact(self):
        root = self.make_root()
        job = Job.create("Build APK", 2, root=root)
        project = job.directory / "UnityProject"
        (project / "Artifacts").mkdir(parents=True)
        facade = SimpleNamespace(call=AsyncMock(return_value={"ok": True}))
        client = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), verify_project=AsyncMock(return_value={}),
            for_agent=lambda role, path: facade,
            read_resource=AsyncMock(return_value={"editor": {"play_mode": {"is_playing": False}}}))
        runner = SimpleNamespace(job=job, project=project)
        with patch("studio.mcp_client.UnityMCPClient.from_environment", return_value=client):
            with self.assertRaises(UnityFailure) as raised:
                await run_live(runner, "android", [], timeout=0)
        self.assertEqual(raised.exception.stage, "android")

    def test_failure_classification_separates_provider_from_unity(self):
        self.assertEqual(failure_type(RuntimeError("HTTP 429 rate limit")), "temporary provider or service failure")
        self.assertEqual(failure_type(UnityFailure("PlayMode", "probe_ground_collision failed")),
                         "Unity scene or gameplay verification")
        self.assertEqual(unity_failure_owner("PlayMode", "probe_ground_collision: IsGrounded was false"), "scene")

    def test_user_report_is_short_and_full_diagnostic_stays_local(self):
        job = Job.create("Game", 2, root=self.make_root())
        error = UnityFailure("PlayMode", "NUnit stack trace with absolute file path", "qa")
        message = failure_report(error, job)
        self.assertIn("Unity scene or gameplay verification", message)
        self.assertNotIn("NUnit stack trace", message)
        self.assertEqual(job.data["failure_report"]["stage"], "PlayMode")

    def test_blank_inherited_provider_keys_do_not_mask_project_env(self):
        root = self.make_root()
        (root / ".env").write_text("AI_PROVIDER=dahl\nDAHL_API_KEY=fixture-dahl-key\n", encoding="utf-8")
        import studio.config as config
        with patch.dict(os.environ, {"AI_PROVIDER": "", "OPENROUTER_API_KEY": "", "DAHL_API_KEY": ""}):
            config.load_project_env(root)
            self.assertEqual(os.environ["AI_PROVIDER"], "dahl")
            self.assertEqual(os.environ["DAHL_API_KEY"], "fixture-dahl-key")
            self.assertNotIn("OPENROUTER_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
