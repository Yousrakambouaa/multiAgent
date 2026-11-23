"""Unity CLI stages require fresh machine-readable evidence, not exit code alone."""
import asyncio
import json
import re
import shutil
import time
import os
import subprocess
import xml.etree.ElementTree as ET
import logging
from pathlib import Path

from studio.config import ROOT, TEMPLATES, UNITY, safe_path
from studio.schemas import CodeBundle, SceneBundle
from studio.state import JobCancelled, write_json

log = logging.getLogger(__name__)

RUNTIME_CONTRACT = """Studio.Runtime.LevelRules : MonoBehaviour has virtual Configure(PlayerMotor player), virtual int CoinPoints().
PlayerMotor has public float moveSpeed, jumpSpeed; Rigidbody2D Body; bool Grounded; bool TryJump().
GameDirector has public void AddCoin(); int Score; int Coins. All Unity 6 Input System, mobile UI and core gameplay
are supplied in the Studio.Runtime assembly. GeneratedRules.cs derives from LevelRules and configures movement and coin points."""


class UnityFailure(RuntimeError):
    def __init__(self, stage, message, owner="developer"):
        super().__init__(message)
        self.stage, self.owner = stage, owner


def compiler_errors(output):
    """Extract and deduplicate Unity/Roslyn diagnostics without licensing chatter."""
    location_pattern = re.compile(r"(?P<file>Assets[\\/][^:\r\n]*?\.cs)\((?P<line>\d+)(?:,(?P<column>\d+))?\):")
    code_pattern = re.compile(r"\b(?:error\s+)?(?P<code>CS\d+):\s*(?P<message>[^\r\n]+)")
    unique = {}
    for line in output.splitlines():
        code = code_pattern.search(line)
        location = location_pattern.search(line)
        if not code or not location:
            continue
        item = {"file": location.group("file").replace("\\", "/"),
                "line": int(location.group("line")),
                "column": int(location.group("column") or 0),
                "code": code.group("code"), "message": code.group("message")}
        unique[tuple(item.values())] = item
    return list(unique.values())


def licensing_status(output):
    recovered = bool(re.search(r"Successfully (?:updated license|resolved entitlement details)|License group:", output, re.I))
    blocked = bool(re.search(r"No valid (?:Unity )?license|No valid entitlement|Failed to activate|"
                             r"Failed to acquire global mutex.*License|license.*(?:unavailable|expired)", output, re.I))
    warnings = [line for line in output.splitlines() if re.search(r"Licensing.*(?:failed|Error:)", line, re.I)]
    return {"status": "available" if recovered else "blocked" if blocked else "unverified",
            "warnings": warnings[:10], "manual_action": "Sign in to Unity Hub and activate/refresh a license" if blocked and not recovered else ""}


def unity_failure_owner(stage, output):
    errors = compiler_errors(output)
    if errors:
        return "developer" if all(e["file"].startswith("Assets/Studio/Generated/") for e in errors) else "infrastructure"
    owner = "scene" if stage == "assembly" else "qa" if stage in ("EditMode", "PlayMode") else "developer"
    if stage == "PlayMode" and any(marker in output.casefold() for marker in
            ("probe_ground_collision", "probe_player_exists", "missing object", "missing component")):
        owner = "scene"
    # Recoverable license-client noise must not suppress scene/runtime repairs.
    if licensing_status(output)["status"] == "available":
        output = "\n".join(line for line in output.splitlines() if "Licensing" not in line)
    if re.search(r"Assets[\\/]+Studio[\\/]+(Runtime|Editor|Tests)[\\/].*error CS", output):
        owner = "infrastructure"
    if ("another Unity instance" in output or "already open" in output
            or re.search(r"Licensing.*(?:failed|Error)|global mutex[^\n]*Unity[- ]LicenseClient|"
                         r"(?:Unity[- ]license|license)[^\n]{0,100}(?:mutex|failed|unavailable)|"
                         r"BIOS serial number|System\.Management\.ManagementException: Access denied|ObjectDisposedException", output, re.I)):
        owner = "infrastructure"
    if any(marker.casefold() in output.casefold() for marker in
           ("not enough space", "Internal build system error", "The backend process appears", "No space left")):
        owner = "infrastructure"
    return owner


