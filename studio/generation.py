"""Version-two execution tools owned by the existing five-agent supervisor."""
import json
import re

from studio.artifacts import package
from studio.costs import Ledger
from studio.flexible import Implementation, requirement_report, validate_implementation
from studio.config import safe_path
from studio.state import write_json
from studio.unity import UnityFailure, UnityRunner


def save_implementation(job, project, spec, plan):
    validate_implementation(spec, plan)
    folder = project / "Assets" / "Studio" / "Generated"
    folder.mkdir(parents=True, exist_ok=True)
    banned = ("UnityEditor", "System.IO", "System.Net", "System.Diagnostics", "System.Reflection", "DllImport",
              "unsafe ", "Process.", "Application.OpenURL", "Assembly.Load", "UnityWebRequest", "Microsoft.Win32")
    # This is a scope check, not a security sandbox for hostile code.
    for script in plan.scripts.scripts:
        name = script.filename[:-3]
        if not re.search(r"\bclass\s+" + re.escape(name) + r"\b", script.content):
            raise ValueError("Script filename must match its public component class")
        if any(word in script.content for word in banned) or "#if UNITY_EDITOR" in script.content:
            raise ValueError("Generated gameplay uses APIs outside the runtime scope")
    validate_scene_files(project, plan)
    # Remove only superseded model-owned scripts listed in THIS new job's manifest.
    previous = job.directory / "implementation.json"
    if previous.is_file():
        old = Implementation.model_validate_json(previous.read_text(encoding="utf-8"))
        names = {s.filename for s in plan.scripts.scripts}
        for script in old.scripts.scripts:
            if script.filename not in names:
                safe_path(folder, script.filename).unlink(missing_ok=True)
    for script in plan.scripts.scripts:
        safe_path(folder, script.filename).write_text(script.content, encoding="utf-8")
    write_json(job.directory / "implementation.json", plan.model_dump())
    write_json(job.directory / "scene_spec.json", plan.scenes.model_dump())
    write_json(project / "Assets/Resources/studio_scenes.json", plan.scenes.model_dump())
    write_json(project / "Assets/Resources/game_spec.json", spec.model_dump())
    write_json(project / "Assets/Resources/studio_probes.json", {"probes": [p.model_dump() for p in plan.probes]})
    write_json(job.directory / "requirements.json", requirement_report(spec, plan))


def validate_scene_files(project, plan):
    for scene in plan.scenes.scenes:
        for obj in scene.objects:
            for path in (obj.sprite, obj.model):
                if path and (not path.startswith("Assets/") or not safe_path(project, path).is_file()):
                    raise ValueError("Missing scene asset: " + path)
            for comp in obj.components:
                if comp.type.startswith(("UnityEditor", "System.")):
                    raise ValueError("Scene component is outside runtime scope")
                for prop in comp.properties:
                    if prop.kind == "asset" and (not prop.value.startswith("Assets/") or not safe_path(project, prop.value).is_file()):
                        raise ValueError("Missing component asset: " + prop.value)
    for mat in plan.scenes.materials:
        if mat.texture and (not mat.texture.startswith("Assets/") or not safe_path(project, mat.texture).is_file()):
            raise ValueError("Missing material texture: " + mat.texture)


