"""Optional, restricted MCP stdio client for Unity Editor automation.

Only trusted Python orchestration can invoke tools. Game prompts are never
interpreted as shell commands or passed straight through as MCP arguments.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from studio.config import JOBS, ROOT

log = logging.getLogger(__name__)
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
PROBE_SCENE_NAME = "StudioMCPProbeScene"
PROBE_SCENE_PATH = "Assets/Studio/Generated/StudioMCPProbeScene.unity"
PROBE_ASSET_PATH = "Assets/Studio/Generated/StudioMCPProbe.mat"
PROBE_OBJECT_NAME = "StudioMCPProbe"

# Permissions follow the five existing agents. Tools are never new agent roles.
AGENT_TOOLS = {
    "supervisor": {"manage_scene", "find_gameobjects", "read_console"},
    "designer": {"manage_scene", "find_gameobjects"},
    "developer": {"manage_scene", "manage_gameobject", "manage_components", "manage_asset", "find_gameobjects"},
    "art": {"manage_asset"},
    "qa": {"manage_scene", "find_gameobjects", "read_console", "validate_script", "run_tests", "get_test_job"},
}
AGENT_ACTIONS = {
    "supervisor": {
        "manage_scene": {"get_active", "get_hierarchy", "get_build_settings", "get_loaded_scenes"},
        "find_gameobjects": {None}, "read_console": {"get"},
    },
    "designer": {
        "manage_scene": {"get_active", "get_hierarchy", "get_build_settings", "get_loaded_scenes"},
        "find_gameobjects": {None},
    },
    "developer": {
        "manage_scene": {"get_active", "get_hierarchy", "get_build_settings", "get_loaded_scenes", "create", "save", "load"},
        "manage_gameobject": {"find", "create", "modify", "delete"},
        "manage_components": {"add", "set_property"},
        "manage_asset": {"search", "create", "import", "delete"},
        "find_gameobjects": {None},
    },
    "art": {"manage_asset": {"search", "create", "import", "delete"}},
    "qa": {
        "manage_scene": {"get_active", "get_hierarchy", "get_build_settings", "get_loaded_scenes", "validate"},
        "find_gameobjects": {None}, "read_console": {"get"},
        "validate_script": {None}, "run_tests": {None}, "get_test_job": {None},
    },
}
SAFE_TOOLS = set().union(*AGENT_TOOLS.values())


class MCPError(RuntimeError):
    """A bounded connection, permission, or Unity-tool failure."""


def _content_value(result):
    """Decode the common MCP text envelope without assuming a tool payload shape."""
    content = result.get("content", []) if isinstance(result, dict) else []
    for item in content:
        text = item.get("text") if isinstance(item, dict) else None
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(value, dict) and value.get("success") is True and "data" in value:
            return value["data"]
        return value
    return None


def _check_tool_error(result, tool_name):
    if result.get("isError"):
        content = _content_value(result)
        raise MCPError(f"Unity MCP tool {tool_name} failed: {str(content or 'unknown tool error')[:500]}")
    value = _content_value(result)
    if isinstance(value, dict) and value.get("success") is False:
        raise MCPError(f"Unity MCP tool {tool_name} failed: {str(value.get('message', value))[:500]}")
    return value


def _authorized_project_root(value):
    root = Path(value).resolve()
    jobs_root = JOBS.resolve()
    if not root.is_relative_to(jobs_root) or root.name != "UnityProject":
        raise MCPError("MCP access is limited to a job's UnityProject folder")
    return root


class MCPAgentTools:
    """Agent-scoped callable view of discovered MCP tools."""

    def __init__(self, client, agent, project_root):
        if agent not in AGENT_TOOLS:
            raise MCPError("Unknown Unity studio agent")
        self.client, self.agent = client, agent
        self.project_root = _authorized_project_root(project_root)

    @property
    def available(self):
        return sorted(AGENT_TOOLS[self.agent].intersection(self.client.tools))

    async def call(self, name, arguments):
        return await self.client.call_for_agent(self.agent, name, arguments, self.project_root)


class UnityMCPClient:
    def __init__(self, command, args, timeout=20):
        if not command:
            raise ValueError("MCP server command is required")
        if not 1 <= float(timeout) <= 120:
            raise ValueError("MCP timeout must be between 1 and 120 seconds")
        self.command, self.args, self.timeout = command, list(args), float(timeout)
        self.process = None
        self._next_id = 0
        self.tools = {}
        self.server_info = None
        self._lock = asyncio.Lock()
        self._stderr_task = None

    @classmethod
    def from_environment(cls):
        if os.environ.get("STUDIO_UNITY_MCP_ENABLED", "0").casefold() not in ("1", "true", "yes"):
            return None
        command = os.environ.get("STUDIO_UNITY_MCP_COMMAND", "uvx").strip()
        raw = os.environ.get("STUDIO_UNITY_MCP_ARGS_JSON", '["--from","mcpforunityserver==10.3.0","mcp-for-unity"]')
        try:
            args = json.loads(raw)
            timeout = float(os.environ.get("STUDIO_UNITY_MCP_TIMEOUT", "20"))
        except (json.JSONDecodeError, ValueError) as error:
            raise MCPError("Invalid Unity MCP arguments or timeout configuration") from error
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise MCPError("STUDIO_UNITY_MCP_ARGS_JSON must be a JSON string array")
        try:
            return cls(command, args, timeout)
        except ValueError as error:
            raise MCPError(str(error)) from error

    async def connect(self):
        if self.process and self.process.returncode is None:
            return self
        try:
            # argv-based startup: no shell is involved, so prompt text cannot form commands.
            self.process = await asyncio.create_subprocess_exec(
                self.command, *self.args, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=ROOT, limit=2**20)
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            init = await self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "telegram-unity-studio", "version": "1.0"},
            })
            negotiated = init.get("protocolVersion")
            if negotiated not in SUPPORTED_PROTOCOL_VERSIONS:
                raise MCPError(f"MCP protocol version mismatch: server={negotiated}")
            self.server_info = init.get("serverInfo", {})
            if not isinstance(self.server_info, dict):
                raise MCPError("Unity MCP returned invalid server identity metadata")
            await self._notify("notifications/initialized", {})
            entries, cursor, seen_cursors = [], None, set()
            for _ in range(20):
                listing = await self._request("tools/list", {"cursor": cursor} if cursor else {})
                page = listing.get("tools", [])
                if not isinstance(page, list):
                    raise MCPError("Unity MCP returned an invalid tools/list response")
                entries.extend(page)
                cursor = listing.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise MCPError("Unity MCP repeated a tools/list pagination cursor")
                seen_cursors.add(cursor)
            else:
                raise MCPError("Unity MCP tool discovery exceeded its pagination limit")
            self.tools = {tool.get("name"): tool for tool in entries
                          if isinstance(tool, dict) and isinstance(tool.get("name"), str)}
            log.info("Unity MCP connected server=%s discovered_tools=%d", self.server_info.get("name", "unknown"), len(self.tools))
            return self
        except (OSError, ValueError, asyncio.TimeoutError) as error:
            await self.close()
            if isinstance(error, MCPError):
                raise
            raise MCPError(f"Could not start or initialize Unity MCP server ({type(error).__name__})") from error
        except Exception:
            await self.close()
            raise

    async def _drain_stderr(self):
        try:
            while self.process and self.process.stderr:
                line = await self.process.stderr.readline()
                if not line:
                    break
                log.info("Unity MCP: %s", line.decode("utf-8", errors="replace").rstrip()[:500])
        except (asyncio.CancelledError, OSError):
            return

    async def _write(self, message):
        if not self.process or self.process.returncode is not None:
            raise MCPError("Unity MCP server is not running")
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        await asyncio.wait_for(self.process.stdin.drain(), self.timeout)

    async def _read_message(self):
        line = await asyncio.wait_for(self.process.stdout.readline(), self.timeout)
        if not line:
            raise MCPError("Unity MCP server closed its protocol stream")
        try:
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MCPError("Unity MCP server returned malformed JSON-RPC") from error
        if not isinstance(message, dict):
            raise MCPError("Unity MCP server returned an invalid JSON-RPC message")
        return message

    async def _request(self, method, params):
        async with self._lock:
            self._next_id += 1
            request_id = self._next_id
            await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            async def read_response():
                while True:
                    response = await self._read_message()
                    if response.get("id") != request_id:
                        continue
                    if "error" in response:
                        error = response["error"]
                        message = str(error.get("message", "unknown error"))[:500] if isinstance(error, dict) else "unknown error"
                        raise MCPError(f"MCP {method} failed: {message}")
                    result = response.get("result")
                    if not isinstance(result, dict):
                        raise MCPError(f"MCP {method} returned no result object")
                    return result
            try:
                return await asyncio.wait_for(read_response(), self.timeout)
            except asyncio.TimeoutError as error:
                raise MCPError(f"MCP {method} timed out after {self.timeout:g} seconds") from error

    async def _notify(self, method, params):
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def read_resource(self, uri):
        if uri != "mcpforunity://project/info":
            raise MCPError("Only Unity project information can be read by this integration")
        result = await self._request("resources/read", {"uri": uri})
        for item in result.get("contents", []):
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                data = value.get("data", value)
                if isinstance(data, dict):
                    return data
        raise MCPError("Unity MCP project-info resource did not return JSON project details")

    async def verify_project(self, expected_root):
        expected = _authorized_project_root(expected_root)
        info = await self.read_resource("mcpforunity://project/info")
        root = info.get("projectRoot") or info.get("project_root")
        if not root or Path(root).resolve() != expected:
            raise MCPError("Connected Unity Editor does not match this job's UnityProject")
        version = str(info.get("unityVersion") or info.get("unity_version") or "")
        try:
            major, minor = (int(part) for part in version.split(".", 2)[:2])
        except (TypeError, ValueError):
            raise MCPError("MCP project information did not include a valid Unity version")
        if major < 2021 or (major == 2021 and minor < 3):
            raise MCPError("Connected Unity version is outside the configured compatibility range")
        return info

    @staticmethod
    def _validate_asset_paths(value, project_root):
        path_keys = {"path", "prefabpath", "materialpath", "scriptpath", "filepath",
                     "destination", "destinationpath", "sourcepath", "uri"}
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = key.casefold().replace("_", "") if isinstance(key, str) else ""
                if normalized_key in path_keys and isinstance(item, str):
                    candidate = item
                    prefix = "mcpforunity://path/"
                    if candidate.startswith(prefix):
                        candidate = candidate[len(prefix):]
                    if normalized_key == "sourcepath" and Path(candidate).is_absolute():
                        source = Path(candidate).resolve()
                        if not source.is_relative_to(project_root.resolve()):
                            raise MCPError("MCP import source escapes the authorized Unity project")
                        candidate = source.relative_to(project_root.resolve()).as_posix()
                    if candidate == "Assets":
                        continue
                    if not candidate.startswith("Assets/") or Path(candidate).is_absolute():
                        raise MCPError("MCP asset paths must be relative to this Unity project's Assets folder")
                    resolved = (project_root / candidate).resolve()
                    if not resolved.is_relative_to(project_root.resolve()):
                        raise MCPError("MCP asset path escapes the authorized Unity project")
                UnityMCPClient._validate_asset_paths(item, project_root)
        elif isinstance(value, list):
            for item in value:
                UnityMCPClient._validate_asset_paths(item, project_root)

    async def call_for_agent(self, agent, name, arguments, project_root):
        if agent not in AGENT_TOOLS or name not in AGENT_TOOLS[agent] or name not in SAFE_TOOLS:
            raise MCPError("This agent is not authorized to use that MCP tool")
        if not isinstance(arguments, dict):
            raise MCPError("MCP tool arguments must be an object")
        action = arguments.get("action")
        if name not in AGENT_ACTIONS[agent] or action not in AGENT_ACTIONS[agent][name]:
            raise MCPError("MCP operation is outside this agent's safe action allowlist")
        root = _authorized_project_root(project_root)
        self._validate_asset_paths(arguments, root)
        if name == "manage_gameobject" and action == "delete":
            if arguments.get("target") != PROBE_OBJECT_NAME:
                raise MCPError("GameObject deletion is reserved for the exact smoke-test object")
        if name == "manage_asset" and action == "delete":
            allowed_probe_files = {PROBE_SCENE_PATH, PROBE_ASSET_PATH}
            if arguments.get("path") not in allowed_probe_files:
                raise MCPError("Asset deletion is reserved for the exact MCP smoke-test artifacts")
            if agent == "art" and arguments.get("path") != PROBE_ASSET_PATH:
                raise MCPError("Art Designer cleanup is reserved for its exact MCP probe material")
            if agent == "developer" and arguments.get("path") != PROBE_SCENE_PATH:
                raise MCPError("Unity Developer cleanup is reserved for its exact MCP probe scene")
        if name not in self.tools:
            raise MCPError("Unity MCP server does not expose the requested tool")
        schema = self.tools[name].get("inputSchema")
        if isinstance(schema, dict):
            missing = [field for field in schema.get("required", []) if field not in arguments]
            if missing:
                raise MCPError(f"Missing required parameters for {name}: {', '.join(missing)}")
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        _check_tool_error(result, name)
        log.info("Unity MCP tool call agent=%s tool=%s action=%s", agent, name, action or "read")
        return result

    def for_agent(self, agent, project_root):
        return MCPAgentTools(self, agent, project_root)

    async def inspect_scene(self, agent, project_root):
        info = await self.verify_project(project_root)
        tools = self.for_agent(agent, project_root)
        result = await tools.call("manage_scene", {"action": "get_hierarchy", "page_size": 200, "cursor": 0})
        log.info("Unity MCP scene inspection project=%s unity=%s", Path(project_root).name, info.get("unityVersion", "unknown"))
        return result

    async def smoke_test(self, project_root):
        """Create/inspect a disposable scene and restore the prior clean scene."""
        await self.verify_project(project_root)
        project = _authorized_project_root(project_root)
        scene_file = project / PROBE_SCENE_PATH
        asset_file = project / PROBE_ASSET_PATH
        if not (project / "Assets/Studio/Generated").is_dir():
            raise MCPError("MCP smoke test requires the generated project's Assets/Studio/Generated folder")
        if scene_file.exists() or asset_file.exists():
            raise MCPError("A reserved MCP probe artifact already exists; refusing to overwrite it")
        developer = self.for_agent("developer", project)
        art = self.for_agent("art", project)
        qa = self.for_agent("qa", project)
        active_result = await developer.call("manage_scene", {"action": "get_active"})
        original = _content_value(active_result)
        if not isinstance(original, dict) or original.get("isDirty") is not False:
            raise MCPError("MCP smoke test requires one saved, clean active Unity scene")
        original_path = original.get("path")
        if not isinstance(original_path, str) or not original_path.startswith("Assets/"):
            raise MCPError("MCP smoke test requires the active scene to be saved under Assets/")
        self._validate_asset_paths({"path": original_path}, project)
        if not (project / original_path).is_file():
            raise MCPError("MCP smoke test could not find the active scene file in this project")
        loaded_result = await developer.call("manage_scene", {"action": "get_loaded_scenes"})
        loaded = _content_value(loaded_result)
        scene_list = loaded.get("scenes") if isinstance(loaded, dict) else None
        if not isinstance(scene_list, list) or len(scene_list) != 1:
            raise MCPError("MCP smoke test requires exactly one loaded scene to preserve editor state")
        existing_probe = await qa.call("find_gameobjects", {
            "search_term": PROBE_OBJECT_NAME, "search_method": "by_name", "page_size": 5, "cursor": 0
        })
        existing_data = _content_value(existing_probe)
        if isinstance(existing_data, dict) and existing_data.get("ids"):
            raise MCPError("MCP smoke test found an object using its reserved sentinel name in the original scene")

        scene_created = False
        object_created = False
        asset_created = False
        failures = []
        try:
            await developer.call("manage_scene", {
                "action": "create", "name": PROBE_SCENE_NAME, "path": "Assets/Studio/Generated"
            })
            scene_created = True
            await developer.call("manage_gameobject", {
                "action": "create", "name": PROBE_OBJECT_NAME, "primitive_type": "Cube", "position": [0, 0, 0]
            })
            object_created = True
            await developer.call("manage_components", {
                "action": "add", "target": PROBE_OBJECT_NAME, "component_type": "Rigidbody"
            })
            await art.call("manage_asset", {
                "action": "create", "path": PROBE_ASSET_PATH, "asset_type": "Material",
                "properties": {"color": [0.8, 0.2, 0.8, 1.0]},
            })
            asset_created = True
            await qa.call("manage_scene", {"action": "get_hierarchy", "page_size": 50, "cursor": 0})
            inspected = await qa.call("find_gameobjects", {
                "search_term": PROBE_OBJECT_NAME, "search_method": "by_name", "page_size": 5, "cursor": 0
            })
            value = _content_value(inspected)
            if not isinstance(value, dict) or not value.get("ids"):
                raise MCPError("MCP scene inspection did not find the probe GameObject")
            await qa.call("read_console", {"action": "get", "types": ["error"], "count": 20})
            return {"scene_created": PROBE_SCENE_PATH, "inspected_object_ids": value["ids"],
                    "component_added": "Rigidbody", "asset_created": PROBE_ASSET_PATH,
                    "scene_hierarchy_checked": True, "error_console_checked": True}
        finally:
            if scene_created:
                if object_created:
                    try:
                        await developer.call("manage_gameobject", {"action": "delete", "target": PROBE_OBJECT_NAME})
                    except Exception as error:
                        failures.append(f"probe object cleanup failed: {type(error).__name__}")
                try:
                    await developer.call("manage_scene", {"action": "save"})
                except Exception as error:
                    failures.append(f"probe scene save failed: {type(error).__name__}")
                restored = False
                try:
                    await developer.call("manage_scene", {"action": "load", "path": original_path})
                    restored = True
                except Exception as error:
                    failures.append(f"original scene restore failed: {type(error).__name__}")
                if restored:
                    try:
                        await developer.call("manage_asset", {"action": "delete", "path": PROBE_SCENE_PATH})
                    except Exception as error:
                        failures.append(f"probe scene cleanup failed: {type(error).__name__}")
                    if asset_created or asset_file.exists():
                        try:
                            await art.call("manage_asset", {"action": "delete", "path": PROBE_ASSET_PATH})
                        except Exception as error:
                            failures.append(f"probe asset cleanup failed: {type(error).__name__}")
                if scene_file.exists() or asset_file.exists():
                    failures.append("probe files remain in the Unity project")
                if failures:
                    log.error("Unity MCP smoke-test cleanup incomplete: %s", "; ".join(failures))
                    raise MCPError("MCP smoke-test cleanup incomplete: " + "; ".join(failures))

    async def close(self):
        process, self.process = self.process, None
        stderr_task, self._stderr_task = self._stderr_task, None
        if stderr_task:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


async def connected_client(expected_project=None):
    client = UnityMCPClient.from_environment()
    if client is None:
        return None
    try:
        await client.connect()
        if expected_project is not None:
            await client.verify_project(expected_project)
        return client
    except Exception:
        await client.close()
        raise
