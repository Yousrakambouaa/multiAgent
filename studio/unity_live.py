"""Run existing Unity stages in the verified live Editor, never a second process."""
import asyncio
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from studio.mcp_client import UnityMCPClient, MCPError, _content_value, wait_for_project, transient_editor_error
from studio.state import write_json
from studio.unity import UnityFailure, compiler_errors, unity_failure_owner


async def require_editor_idle(resource, stage, deadline):
    """Wait out Unity's Play Mode transition; never stop an active game automatically."""
    transition_deadline = min(deadline, time.monotonic() + 30)
    state = await resource("mcpforunity://editor/state")
    editor = state.get("editor") or {}
    play_mode = editor.get("play_mode") or {}
    while play_mode.get("is_changing") and time.monotonic() < transition_deadline:
        await asyncio.sleep(.25)
        state = await resource("mcpforunity://editor/state")
        editor = state.get("editor") or {}
        play_mode = editor.get("play_mode") or {}
    if play_mode.get("is_changing"):
        raise UnityFailure(stage, "Unity Play Mode transition did not finish within 30 seconds", "infrastructure")
    if play_mode.get("is_playing"):
        raise UnityFailure(stage, "Stop Play mode before running studio stages", "infrastructure")


async def run_live(runner, stage, extra, timeout):
    client = UnityMCPClient.from_environment()
    if client is None:
        raise UnityFailure(stage, "Live mode requires STUDIO_UNITY_MCP_ENABLED=1 and the matching project's Editor bridge", "infrastructure")
    deadline = time.monotonic() + timeout
    async def recover(error, recovery_deadline):
        if not transient_editor_error(error):
            raise error
        runner.job.event("supervisor", "mcp_reconnecting", unity_stage=stage, reason=str(error)[:500])
        await wait_for_project(client, runner.project,
            timeout=max(0, min(deadline, recovery_deadline) - time.monotonic()),
            check_cancelled=runner.job.check_cancelled)
        await asyncio.sleep(.25)
    async def call(role, tool, arguments):
        recovery_deadline = min(deadline, time.monotonic() + 90)
        while True:
            runner.job.check_cancelled()
            try:
                result = await client.for_agent(role, runner.project).call(tool, arguments)
                break
            except MCPError as error:
                if time.monotonic() >= recovery_deadline:
                    raise
                # These errors mean the bridge could not dispatch the operation.
                # Never blindly replay a mutating command after an ambiguous timeout.
                await recover(error, recovery_deadline)
        runner.job.event(role, "mcp_tool_used", tool=tool, action=arguments.get("action"), project=str(runner.project))
        return _content_value(result)
    async def resource(uri):
        recovery_deadline = min(deadline, time.monotonic() + 90)
        while True:
            try:
                return await client.read_resource(uri)
            except MCPError as error:
                if time.monotonic() >= recovery_deadline:
                    raise
                await recover(error, recovery_deadline)
    try:
        await client.connect()
        await wait_for_project(client, runner.project, timeout=min(90, timeout), check_cancelled=runner.job.check_cancelled)
        await require_editor_idle(resource, stage, deadline)
        if stage == "compile":
            evidence_path = runner.project / "Artifacts/compilation.json"
            evidence_path.unlink(missing_ok=True)
            requested_at = int(time.time() * 1000)
            # Full import is required when a repair adds/moves files or changes asmdefs.
            await call("developer", "refresh_unity", {"mode": "force", "scope": "all", "compile": "request", "wait_for_ready": True})
            # Read state after the refresh, including across a domain reload.
            while time.monotonic() < deadline:
                state = await resource("mcpforunity://editor/state")
                compilation = state.get("compilation") or {}
                if not compilation.get("is_compiling") and evidence_path.is_file():
                    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                    if (evidence.get("completed") and evidence.get("started_unix_ms", 0) >= requested_at
                            and Path(evidence.get("project", "")).resolve() == runner.project.resolve()):
                        break
                await asyncio.sleep(1)
            else:
                raise UnityFailure(stage, "Live Editor compilation timed out", "infrastructure")
            console = await call("qa", "read_console", {"action": "get", "types": ["error"], "count": 200, "include_stacktrace": True})
            output = json.dumps(console, ensure_ascii=False).replace("\\n", "\n").replace("\\\\", "\\")
            write_json(runner.job.directory / "live_compile_console.json", console)
            fresh_errors = "\n".join(evidence.get("errors", []))
            if not evidence.get("success"):
                diagnostic = fresh_errors or output
                raise UnityFailure(stage, diagnostic, unity_failure_owner(stage, diagnostic))
            return "Application.AssetDatabase Initial Refresh End\nLive MCP compilation completed"
        if stage == "assembly":
            active = await call("developer", "manage_scene", {"action": "get_active"})
            if active.get("isDirty"):
                raise UnityFailure(stage, "Save the open scene before Studio assembly; unsaved edits were preserved", "infrastructure")
            menu = "Studio/Assemble prompt-driven scenes" if runner.flexible() else "Studio/Assemble validated scenes"
            await call("developer", "execute_menu_item", {"menu_path": menu})
            # The existing assembler saves its scenes and writes assembly.json.
            # Preserve that diagnostic if it fails before saving the new scene.
            return "Live MCP scene assembly completed"
        if stage in ("EditMode", "PlayMode"):
            suite = extra[extra.index("-testFilter") + 1]
            arguments = {"mode": stage, "test_names": [suite], "include_details": True,
                         "init_timeout": 120000 if stage == "PlayMode" else 30000}
            started = None
            for attempt in range(5):
                try:
                    started = await call("qa", "run_tests", arguments)
                    break
                except MCPError as error:
                    if "tests_running" not in str(error).casefold() or attempt == 4:
                        raise
                    # Unity can finish its previous test job before the MCP test
                    # coordinator releases its start lock. Retry briefly, bounded.
                    await asyncio.sleep(2)
            job_id = started.get("job_id")
            if not job_id:
                raise UnityFailure(stage, "MCP did not start a test job", "infrastructure")
            while time.monotonic() < deadline:
                result = await call("qa", "get_test_job", {"job_id": job_id, "include_details": True, "wait_timeout": 5})
                if result.get("status") in ("succeeded", "failed", "cancelled"):
                    break
                await asyncio.sleep(1)
            else:
                raise UnityFailure(stage, "Live Editor test run timed out", "infrastructure")
            write_json(runner.job.directory / f"live_{stage}_results.json", result)
            payload = result.get("result") or {}
            cases = payload.get("results") or []
            root = ET.Element("test-run", result="Passed" if result.get("status") == "succeeded" else "Failed")
            for case in cases:
                item = ET.SubElement(root, "test-case", name=str(case.get("name", "")),
                    fullname=str(case.get("fullName", "")), result=str(case.get("state", "Unknown")))
                if case.get("message"):
                    ET.SubElement(ET.SubElement(item, "failure"), "message").text = case["message"]
            path = runner.project / "Artifacts" / f"{stage}.xml"
            path.parent.mkdir(parents=True, exist_ok=True)
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            return "Live MCP test job completed; results require validation"
        if stage == "android":
            evidence_path = runner.project / "Artifacts/android.json"
            apk_path = runner.project / "Artifacts/prototype.apk"
            progress_path = runner.project / "Artifacts/android_progress.json"
            last_progress = None
            evidence_path.unlink(missing_ok=True)
            apk_path.unlink(missing_ok=True)
            await call("developer", "execute_menu_item", {"menu_path": "Studio/Build Android APK"})
            while time.monotonic() < deadline:
                if progress_path.is_file():
                    try:
                        phase = json.loads(progress_path.read_text(encoding="utf-8")).get("phase")
                        if phase and phase != last_progress:
                            last_progress = phase
                            runner.job.event("developer", "android_" + phase)
                    except (OSError, ValueError):
                        pass
                if evidence_path.is_file():
                    try:
                        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        await asyncio.sleep(.2)
                        continue
                    if evidence.get("success") and apk_path.is_file() and apk_path.stat().st_size >= 10000:
                        import zipfile
                        with zipfile.ZipFile(apk_path) as archive:
                            if "AndroidManifest.xml" not in archive.namelist() or archive.testzip() is not None:
                                raise UnityFailure(stage, "APK integrity validation failed", "infrastructure")
                        return {"status": "passed", "path": str(apk_path), "device_test": "unverified"}
                    if evidence.get("error"):
                        raise UnityFailure(stage, evidence["error"], "infrastructure")
                await asyncio.sleep(1)
            raise UnityFailure(stage, "Live Editor Android build timed out without verified APK evidence", "infrastructure")
        raise UnityFailure(stage, "This stage is not supported in Live Editor mode; no batch process was launched", "infrastructure")
    except MCPError as error:
        raise UnityFailure(stage, str(error), "infrastructure") from error
    finally:
        await client.close()