async def build_flexible(supervisor, service, spec, project, notify, android):
    job = service.job
    report = {"job_id": job.data["id"], "game": spec.name, "original_request": job.data["request"],
              "project": str(project), "spec_version": 2, "stages": {}, "limitations": list(spec.limitations),
              "requirements": requirement_report(spec), "completion_status": "partial"}
    runtime = None
    manifest = []
    plan = None
    def save_report():
        report["requirements"] = requirement_report(spec, plan, runtime, manifest)
        report["costs"] = job.data["costs"]
        report["cost_summary"] = Ledger(job).summary()
        write_json(job.directory / "requirements.json", report["requirements"])
        write_json(job.directory / "validation_report.json", report)
    try:
        # GameSpec v2 already contains genre-specific UI and control requirements.
        # Keep the legacy UIPlan model (with its menu/touch fields) on the v1 path only.
        if getattr(spec, "version", 1) == 2:
            ui = {"ui_requirements": spec.ui_requirements, "controls": spec.controls,
                  "target_platform": spec.target_platform}
        else:
            await supervisor.notify(notify, "Designer: planning UI for the requested game and target.")
            ui = await supervisor.agents["designer"].plan_ui(service, spec)
            ui = ui.model_dump()
        write_json(job.directory / "ui_plan.json", ui)
        job.data["tasks"][2]["status"] = "passed"
        await supervisor.notify(notify, "Art Designer: generating or importing requested assets; unavailable assets will be listed.")
        art = await supervisor.agents["art"].create(service, spec, project)
        manifest = json.loads((job.directory / "art_manifest.json").read_text(encoding="utf-8"))
        write_json(project / "Assets/Resources/studio_art.json", {"assets": manifest})
        job.data["tasks"][3]["status"] = "passed" if all(a["status"] == "ready" for a in manifest) else "partial"
        await supervisor.notify(notify, "Developer: implementing the requested scripts, scenes, components and gameplay checks.")
        plan = await supervisor.agents["developer"].implement(service, spec, ui, art)
        review = await supervisor.agents["qa"].review_implementation(service, spec, plan)
        write_json(job.directory / "qa_review.json", review.model_dump())
        report["qa_review"] = review.model_dump()
        report["limitations"].extend(review.missing_prompt_instructions + review.concerns)
        report["limitations"].extend("QA identified incomplete requirement " + r for r in review.missing_requirement_ids)
        runner = UnityRunner(job, project)
        for attempt in range(4):
            try:
                save_implementation(job, project, spec, plan)
                job.data["tasks"][1]["status"] = job.data["tasks"][4]["status"] = "passed"
                for index, stage, operation in [(5, "compilation", runner.compile), (6, "assembly", runner.assemble),
                        (7, "EditMode", lambda: runner.tests("EditMode")), (8, "PlayMode", lambda: runner.tests("PlayMode"))]:
                    job.check_cancelled()
                    await supervisor.notify(notify, f"Unity QA: {stage}, attempt {attempt + 1}.")
                    report["stages"][stage] = await operation()
                    job.data["tasks"][index]["status"] = "passed"
                    job.event("qa", stage, repair_round=attempt)
                    save_report()
                runtime = json.loads((project / "Artifacts/runtime_checks.json").read_text(encoding="utf-8"))
                break
            except (UnityFailure, ValueError) as error:
                owner = error.owner if isinstance(error, UnityFailure) else "developer"
                report.setdefault("repair_history", []).append({"round": attempt, "error": str(error), "owner": owner})
                save_report()
                if owner == "infrastructure" or attempt == 3:
                    raise
                job.data["repairs"] = attempt + 1
                await supervisor.notify(notify, "QA: repairing against the saved diagnostics, preserving the test plan.")
                plan = await supervisor.agents["qa"].repair_implementation(service, spec, plan, str(error))
        report["limitations"].extend(plan.limitations)
        report["limitations"].extend(a.get("reason", "Unavailable asset: " + a["id"]) for a in manifest if a["status"] != "ready")
        queued = [item["text"] for item in job.data.get("additional_instructions", []) if item.get("status") == "queued"]
        if queued:
            report["queued_instructions"] = queued
            report["limitations"].append("These user instructions were saved but arrived after planning; they were not applied to this build: " + " | ".join(queued))
        report["android"] = {"status": "not_requested", "device_test": "unverified"}
        if android and spec.target_platform.casefold() == "android":
            try:
                report["android"] = await runner.android()
            except UnityFailure as error:
                report["android"] = {"status": "failed", "error": str(error), "device_test": "unverified"}
                report["limitations"].append("Android build failed: " + str(error))
        elif android:
            report["limitations"].append("Android build not performed: requested target is " + spec.target_platform)
        report["gameplay"] = "Only the linked runtime probes passed; untested mechanics and visual quality remain unverified."
        save_report()
        complete = all(r["verification_result"] == "passed" and r["implementation_status"] != "incomplete"
                       for r in report["requirements"]) and not report["limitations"]
        report["completion_status"] = "completed" if complete else "partial"
        save_report()
        artifacts = package(job, project, report)
        job.data["status"] = report["completion_status"]
        job.data["tasks"][9]["status"] = "passed"
        job.event("supervisor", job.data["status"])
        update_notice = ("\nQueued instruction(s) were saved but not applied to this build: " + " | ".join(queued)
                         if queued else "")
        await supervisor.notify(notify, f"{job.data['status'].capitalize()} prototype: {project}\n"
            "Use /result for the project and requirement-by-requirement evidence. "
            "Partial means some requested work or review remains unfinished.\n" + Ledger(job).summary() + update_notice)
        return artifacts
    except Exception as error:
        report["verification_error"] = str(error)
        save_report()
        raise
