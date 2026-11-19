"""Reverify saved projects after local changes without repeating paid agents."""
import json
import shutil

from studio.artifacts import package
from studio.config import TEMPLATES
from studio.costs import Ledger
from studio.schemas import CodeBundle, GameSpec, ScriptFile, UIPlan
from studio.flexible import load_game_spec
from studio.state import ProcessLock, write_json
from studio.unity import UnityFailure, UnityRunner, validate_files


async def verify(supervisor, notify, android=False):
    job=supervisor.job
    if not job: raise RuntimeError("No saved project")
    project=job.directory/"UnityProject"
    if not project.exists(): raise RuntimeError("No generated Unity project")
    report={"job_id":job.data["id"],"game":"","project":str(project),"stages":{},"limitations":[
        "MVP supports 2D platformers only.","Real-device UX and aesthetic quality require human review."]}
    try:
        with ProcessLock(supervisor.root/".unity.lock"):
            job.check_cancelled(); job.data["status"]="building"; job.save()
            for source in TEMPLATES.rglob("*"):
                if source.is_file():
                    target=project/"Assets/Studio"/source.relative_to(TEMPLATES)
                    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target)
            spec=load_game_spec((job.directory/"game_spec.json").read_text(encoding="utf-8"))
            if spec.version == 2:
                return await verify_flexible(supervisor, spec, project, notify, android)
            ui=UIPlan.model_validate_json((job.directory/"ui_plan.json").read_text(encoding="utf-8"))
            manifest=json.loads((job.directory/"art_manifest.json").read_text(encoding="utf-8"))
            art={a["kind"]:a["path"] for a in manifest}
            scenes=supervisor.agents["developer"].assemble_scenes(spec,ui,art)
            write_json(job.directory/"scene_spec.json",scenes.model_dump())
            write_json(project/"Assets/Resources/studio_scenes.json",scenes.model_dump())
            scripts=CodeBundle(scripts=[ScriptFile(filename="GeneratedRules.cs",content=(project/"Assets/Studio/Generated/GeneratedRules.cs").read_text())])
            validate_files(project,scenes,scripts)
            report["game"]=spec.name
            if any(a["source"]!="OpenRouter image generation" for a in manifest):
                report["limitations"].append("Original procedural sprites used: image service rejected requests (HTTP 402, credits required) in the demonstration.")
            runner=UnityRunner(job,project)
            for name,operation in [("compilation",runner.compile),("assembly",runner.assemble),
                    ("EditMode",lambda:runner.tests("EditMode")),("PlayMode",lambda:runner.tests("PlayMode"))]:
                job.event("qa",name); await notify("Reverification: "+name)
                report["stages"][name]=await operation()
            report["android"]={"status":"not_requested","device_test":"unverified"}
            if android:
                await notify("Android development build: checking the installed SDK and IL2CPP toolchain.")
                try: report["android"]=await runner.android()
                except UnityFailure as error:
                    report["android"]={"status":"failed","error":str(error),"device_test":"unverified"}
            report["gameplay"]="passed automated platformer suite; real-device play unverified"
            report["costs"]=job.data["costs"]; report["cost_summary"]=Ledger(job).summary()
            job.check_cancelled(); package(job,project,report)
            job.data["status"]="completed"; job.event("supervisor","completed")
            await notify("Saved project reverified. "+Ledger(job).summary())
    except Exception as error:
        report["verification_error"]=str(error); write_json(job.directory/"validation_report.json",report)
        supervisor.fail(job,error); await notify("Verification failed: "+job.data["error"][:1800])
    return report


async def verify_flexible(supervisor, spec, project, notify, android):
    from studio.flexible import Implementation, requirement_report
    from studio.generation import save_implementation
    job = supervisor.job
    plan = Implementation.model_validate_json((job.directory / "implementation.json").read_text(encoding="utf-8"))
    # Respect local script edits during explicit CLI reverification.
    for script in plan.scripts.scripts:
        script.content = (project / "Assets/Studio/Generated" / script.filename).read_text(encoding="utf-8")
    save_implementation(job, project, spec, plan)
    previous = job.directory / "validation_report.json"
    report = json.loads(previous.read_text(encoding="utf-8")) if previous.is_file() else {}
    report.update(job_id=job.data["id"], game=spec.name, spec_version=2, project=str(project), stages={}, completion_status="partial")
    report.pop("verification_error", None)
    report.setdefault("limitations", list(spec.limitations) + list(plan.limitations))
    runner = UnityRunner(job, project)
    for name, operation in [("compilation", runner.compile), ("assembly", runner.assemble),
            ("EditMode", lambda: runner.tests("EditMode")), ("PlayMode", lambda: runner.tests("PlayMode"))]:
        await supervisor.notify(notify, "Reverification: " + name)
        report["stages"][name] = await operation()
    runtime = json.loads((project / "Artifacts/runtime_checks.json").read_text(encoding="utf-8"))
    manifest = json.loads((job.directory / "art_manifest.json").read_text(encoding="utf-8"))
    report["requirements"] = requirement_report(spec, plan, runtime, manifest)
    write_json(job.directory / "requirements.json", report["requirements"])
    report["android"] = {"status": "not_requested", "device_test": "unverified"}
    if android and spec.target_platform.casefold() == "android":
        report["android"] = await runner.android()
    if all(r["verification_result"] == "passed" and r["implementation_status"] != "incomplete" for r in report["requirements"]) and not report["limitations"]:
        report["completion_status"] = "completed"
    report["costs"] = job.data["costs"]; report["cost_summary"] = Ledger(job).summary()
    package(job, project, report)
    job.data["status"] = report["completion_status"]; job.event("supervisor", job.data["status"])
    await supervisor.notify(notify, "Saved prototype reverified: " + job.data["status"])
    return report
