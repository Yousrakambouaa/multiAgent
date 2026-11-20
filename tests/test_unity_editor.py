import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from studio.state import Job
from studio.unity import UnityFailure, UnityRunner, ensure_live_editor, uses_live_editor


class LiveEditorStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = Path(__file__).resolve().parent / (".unity-editor-test-" + uuid4().hex)
        self.temp.mkdir()
        self.addCleanup(shutil.rmtree, self.temp)
        self.job = Job.create("visible Unity test", 1, root=self.temp)
        self.project = self.job.directory / "UnityProject"
        self.project.mkdir()
        self.editor = self.temp / "Unity" / "Editor.exe"
        self.editor.parent.mkdir()
        self.editor.write_text("fake editor executable", encoding="utf-8")
        self.client = SimpleNamespace(connect=AsyncMock(), verify_project=AsyncMock(return_value={
            "projectRoot": str(self.project), "unityVersion": "6000.3.24f1"
        }), close=AsyncMock(), timeout=20)

    async def test_closed_project_launches_visible_editor_and_waits_for_matching_mcp(self):
        process = SimpleNamespace(pid=4321, poll=lambda: None)
        with patch.dict(os.environ, {
                "STUDIO_UNITY_MCP_ENABLED": "1", "STUDIO_UNITY_STARTUP_TIMEOUT": "30",
                "OPENROUTER_API_KEY": "must-not-reach-editor", "TELEGRAM_BOT_TOKEN": "must-not-reach-editor"}), \
             patch("studio.unity.UNITY", self.editor), \
             patch("studio.unity.project_is_open", side_effect=[False, True]), \
             patch("studio.unity.subprocess.Popen", return_value=process) as launch, \
             patch("studio.mcp_client.UnityMCPClient.from_environment", return_value=self.client):
            await ensure_live_editor(self.job, self.project)

        command = launch.call_args.args[0]
        self.assertIn("-projectPath", command)
        self.assertEqual(command[command.index("-projectPath") + 1], str(self.project.resolve()))
        self.assertNotIn("-batchmode", command)
        self.assertNotIn("-quit", command)
        self.assertNotIn("OPENROUTER_API_KEY", launch.call_args.kwargs["env"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", launch.call_args.kwargs["env"])
        self.client.connect.assert_awaited_once()
        self.client.verify_project.assert_awaited_once_with(self.project.resolve())
        self.client.close.assert_awaited_once()

    async def test_existing_project_editor_is_reused_without_another_process(self):
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "1", "STUDIO_UNITY_STARTUP_TIMEOUT": "30"}), \
             patch("studio.unity.project_is_open", return_value=True), \
             patch("studio.unity.subprocess.Popen") as launch, \
             patch("studio.mcp_client.UnityMCPClient.from_environment", return_value=self.client):
            await ensure_live_editor(self.job, self.project)

        launch.assert_not_called()
        self.client.verify_project.assert_awaited_once_with(self.project.resolve())

    async def test_disabled_mcp_never_launches_unity_for_live_mode(self):
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "0"}, clear=False), \
             patch("studio.unity.subprocess.Popen") as launch:
            with self.assertRaisesRegex(UnityFailure, "requires Unity MCP"):
                await ensure_live_editor(self.job, self.project)
        launch.assert_not_called()

    async def test_auto_mode_opens_editor_for_closed_projects_when_mcp_is_enabled(self):
        runner = UnityRunner(self.job, self.project)
        with patch.dict(os.environ, {"STUDIO_UNITY_MODE": "auto", "STUDIO_UNITY_MCP_ENABLED": "1"}), \
             patch("studio.unity.project_is_open", return_value=False), \
             patch("studio.unity.ensure_live_editor", new=AsyncMock()) as ensure, \
             patch("studio.unity_live.run_live", new=AsyncMock(return_value="Application.AssetDatabase Initial Refresh End")) as live:
            result = await runner.compile()

        self.assertEqual(result["status"], "passed")
        ensure.assert_awaited_once_with(self.job, self.project, timeout=600)
        live.assert_awaited_once()

    def test_auto_uses_batch_only_when_mcp_is_opted_out_and_project_is_closed(self):
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "0"}):
            self.assertFalse(uses_live_editor("auto", opened=False))
            self.assertTrue(uses_live_editor("auto", opened=True))
            self.assertTrue(uses_live_editor("live", opened=False))

    async def test_auto_opt_out_uses_batch_only_while_project_is_closed(self):
        runner = UnityRunner(self.job, self.project)
        process = SimpleNamespace(returncode=0)

        async def launch(*command, **kwargs):
            args = list(command)
            log = Path(args[args.index("-logFile") + 1])
            log.write_text("Application.AssetDatabase Initial Refresh End", encoding="utf-8")
            return process

        with patch.dict(os.environ, {"STUDIO_UNITY_MODE": "auto", "STUDIO_UNITY_MCP_ENABLED": "0"}), \
             patch("studio.unity.project_is_open", return_value=False), \
             patch("studio.unity.UNITY", self.editor), \
             patch("studio.unity.ensure_live_editor", new=AsyncMock()) as ensure, \
             patch("studio.unity.asyncio.create_subprocess_exec", side_effect=launch) as batch:
            result = await runner.compile()

        self.assertEqual(result["status"], "passed")
        ensure.assert_not_awaited()
        command = batch.await_args.args
        self.assertIn("-batchmode", command)
        self.assertEqual(command[command.index("-projectPath") + 1], str(self.project))


if __name__ == "__main__":
    unittest.main()
