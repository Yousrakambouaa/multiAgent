import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import studio.mcp_client as mcp
from types import SimpleNamespace


class ProjectReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_project_startup_waits_for_its_bridge_when_older_editors_are_open(self):
        client = SimpleNamespace(verify_project=AsyncMock(side_effect=[
            mcp.MCPError("Connected Unity Editor does not match this job's UnityProject"), {"projectRoot": "expected"}]))
        with patch("studio.mcp_client.asyncio.sleep", new_callable=AsyncMock):
            result = await mcp.wait_for_project(client, Path("expected"), timeout=1, allow_startup_missing=True)
        self.assertEqual(result["projectRoot"], "expected")
        self.assertEqual(client.verify_project.await_count, 2)

    async def test_known_project_can_reconnect_without_calling_the_other_editor(self):
        with tempfile.TemporaryDirectory() as temp, patch("studio.mcp_client.JOBS", Path(temp)):
            expected = Path(temp) / "active" / "UnityProject"
            other = Path(temp) / "older" / "UnityProject"
            missing = False
            async def resource(uri):
                if uri.endswith("instances"):
                    entries = [{"id": "other", "path": str(other / "Assets")}]
                    if not missing:
                        entries.append({"id": "active", "path": str(expected / "Assets")})
                    return {"instances": entries}
                if uri.endswith("editor/state"):
                    return {"unity": {"instance_id": "active"}}
                return {"projectRoot": str(expected), "unityVersion": "6000.3.24f1"}
            client = mcp.UnityMCPClient("unused", [])
            client.tools = {"set_active_instance": {}, "manage_scene": {}}
            client.read_resource = AsyncMock(side_effect=resource)
            client._request = AsyncMock(return_value={"content": [{"type": "text", "text": '{"success":true}'}]})
            await client.verify_project(expected)
            missing = True
            with self.assertRaisesRegex(mcp.MCPError, "domain reload"):
                await client.for_agent("developer", expected).call("manage_scene", {"action": "get_active"})
            self.assertTrue(all(call.args[1].get("name") == "set_active_instance"
                                for call in client._request.await_args_list))
            missing = False
            info = await client.verify_project(expected)
            self.assertEqual(Path(info["projectRoot"]), expected)
