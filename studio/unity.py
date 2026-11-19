"""Unity CLI stages require fresh machine-readable evidence, not exit code alone."""
import asyncio
import json
import re
import shutil
import time
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from studio.config import ROOT, TEMPLATES, UNITY, safe_path
from studio.schemas import CodeBundle, SceneBundle
from studio.state import JobCancelled, write_json

RUNTIME_CONTRACT = """Studio.Runtime.LevelRules : MonoBehaviour has virtual Configure(PlayerMotor player), virtual int CoinPoints().
PlayerMotor has public float moveSpeed, jumpSpeed; Rigidbody2D Body; bool Grounded; bool TryJump().
GameDirector has public void AddCoin(); int Score; int Coins. All Unity 6 Input System, mobile UI and core gameplay
are supplied in the Studio.Runtime assembly. GeneratedRules.cs derives from LevelRules and configures movement and coin points."""


class UnityFailure(RuntimeError):
    def __init__(self, stage, message, owner="developer"):
        super().__init__(message)
        self.stage, self.owner = stage, owner


def available_space(path):
    """Honor Windows user quotas, not just the volume's total free bytes."""
    if os.name=="nt":
        import ctypes
        available=ctypes.c_ulonglong()
        if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(str(Path(path).resolve()),ctypes.byref(available),None,None):
            raise OSError("Could not determine disk space available to this user")
        return available.value
    return shutil.disk_usage(path).free


def bootstrap(job):
    if available_space(job.directory) < 1_500_000_000:
        raise UnityFailure("project_setup","At least 1.5 GB free disk space is required for a new Unity project", "infrastructure")
    project = job.directory / "UnityProject"
    project.mkdir(exist_ok=True)
    if not (project / "ProjectSettings").exists():
        for name in ("ProjectSettings", "Packages"):
            shutil.copytree(ROOT / "generated_game" / name, project / name)
        settings = ROOT / "generated_game" / "Assets" / "Settings"
        if settings.exists():
            shutil.copytree(settings, project / "Assets" / "Settings")
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
    (project / "Assets" / "Resources").mkdir(parents=True, exist_ok=True)
    (project / "Assets" / "Studio" / "Generated").mkdir(exist_ok=True)
    job.event("developer", "project_initialized", project=str(project.relative_to(ROOT)))
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

    async def run(self, stage, extra, timeout=600, graphics=False):
        if not UNITY.is_file():
            raise UnityFailure(stage, "Configured Unity Editor does not exist", "infrastructure")
        self.job.check_cancelled()
        log = self.job.directory / f"{stage}-{time.time_ns()}.log"
        command = [str(UNITY), "-batchmode", "-projectPath", str(self.project), "-logFile", str(log)]
        if not graphics:
            command.append("-nographics")
        command.extend(extra)
        process = await asyncio.create_subprocess_exec(*command, cwd=self.project,
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
        if process.returncode != 0 or re.search(r"error CS\d+|Scripts have compiler errors|Aborting batchmode due to failure|Fatal Error", output):
            lines = [line for line in output.splitlines() if re.search(r"error CS|Exception|Error|error |Aborting|already open|Failed|failed|enough space|Internal build system|backend process", line)]
            owner="scene" if stage=="assembly" else "qa" if stage in ("EditMode","PlayMode") else "developer"
            if re.search(r"Assets[\\/]Studio[\\/](Runtime|Editor|Tests)[\\/].*error CS",output): owner="infrastructure"
            if "another Unity instance" in output or "already open" in output: owner="infrastructure"
            if any(marker in output for marker in ("not enough space", "Internal build system error", "The backend process appears", "No space left")): owner="infrastructure"
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
        try:
            await self.run("assembly", ["-quit", "-executeMethod", "StudioSceneBuilder.Assemble"])
        except UnityFailure:
            if not path.is_file(): raise
        if not path.is_file():
            raise UnityFailure("assembly", "Unity exited without writing assembly evidence", "scene")
        report = json.loads(path.read_text(encoding="utf-8"))
        if not report.get("success") or report.get("scenes", 0) < 3:
            raise UnityFailure("assembly", report.get("error", "Incomplete scene report"), "scene")
        for scene in report["scene_paths"]:
            if not safe_path(self.project, scene).is_file():
                raise UnityFailure("assembly", "Scene report references a missing file", "scene")
        return report

    async def tests(self, mode):
        path = safe_path(self.project, "Artifacts/" + mode + ".xml")
        path.unlink(missing_ok=True)
        try:
            await self.run(mode, ["-runTests", "-testPlatform", mode, "-testResults", str(path), "-testFilter", "PlatformerTests" if mode == "PlayMode" else "AssemblyTests"], graphics=mode == "PlayMode")
        except UnityFailure:
            if not path.is_file(): raise
        if not path.is_file():
            raise UnityFailure(mode, "No NUnit test results produced", "infrastructure")
        return parse_test_report(path, mode)

    async def android(self):
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


def parse_test_report(path, stage):
    root = ET.parse(path).getroot()
    cases = root.findall(".//test-case")
    failures = [c for c in cases if c.get("result") != "Passed"]
    expected = 10 if stage == "PlayMode" else 2
    if len(cases) < expected or failures or root.get("result") not in ("Passed", "Success"):
        messages = [f"{c.get('name')}: {c.findtext('.//message', default=c.get('result', 'unknown'))}" for c in failures]
        raise UnityFailure(stage, "\n".join(messages) or f"Expected at least {expected} real tests, found {len(cases)}", "qa")
    return {"status": "passed", "passed": len(cases), "failed": 0, "results": str(path)}
