import asyncio
import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import studio.mcp_client as mcp


class FakeStream:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def readline(self):
        return await self.queue.get()


class FakeStderr:
    async def readline(self):
        return b""


class FakeMCPProcess:
    def __init__(self, root, log_path):
        self.root, self.log_path = str(root), log_path
        self.stdout, self.stderr = FakeStream(), FakeStderr()
        self.stdin = SimpleNamespace(write=self.write, drain=self.drain)
        self.returncode = None
        self.current_path = "Assets/Scenes/Main.unity"
        self.probe_exists = False
        self.calls = []

    def write(self, data):
        message = json.loads(data)
        method, ident = message.get("method"), message.get("id")
        if ident is None:
            return
        params = message.get("params", {})
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake-unity-mcp", "version": "test"}}
        elif method == "tools/list":
            names = ["manage_scene", "manage_gameobject", "manage_components", "manage_asset", "find_gameobjects", "read_console", "validate_script", "run_tests", "get_test_job", "execute_code"]
            result = {"tools": [{"name": name, "inputSchema": {"type": "object", "properties": {}}} for name in names]}
        elif method == "resources/read":
            result = {"contents": [{"mimeType": "application/json", "text": json.dumps({"data": {"projectRoot": self.root, "unityVersion": "6000.3.24f1"}})}]}
        elif method == "tools/call":
            name, args = params["name"], params["arguments"]
            self.calls.append({"name": name, "arguments": args})
            action, data = args.get("action"), {}
            if name == "manage_scene" and action == "get_active":
                data = {"name": "Main", "path": self.current_path, "isDirty": False}
            elif name == "manage_scene" and action == "get_loaded_scenes":
                data = {"scenes": [{"name": "Main", "path": self.current_path, "isDirty": False, "isActive": True}]}
            elif name == "manage_scene" and action == "create":
                self.current_path = "Assets/Studio/MCPProbe/StudioMCPProbeScene.unity"
                data = {"path": self.current_path}
            elif name == "manage_scene" and action == "load":
                self.current_path = args["path"]
                data = {"path": self.current_path}
            elif name == "manage_gameobject" and action == "create":
                self.probe_exists = args["name"] == "StudioMCPProbe"
                data = {"name": args["name"]}
            elif name == "manage_gameobject" and action == "delete":
                self.probe_exists = False
                data = {"deleted": True}
            elif name == "find_gameobjects":
                data = {"ids": [42] if self.probe_exists else []}
            elif name == "manage_asset" and action == "delete":
                data = {"deleted": True}
            else:
                data = {"ok": True}
            result = {"content": [{"type": "text", "text": json.dumps({"success": True, "data": data})}]}
        else:
            self.stdout.queue.put_nowait(json.dumps({"jsonrpc": "2.0", "id": ident,
                "error": {"code": -32601, "message": "unknown method"}}).encode() + b"\n")
            return
        self.stdout.queue.put_nowait(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}).encode() + b"\n")

    async def drain(self):
        return None

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class UnityMCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = Path(__file__).resolve().parent / (".mcp-test-" + uuid4().hex)
        self.root = self.temp / "jobs" / "test-job" / "UnityProject"
        (self.root / "Assets" / "Studio" / "Generated").mkdir(parents=True)
        original_scene = self.root / "Assets" / "Scenes" / "Main.unity"
        original_scene.parent.mkdir(parents=True)
        original_scene.write_text("fake scene", encoding="utf-8")
        self.log_path = self.temp / "calls.jsonl"
        self.jobs_patch = patch.object(mcp, "JOBS", self.root.parents[1])
        self.jobs_patch.start()
        self.process = FakeMCPProcess(self.root, self.log_path)
        self.process_patch = patch.object(mcp.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=self.process))
        self.process_patch.start()
        self.client = mcp.UnityMCPClient("fake-server", [], timeout=2)
        await self.client.connect()

    async def asyncTearDown(self):
        await self.client.close()
        self.process_patch.stop()
        self.jobs_patch.stop()
        shutil.rmtree(self.temp)

    async def test_connect_discovers_tools_and_exposes_role_limited_access(self):
        self.assertEqual(self.client.server_info["name"], "fake-unity-mcp")
        self.assertIn("execute_code", self.client.tools)
        self.assertNotIn("execute_code", mcp.AGENT_TOOLS["developer"])
        self.assertEqual(self.client.for_agent("art", self.root).available, ["manage_asset"])
        await self.client.verify_project(self.root)

    async def test_untrusted_paths_and_arbitrary_code_tools_are_blocked(self):
        with self.assertRaises(mcp.MCPError):
            await self.client.call_for_agent("developer", "execute_code", {"code": "bad"}, self.root)
        with self.assertRaises(mcp.MCPError):
            await self.client.call_for_agent("art", "manage_asset", {
                "action": "create", "path": "Assets/../../outside.png"
            }, self.root)
        with self.assertRaises(mcp.MCPError):
            await self.client.call_for_agent("art", "manage_asset", {
                "action": "import", "sourcePath": str(self.temp / "outside.png"),
                "destinationPath": "Assets/Studio/Generated/outside.png",
            }, self.root)
        self.assertEqual(self.process.calls, [])

    async def test_smoke_creates_inspects_restores_and_cleans_probe_scene(self):
        result = await self.client.smoke_test(self.root)
        self.assertEqual(result["scene_created"], mcp.PROBE_SCENE_PATH)
        self.assertEqual(result["inspected_object_ids"], [42])
        actions = [(call["name"], call["arguments"].get("action")) for call in self.process.calls]
        self.assertIn(("manage_scene", "create"), actions)
        self.assertIn(("manage_gameobject", "create"), actions)
        self.assertIn(("manage_components", "add"), actions)
        self.assertIn(("manage_asset", "create"), actions)
        self.assertIn(("manage_scene", "get_hierarchy"), actions)
        self.assertIn(("find_gameobjects", None), actions)
        self.assertIn(("read_console", "get"), actions)
        self.assertIn(("manage_gameobject", "delete"), actions)
        self.assertIn(("manage_scene", "load"), actions)
        self.assertIn(("manage_asset", "delete"), actions)


class MCPConfigurationTests(unittest.TestCase):
    def test_disabled_by_default_and_timeout_is_bounded(self):
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "0"}, clear=False):
            self.assertIsNone(mcp.UnityMCPClient.from_environment())
        with self.assertRaises(ValueError):
            mcp.UnityMCPClient("uvx", [], timeout=0)


if __name__ == "__main__":
    unittest.main()
