import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.verify_live import run


class LiveAuditFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_assetless_fixture_writes_the_resource_loaded_by_scene_builder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            studio = SimpleNamespace(root=root, job=None)
            project = root / "UnityProject"
            (project / "Assets/Resources").mkdir(parents=True)
            async def verify(_studio, _notify, android):
                self.assertFalse(android)
                studio.job.data["status"] = "completed"
                return {}

            with patch("tools.verify_live.Supervisor", return_value=studio), \
                 patch("tools.verify_live.JOBS", root), patch("tools.verify_live.ROOT", root), \
                 patch("studio.unity.bootstrap", return_value=project), \
                 patch("studio.generation.save_implementation"), \
                 patch("studio.verification.verify", new=AsyncMock(side_effect=verify)):
                status = await run(SimpleNamespace(unity_fixture="racing", resume_fixture=None,
                                                   preflight=False, prompt=""))

            self.assertEqual(status, 0)
            resource = json.loads((project / "Assets/Resources/studio_art.json").read_text(encoding="utf-8"))
            self.assertEqual(resource, {"assets": []})


if __name__ == "__main__":
    unittest.main()
