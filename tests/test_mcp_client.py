import asyncio
import json
import os
import shutil
import sys
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
        self.asset_exists = False
        self.created_asset_paths = []
        self.deleted_asset_paths = []
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
            names = ["manage_scene", "manage_gameobject", "manage_components", "manage_asset", "manage_camera", "find_gameobjects", "read_console", "validate_script", "run_tests", "get_test_job", "execute_code", "refresh_unity", "execute_menu_item"]
            result = {"tools": [{"name": name, "inputSchema": {"type": "object", "properties": {}}} for name in names]}
        elif method == "resources/read":
            uri = params.get("uri", "")
            if uri.startswith("mcpforunity://scene/gameobject/"):
                value = {"data": {"name": "StudioMCPProbe", "componentTypes": ["Transform", "MeshRenderer", "MeshFilter", "Rigidbody"]}}
            else:
                value = {"data": {"projectRoot": self.root, "unityVersion": "6000.3.24f1"}}
            result = {"contents": [{"mimeType": "application/json", "text": json.dumps(value)}]}
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
            elif name == "manage_asset" and action == "create":
                self.asset_exists = True
                self.created_asset_paths.append(args["path"])
                data = {"created": True}
            elif name == "manage_asset" and action == "delete":
                self.asset_exists = False
                self.deleted_asset_paths.append(args["path"])
                data = {"deleted": True}
            elif name == "find_gameobjects":
                key = getattr(self, "find_ids_key", "ids")
                data = {key: [42] if self.probe_exists else []}
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
    def test_empty_editor_registry_during_startup_is_retryable(self):
        self.assertTrue(mcp.transient_editor_error(
            mcp.MCPError("No Unity instances are currently connected. Start Unity and press 'Start Session'.")))

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

    async def test_instance_assets_path_is_normalized_to_the_matching_project_root(self):
        self.client.tools["set_active_instance"] = {"inputSchema": {"type": "object", "properties": {}}}
        self.client.read_resource = AsyncMock(side_effect=[
            {"instances": [{"id": "UnityProject@abc123", "path": str(self.root / "Assets")}]},
            {"unity": {"instance_id": None}},
            {"projectRoot": str(self.root), "unityVersion": "6000.3.24f1"},
        ])
        self.client._request = AsyncMock(return_value={"content": []})

        info = await self.client.verify_project(self.root)

        self.assertEqual(info["projectRoot"], str(self.root))
        self.client._request.assert_awaited_once_with("tools/call", {
            "name": "set_active_instance", "arguments": {"instance": "UnityProject@abc123"}
        })

    async def test_matching_instance_is_selected_once_per_mcp_connection(self):
        self.client.tools["set_active_instance"] = {"inputSchema": {"type": "object", "properties": {}}}
        self.client.read_resource = AsyncMock(side_effect=[
            {"instances": [{"id": "UnityProject@abc123", "path": str(self.root / "Assets")}]},
            {"unity": {"instance_id": "UnityProject@abc123"}},
            {"projectRoot": str(self.root), "unityVersion": "6000.3.24f1"},
            {"instances": [{"id": "UnityProject@abc123", "path": str(self.root / "Assets")}]},
            {"unity": {"instance_id": "UnityProject@abc123"}},
            {"projectRoot": str(self.root), "unityVersion": "6000.3.24f1"},
        ])
        self.client._request = AsyncMock(return_value={"content": []})

        await self.client.verify_project(self.root)
        await self.client.verify_project(self.root)

        self.client._request.assert_awaited_once_with("tools/call", {
            "name": "set_active_instance", "arguments": {"instance": "UnityProject@abc123"}
        })

    async def test_reselects_matching_instance_after_editor_context_changes(self):
        self.client.tools["set_active_instance"] = {"inputSchema": {"type": "object", "properties": {}}}
        self.client.read_resource = AsyncMock(side_effect=[
            {"instances": [{"id": "UnityProject@abc123", "path": str(self.root / "Assets")}]},
            {"unity": {"instance_id": "UnityProject@abc123"}},
            {"projectRoot": str(self.root), "unityVersion": "6000.3.24f1"},
            {"instances": [{"id": "UnityProject@abc123", "path": str(self.root / "Assets")}]},
            {"unity": {"instance_id": "OtherProject@def456"}},
            {"projectRoot": str(self.root), "unityVersion": "6000.3.24f1"},
        ])
        self.client._request = AsyncMock(return_value={"content": []})

        await self.client.verify_project(self.root)
        await self.client.verify_project(self.root)

        self.assertEqual(self.client._request.await_count, 2)
        self.assertTrue(all(call.args[0] == "tools/call" and
                            call.args[1]["arguments"]["instance"] == "UnityProject@abc123"
                            for call in self.client._request.await_args_list))

    async def test_scene_smoke_accepts_unity_instance_ids_response_shape(self):
        self.process.find_ids_key = "instanceIDs"
        result = await self.client.smoke_test(self.root)
        self.assertEqual(result["inspected_object_ids"], [42])

    async def test_scene_smoke_uses_unique_material_path_and_deletes_same_path(self):
        result = await self.client.smoke_test(self.root)
        asset_path = result["asset_created"]
        self.assertRegex(asset_path, r"^Assets/Studio/Generated/StudioMCPProbe-[0-9a-f]{32}\.mat$")
        self.assertIn("Rigidbody", result["verified_component_types"])
        self.assertEqual(self.process.created_asset_paths, [asset_path])
        self.assertIn(asset_path, self.process.deleted_asset_paths)
        self.assertIn(mcp.PROBE_SCENE_PATH, self.process.deleted_asset_paths)
        self.assertFalse(self.process.asset_exists)

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

    async def test_assets_cannot_escape_into_project_settings(self):
        with self.assertRaises(mcp.MCPError):
            await self.client.call_for_agent("developer", "manage_scene", {
                "action": "load", "scene_path": "Assets/../ProjectSettings/unsafe.unity"
            }, self.root)
        self.assertFalse(self.process.calls)

    async def test_editor_switch_is_rejected_before_any_tool_call(self):
        self.process.root = str(self.temp / "another-project")
        with self.assertRaisesRegex(mcp.MCPError, "does not match"):
            await self.client.for_agent("developer", self.root).call("manage_scene", {"action": "create"})
        self.assertFalse(self.process.calls)

    async def test_live_mode_rejects_wrong_project_without_batch_fallback(self):
        from studio.state import Job
        from studio.unity import UnityRunner, UnityFailure
        job=Job.create("test",1,root=self.root.parents[1])
        project=job.directory/"UnityProject"
        project.mkdir()
        with patch.dict(os.environ,{"STUDIO_UNITY_MODE":"live"}),patch.object(
                mcp.UnityMCPClient,"from_environment",return_value=self.client),patch.object(self.client,"close",AsyncMock()),patch(
                "studio.unity.ensure_live_editor",new=AsyncMock()):
            with self.assertRaisesRegex(UnityFailure,"does not match"):
                await UnityRunner(job,project).compile()
        self.assertFalse(self.process.calls)

    async def test_live_scene_assembler_menu_is_allowed_but_arbitrary_menus_are_not(self):
        await self.client.for_agent("developer",self.root).call("execute_menu_item",{
            "menu_path":"Studio/Assemble prompt-driven scenes"})
        with self.assertRaises(mcp.MCPError):
            await self.client.for_agent("developer",self.root).call("execute_menu_item",{"menu_path":"File/Exit"})
        self.assertEqual(len(self.process.calls),1)

    async def test_qa_camera_access_is_limited_to_screenshots(self):
        await self.client.for_agent("qa",self.root).call("manage_camera",{
            "action":"screenshot","capture_source":"scene_view","include_image":True
        })
        with self.assertRaises(mcp.MCPError):
            await self.client.for_agent("qa",self.root).call("manage_camera",{
                "action":"create_camera","camera_name":"Unauthorized"
            })

    async def test_existing_agent_service_uses_role_tools(self):
        from studio.api import ModelService
        from studio.state import Job
        job = Job.create("test", .1, root=self.root.parents[1])
        project = job.directory / "UnityProject"
        project.mkdir()
        self.process.root = str(project)
        service = ModelService(None, job)
        # A live client is injected only at the transport boundary.
        with patch.object(mcp.UnityMCPClient, "from_environment", return_value=self.client), patch.object(self.client, "close", AsyncMock()):
            for role in mcp.AGENT_TOOLS:
                result = await service.unity_context(role)
                self.assertIn(result["tool"], mcp.AGENT_TOOLS[role])
        self.assertEqual(len(self.process.calls), 5)

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
    def test_uvx_uses_the_application_python(self):
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "1"}, clear=True):
            client = mcp.UnityMCPClient.from_environment()
            self.assertEqual(client.args[:2], ["--python", sys.executable])

    def test_environment_client_is_reused_and_remains_alive_between_agent_calls(self):
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "1"}, clear=True):
            first = mcp.UnityMCPClient.from_environment()
            self.assertIs(mcp.UnityMCPClient.from_environment(), first)
            self.assertTrue(first._persistent)
            marker = object()
            first.process = marker
            asyncio.run(first.close())
            self.assertIs(first.process, marker)
            first.process = None
            asyncio.run(mcp.shutdown_environment_clients())
            self.assertFalse(first._persistent)

    def test_enabled_by_default_and_explicit_opt_out_and_timeout_is_bounded(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNotNone(mcp.UnityMCPClient.from_environment())
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "0"}, clear=False):
            self.assertIsNone(mcp.UnityMCPClient.from_environment())
        with self.assertRaises(ValueError):
            mcp.UnityMCPClient("uvx", [], timeout=0)


if __name__ == "__main__":
    unittest.main()
