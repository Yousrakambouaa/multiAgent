import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from studio.state import Job, write_json
from studio.unity import UnityFailure, UnityRunner, unity_failure_owner


class UnityFailureClassificationTests(unittest.TestCase):
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
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.job = Job.create("test", 1, root=Path(self.temp.name))
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
