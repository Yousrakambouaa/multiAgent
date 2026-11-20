import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from studio.state import Job, write_json
from studio.unity import UnityFailure, UnityRunner, unity_failure_owner, compiler_errors, licensing_status


class UnityFailureClassificationTests(unittest.TestCase):
    def test_generated_compiler_errors_take_priority_over_recovered_license_warnings(self):
        text = ("[Licensing::Module] LicensingClient has failed validation; ignoring\n"
                "[Licensing::Module] Error: Access token is unavailable; failed to update\n"
                "[Licensing::Client] Successfully updated license, isAsync: True\n"
                "Assets\\Studio\\Generated\\MobileControls.cs(37,20): error CS1061: Button has no onPointerUp\n")
        self.assertEqual(unity_failure_owner("compile",text),"developer")
        self.assertEqual(licensing_status(text)["status"],"available")
        self.assertEqual(len(compiler_errors(text + text)),1)
        self.assertEqual(compiler_errors(text)[0]["code"],"CS1061")
        self.assertEqual(compiler_errors(text)[0]["file"],"Assets/Studio/Generated/MobileControls.cs")
        wrapped = ("Assets/Studio/Generated/MobileControls.cs(1,153): "
                   "Assets/Studio/Generated/MobileControls.cs(1,153): error CS1061: "
                   "'Button' does not contain a definition for 'onPointerUp'")
        self.assertEqual(compiler_errors(wrapped), [{
            "file": "Assets/Studio/Generated/MobileControls.cs", "line": 1, "column": 153,
            "code": "CS1061", "message": "'Button' does not contain a definition for 'onPointerUp'"}])
        recovered = "[Licensing::Module] LicensingClient has failed validation; ignoring\nSuccessfully updated license"
        self.assertEqual(unity_failure_owner("PlayMode",recovered+"\nGameplay assertion failed"),"qa")

    def test_unity_license_and_host_permission_failures_are_infrastructure(self):
        logs = ["Failed to acquire global mutex Unity-LicenseClient-new.",
                "Licensing client has failed validation",
                "System.Management.ManagementException: Access denied"]
        for log in logs:
            with self.subTest(log=log):
                self.assertEqual(unity_failure_owner("compile", log), "infrastructure")
        self.assertEqual(unity_failure_owner("compile", "Assets/Studio/Generated/Bad.cs(1): error CS1002"), "developer")


class UnityFailureTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.temp = Path(__file__).resolve().parent / (".unity-failure-test-" + uuid4().hex)
        self.temp.mkdir()
        self.addCleanup(shutil.rmtree, self.temp)
        self.job = Job.create("test", 1, root=self.temp)
        self.project = self.job.directory / "UnityProject"
        self.runner = UnityRunner(self.job, self.project)
        self.paths = [f"Assets/Scenes/{name}.unity" for name in ("Main", "Game", "End")]
        for path in self.paths:
            target = self.project / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text("scene")

    async def test_passing_report_does_not_hide_failed_unity_exit(self):
        async def crash(*args, **kwargs):
            write_json(self.project / "Artifacts/assembly.json", {"success": True, "scenes": 3, "scene_paths": self.paths})
            raise UnityFailure("assembly", "Unity crashed after writing the report")
        with patch.object(self.runner, "run", crash), self.assertRaisesRegex(UnityFailure, "crashed"):
            await self.runner.assemble()

    async def test_invalid_optional_mcp_config_preserves_successful_assembly(self):
        async def assemble(*args, **kwargs):
            write_json(self.project / "Artifacts/assembly.json", {"success": True, "scenes": 3, "scene_paths": self.paths})
        with patch.object(self.runner, "run", assemble), patch(
                "studio.mcp_client.UnityMCPClient.from_environment", side_effect=ValueError("bad config")):
            self.assertTrue((await self.runner.assemble())["success"])

    async def test_stale_preview_removed_and_passing_xml_cannot_hide_crash(self):
        artifacts = self.project / "Artifacts"; artifacts.mkdir()
        preview = artifacts / "preview.png"; preview.write_bytes(b"old preview")
        async def crash(*args, **kwargs):
            (artifacts / "PlayMode.xml").write_text('<test-run result="Passed"/>')
            raise UnityFailure("PlayMode", "runtime crash")
        with patch.object(self.runner, "run", crash), self.assertRaisesRegex(UnityFailure, "runtime crash"):
            await self.runner.tests("PlayMode")
        self.assertFalse(preview.exists())
