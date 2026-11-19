"""Reverify saved projects after local changes without repeating paid agents."""
import json
import shutil

from studio.agents import SceneArchitect
from studio.artifacts import package
from studio.config import TEMPLATES
from studio.costs import Ledger
from studio.schemas import CodeBundle, GameSpec, ScriptFile, UIPlan
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
            spec=GameSpec.model_validate_json((job.directory/"game_spec.json").read_text(encoding="utf-8"))
            ui=UIPlan.model_validate_json((job.directory/"ui_plan.json").read_text(encoding="utf-8"))
            manifest=json.loads((job.directory/"art_manifest.json").read_text(encoding="utf-8"))
            art={a["kind"]:a["path"] for a in manifest}
            scenes=SceneArchitect().assemble(spec,ui,art)
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