def project_is_open(project):
    """Probe Unity's held lock, allowing stale lockfiles left after a crash."""
    path = Path(project) / "Temp/UnityLockfile"
    if not path.exists():
        return False
    try:
        with path.open("r+b") as handle:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


def mcp_enabled():
    return os.environ.get("STUDIO_UNITY_MCP_ENABLED", "1").casefold() in ("1", "true", "yes")


def uses_live_editor(mode, opened=False):
    if mode == "live":
        return True
    return mode == "auto" and (opened or mcp_enabled())


async def ensure_live_editor(job, project, timeout=None):
    """Open this job in the visible Editor once and wait for its matching MCP bridge."""
    from studio.mcp_client import UnityMCPClient, wait_for_project

    project = Path(project).resolve()
    if not project.is_dir():
        raise UnityFailure("project_setup", f"Unity project folder does not exist: {project}", "infrastructure")
    try:
        configured_timeout = float(os.environ.get("STUDIO_UNITY_STARTUP_TIMEOUT", "600"))
        requested_timeout = float(timeout) if timeout is not None else configured_timeout
    except (TypeError, ValueError) as error:
        raise UnityFailure("project_setup", "STUDIO_UNITY_STARTUP_TIMEOUT must be a number of seconds", "infrastructure") from error
    if not 30 <= configured_timeout <= 900:
        raise UnityFailure("project_setup", "STUDIO_UNITY_STARTUP_TIMEOUT must be between 30 and 900 seconds", "infrastructure")
    startup_timeout = min(configured_timeout, requested_timeout)
    client = UnityMCPClient.from_environment()
    if client is None:
        raise UnityFailure("project_setup", "Live Editor mode requires Unity MCP; set STUDIO_UNITY_MCP_ENABLED=1", "infrastructure")

    deadline = time.monotonic() + startup_timeout
    process = None
    if not project_is_open(project):
        if not UNITY.is_file():
            await client.close()
            raise UnityFailure("project_setup", f"Configured Unity Editor does not exist: {UNITY}", "infrastructure")
        job.check_cancelled()
        editor_log = job.directory / "unity-editor-live.log"
        command = [str(UNITY), "-projectPath", str(project), "-logFile", str(editor_log)]
        environment = os.environ.copy()
        environment.pop("OPENROUTER_API_KEY", None)
        environment.pop("DAHL_API_KEY", None)
        environment.pop("TELEGRAM_BOT_TOKEN", None)
        job.event("supervisor", "unity_editor_opening", project=str(project), log=str(editor_log))
        try:
            # No -batchmode or -quit: this is the visible Editor the user will work with.
            process = subprocess.Popen(command, cwd=project, env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            await client.close()
            raise UnityFailure("project_setup", f"Could not open the Unity Editor: {error}", "infrastructure") from error
    else:
        job.event("supervisor", "unity_editor_reused", project=str(project))

    try:
        while not project_is_open(project):
            job.check_cancelled()
            if process is not None and process.poll() is not None:
                detail = ""
                if editor_log.is_file():
                    detail = editor_log.read_text(encoding="utf-8", errors="replace")[-3000:]
                raise UnityFailure("project_setup", "Unity Editor exited before opening this project. " + detail, "infrastructure")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UnityFailure("project_setup", f"Unity Editor did not open {project} within {startup_timeout:g}s", "infrastructure")
            await asyncio.sleep(min(.5, remaining))

        job.check_cancelled()
        client.timeout = min(90, max(client.timeout, deadline - time.monotonic()))
        await client.connect()
        remaining = max(0, deadline - time.monotonic())
        await wait_for_project(client, project, timeout=remaining, check_cancelled=job.check_cancelled)
        job.event("supervisor", "unity_editor_ready", project=str(project), pid=process.pid if process else None)
    except Exception as error:
        if isinstance(error, (UnityFailure, JobCancelled)):
            raise
        raise UnityFailure("project_setup", f"Unity Editor opened but its MCP bridge did not connect: {error}", "infrastructure") from error
    finally:
        await client.close()


def available_space(path):
    """Honor Windows user quotas, not just the volume's total free bytes."""
    if os.name=="nt":
        import ctypes
        available=ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(str(Path(path).resolve()),ctypes.byref(available),None,None):
            raise OSError("Could not determine disk space available to this user")
        return available.value
    return shutil.disk_usage(path).free


def bootstrap(job, dimension="2D"):
    if dimension not in ("2D", "3D", "mixed"):
        raise UnityFailure("project_setup", f"Unsupported game dimension: {dimension!r}", "infrastructure")
    if available_space(job.directory) < 1_500_000_000:
        raise UnityFailure("project_setup","At least 1.5 GB free disk space is required for a new Unity project", "infrastructure")
    project = job.directory / "UnityProject"
    project.mkdir(exist_ok=True)
    if not (project / "ProjectSettings").exists():
        for name in ("ProjectSettings", "Packages"):
            shutil.copytree(ROOT / "generated_game" / name, project / name)
        settings = ROOT / "generated_game" / "Assets" / "Settings"
        if settings.exists():
            settings_target = project / "Assets" / "Settings"
            settings_target.mkdir(parents=True, exist_ok=True)
            # The starter project is a 2D URP template. Do not seed 3D/mixed
            # projects with its 2D renderer and scene templates; v2 scenes use
            # the built-in renderer unless their own data selects otherwise.
            two_d_only = {"renderer2d.asset", "universalrp.asset", "lit2dscenetemplate.scenetemplate",
                          "urp2dscenetemplate.unity"}
            for source in settings.rglob("*"):
                if not source.is_file():
                    continue
                relative = source.relative_to(settings)
                source_name = source.name.removesuffix(".meta").casefold()
                if dimension != "2D" and (source_name in two_d_only
                        or any(part.casefold() == "scenes" for part in relative.parts)):
                    continue
                target = settings_target / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            meta = settings.with_suffix(".meta")
            if meta.exists():
                shutil.copy2(meta, project / "Assets" / meta.name)
        settings_file = project / "ProjectSettings" / "ProjectSettings.asset"
        content = settings_file.read_text(encoding="utf-8")
        content = re.sub(r"activeInputHandler: \d+", "activeInputHandler: 1", content)
        settings_file.write_text(content, encoding="utf-8")
    for source in TEMPLATES.rglob("*"):
        if source.is_file():
            target = safe_path(project / "Assets" / "Studio", source.relative_to(TEMPLATES).as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    from studio.mcp_setup import prepare_mcp_project
    prepare_mcp_project(project)
    (project / "Assets" / "Resources").mkdir(parents=True, exist_ok=True)
    dimension_file = project / "Assets" / "Resources" / "studio_dimension.json"
    if dimension_file.is_file():
        try:
            existing_dimension = json.loads(dimension_file.read_text(encoding="utf-8")).get("dimension")
        except (OSError, ValueError):
            raise UnityFailure("project_setup", "The saved Unity project dimension marker is invalid", "infrastructure")
        if existing_dimension != dimension:
            raise UnityFailure("project_setup", "Saved Unity project dimension does not match the approved game specification", "infrastructure")
    write_json(dimension_file, {"dimension": dimension})
    write_json(project / "ProjectSettings" / "StudioGameProject.json",
               {"dimension": dimension, "rendering": "built-in"})
    (project / "Assets" / "Studio" / "Generated").mkdir(parents=True, exist_ok=True)
    try:
        project_label = project.relative_to(ROOT)
    except ValueError:
        project_label = project
    job.event("developer", "project_initialized", project=str(project_label))
    return project


def save_scripts(project, bundle):
    bundle = CodeBundle.model_validate(bundle.model_dump())
    for script in bundle.scripts:
        if script.filename != "GeneratedRules.cs":
            raise ValueError("Only the generated rules contract may be changed by the model")
        if not re.search(r"class\s+GeneratedRules\s*:\s*(?:Studio\.Runtime\.)?LevelRules", script.content):
            raise ValueError("GeneratedRules must extend the provided LevelRules")
        banned = ("UnityEditor", "System.IO", "System.Net", "System.Diagnostics", "System.Reflection", "DllImport", "unsafe ", "Process.", "Application.OpenURL", "Assembly.Load")
        if any(word in script.content for word in banned):
            raise ValueError("Generated gameplay code used APIs outside the runtime contract")
        safe_path(project / "Assets" / "Studio" / "Generated", script.filename).write_text(script.content, encoding="utf-8")


def validate_files(project, scenes, scripts):
    SceneBundle.model_validate(scenes.model_dump())
    allowed = {"UnityEngine.BoxCollider2D", "UnityEngine.CircleCollider2D", "UnityEngine.Rigidbody2D", "GeneratedRules"}
    allowed |= {"Studio.Runtime." + p.stem for p in (TEMPLATES / "Runtime").glob("*.cs")}
    for scene in scenes.scenes:
        for obj in scene.objects:
            if obj.sprite and not safe_path(project, obj.sprite).is_file():
                raise ValueError("Missing required sprite: " + obj.sprite)
            for comp in obj.components:
                if comp.type not in allowed:
                    raise ValueError("Unverified component metadata: " + comp.type)
    for script in scripts.scripts:
        if not (project / "Assets" / "Studio" / "Generated" / script.filename).is_file():
            raise ValueError("Missing required script")


class UnityRunner:
    def __init__(self, job, project):
        self.job, self.project = job, project

    def flexible(self):
        path = self.project / "Assets/Resources/studio_scenes.json"
        return path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("version") == 2

    def validate_dimension(self):
        if not self.flexible():
            return
        try:
            bundle = json.loads((self.project / "Assets/Resources/studio_scenes.json").read_text(encoding="utf-8"))
            marker = json.loads((self.project / "Assets/Resources/studio_dimension.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise UnityFailure("dimension_validation", "Could not read the saved Unity game dimension marker and scene specification.",
                               "infrastructure") from error
        if bundle.get("dimension") != marker.get("dimension"):
            raise UnityFailure("dimension_validation",
                "Saved scene dimension does not match the Unity project dimension selected before generation.", "scene")
        dimension = marker.get("dimension")
        if dimension not in ("2D", "3D", "mixed"):
            raise UnityFailure("dimension_validation", "Saved Unity game dimension is unsupported.", "infrastructure")
        return dimension

    async def run(self, stage, extra, timeout=600, graphics=False):
        mode = os.environ.get("STUDIO_UNITY_MODE", "auto").lower()
        opened = project_is_open(self.project)
        if mode == "auto" and not opened and not mcp_enabled():
            mode = "batch"
        if uses_live_editor(mode, opened):
            if not opened:
                await ensure_live_editor(self.job, self.project, timeout=timeout)
            from studio.unity_live import run_live
            return await run_live(self, stage, extra, timeout)
        if mode != "batch":
            raise UnityFailure(stage, "STUDIO_UNITY_MODE must be auto, batch or live", "infrastructure")
        if opened:
            raise UnityFailure(stage, "Project is already open in Unity. Use STUDIO_UNITY_MODE=live with its MCP bridge; batch mode was not launched.", "infrastructure")
        if not UNITY.is_file():
            raise UnityFailure(stage, "Configured Unity Editor does not exist", "infrastructure")
        self.job.check_cancelled()
        log = self.job.directory / f"{stage}-{time.time_ns()}.log"
        command = [str(UNITY), "-batchmode", "-projectPath", str(self.project), "-logFile", str(log)]
        if not graphics:
            command.append("-nographics")
        command.extend(extra)
        process = await asyncio.create_subprocess_exec(*command, cwd=self.project,
            env={k: v for k, v in os.environ.items() if k not in
                 ("OPENROUTER_API_KEY", "DAHL_API_KEY", "TELEGRAM_BOT_TOKEN")},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        try:
            while process.returncode is None:
                self.job.check_cancelled()
                if available_space(self.project)<250_000_000:
                    raise UnityFailure(stage,"Unity stopped because usable disk space fell below 250 MB", "infrastructure")
                if time.monotonic() > deadline:
                    raise UnityFailure(stage, "Unity stage timed out", "infrastructure")
                try:
                    await asyncio.wait_for(process.wait(), 1)
                except asyncio.TimeoutError:
                    pass
        except BaseException:
            if process.returncode is None:
                if os.name=="nt":
                    stop=await asyncio.create_subprocess_exec("taskkill.exe","/PID",str(process.pid),"/T","/F",
                        stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                    await stop.wait()
                elif process.returncode is None:
                    process.kill()
                await process.wait()
            raise
        output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else "Unity did not write a log"
        write_json(self.job.directory / "unity_licensing.json", licensing_status(output))
        if process.returncode != 0 or re.search(r"error CS\d+|Scripts have compiler errors|Aborting batchmode due to failure|Fatal Error", output):
            lines = [line for line in output.splitlines() if re.search(r"error CS|Exception|Error|error |Aborting|already open|Failed|failed|enough space|Internal build system|backend process", line)]
            owner = unity_failure_owner(stage, output)
            raise UnityFailure(stage, "\n".join(lines)[-14000:] or output[-10000:], owner)
        return output

    async def compile(self):
        output = await self.run("compile", ["-quit"])
        if "Application.AssetDatabase Initial Refresh End" not in output:
            raise UnityFailure("compile", "Unity did not confirm asset import completion", "infrastructure")
        return {"status": "passed", "scope": "C# compilation and asset import only"}

    async def assemble(self):
        path = safe_path(self.project, "Artifacts/assembly.json")
        path.unlink(missing_ok=True)
        expected_dimension = self.validate_dimension()
        await self.run("assembly", ["-quit", "-executeMethod",
            "UniversalSceneBuilder.Assemble" if self.flexible() else "StudioSceneBuilder.Assemble"])
        if not path.is_file():
            raise UnityFailure("assembly", "Unity exited without writing assembly evidence", "scene")
        report = json.loads(path.read_text(encoding="utf-8"))
        if expected_dimension and report.get("dimension") != expected_dimension:
            raise UnityFailure("assembly", "Unity assembly evidence reports a different game dimension", "scene")
        minimum = len(json.loads((self.project / "Assets/Resources/studio_scenes.json").read_text(encoding="utf-8"))["scenes"]) if self.flexible() else 3
        count = report.get("scenes", 0)
        if not report.get("success") or (count != minimum if self.flexible() else count < minimum):
            raise UnityFailure("assembly", report.get("error", "Incomplete scene report"), "scene")
        if len(report.get("scene_paths", [])) != count or len(set(report["scene_paths"])) != count:
            raise UnityFailure("assembly", "Scene evidence has missing or duplicate paths", "scene")
        for scene in report["scene_paths"]:
            if not safe_path(self.project, scene).is_file():
                raise UnityFailure("assembly", "Scene report references a missing file", "scene")
        # MCP inspection is an optional second view. The existing checked Unity
        # assembler remains authoritative when no matching live Editor is open.
        from studio.mcp_client import UnityMCPClient
        client = None
        try:
            client = UnityMCPClient.from_environment()
            if client is not None:
                await client.connect()
                await client.verify_project(self.project)
                # QA uses its read-only scene facade here; the existing CLI
                # builder and its evidence remain authoritative.
                qa_tools = client.for_agent("qa", self.project)
                inspected = await qa_tools.call("manage_scene", {
                    "action": "get_hierarchy", "page_size": 200, "cursor": 0
                })
                log.info("Unity MCP inspected assembled job %s via QA (%d tool results)",
                         self.job.data["id"], 1 if inspected else 0)
        except Exception as error:
            log.info("Unity MCP unavailable for job %s; existing Unity CLI path remains active (%s)",
                     self.job.data["id"], type(error).__name__)
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as error:
                    log.warning("Unity MCP cleanup failed (%s)", type(error).__name__)
        return report

    async def tests(self, mode):
        self.validate_dimension()
        path = safe_path(self.project, "Artifacts/" + mode + ".xml")
        path.unlink(missing_ok=True)
        flexible = self.flexible()
        suite = ("PromptRuntimeTests" if mode == "PlayMode" else "PromptSceneTests") if flexible else ("PlatformerTests" if mode == "PlayMode" else "AssemblyTests")
        runtime = self.project / "Artifacts/runtime_checks.json"
        if flexible and mode == "PlayMode":
            runtime.unlink(missing_ok=True)
        if mode == "PlayMode":
            (self.project / "Artifacts/preview.png").unlink(missing_ok=True)
        await self.run(mode, ["-runTests", "-testPlatform", mode, "-testResults", str(path), "-testFilter", suite], graphics=mode == "PlayMode")
        if not path.is_file():
            raise UnityFailure(mode, "No NUnit test results produced", "infrastructure")
        result = parse_test_report(path, mode, expected=1 if flexible and mode == "PlayMode" else None,
                                   suite=suite if flexible else None)
        if flexible and mode == "PlayMode":
            if not runtime.is_file():
                raise UnityFailure(mode, "No fresh requirement-linked runtime evidence", "qa")
            evidence = json.loads(runtime.read_text(encoding="utf-8"))
            probes = json.loads((self.project / "Assets/Resources/studio_probes.json").read_text(encoding="utf-8"))["probes"]
            checks = evidence.get("checks", [])
            if (len(checks) != len(probes) or {c.get("id") for c in checks} != {p["id"] for p in probes}
                    or any(c.get("passed") is not True for c in checks)):
                raise UnityFailure(mode, "Requirement-linked runtime checks are missing or failed", "qa")
            result["checks"] = checks
        return result

    async def android(self):
        self.validate_dimension()
        if available_space(self.project) < 5_000_000_000:
            raise UnityFailure("android","Android build needs at least 5 GB free disk space for native intermediates", "infrastructure")
        path = safe_path(self.project, "Artifacts/android.json")
        path.unlink(missing_ok=True)
        apk = safe_path(self.project, "Artifacts/prototype.apk")
        apk.unlink(missing_ok=True)
        await self.run("android", ["-quit", "-executeMethod", "StudioSceneBuilder.BuildAndroid"], timeout=1200)
        report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not report.get("success") or not apk.exists() or apk.stat().st_size < 10000:
            raise UnityFailure("android", report.get("error", "No verified APK produced"), "infrastructure")
        import zipfile
        with zipfile.ZipFile(apk) as archive:
            if "AndroidManifest.xml" not in archive.namelist() or archive.testzip() is not None:
                raise UnityFailure("android", "APK integrity validation failed", "infrastructure")
        return {"status": "passed", "path": str(apk), "device_test": "unverified"}


def parse_test_report(path, stage, expected=None, suite=None):
    root = ET.parse(path).getroot()
    cases = root.findall(".//test-case")
    failures = [c for c in cases if c.get("result") != "Passed"]
    expected = expected if expected is not None else 10 if stage == "PlayMode" else 2
    if suite and any(suite not in c.get("fullname", c.get("name", "")) for c in cases):
        raise UnityFailure(stage, "NUnit results do not match the requested test suite", "qa")
    if len(cases) < expected or failures or root.get("result") not in ("Passed", "Success"):
        messages = [f"{c.get('name')}: {c.findtext('.//message', default=c.get('result', 'unknown'))}" for c in failures]
        raise UnityFailure(stage, "\n".join(messages) or f"Expected at least {expected} real tests, found {len(cases)}", "qa")
    return {"status": "passed", "passed": len(cases), "failed": 0, "results": str(path)}
