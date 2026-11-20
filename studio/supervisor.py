import asyncio
import json

from studio.agents import GameDesigner, QADebugger, UnityDeveloper, demo_spec
from studio.api import ModelService
from studio.artifacts import package
from studio.assets import ArtDesigner
from studio.config import BUILD_ANDROID, DEFAULT_CAP, JOBS
from studio.costs import BudgetExceeded, Ledger
from studio.registry import Registry
from studio.request_validation import NOISE
from studio.schemas import GameSpec, UIPlan
from studio.flexible import load_game_spec, requirement_report
from studio.state import Job, JobCancelled, ProcessLock, write_json
from studio.unity import (RUNTIME_CONTRACT, UnityFailure, UnityRunner, bootstrap,
    ensure_live_editor, project_is_open, save_scripts, uses_live_editor, validate_files)

TASKS = [
    ("designer", "game_spec", []), ("developer", "scripts", ["game_spec"]),
    ("designer", "ui_plan", ["game_spec"]), ("art", "sprites", ["game_spec"]),
    ("developer", "scene_spec", ["scripts", "ui_plan", "sprites"]),
    ("qa", "compilation", ["scene_spec"]), ("developer", "assembly", ["compilation"]),
    ("qa", "EditMode", ["assembly"]), ("qa", "PlayMode", ["EditMode"]),
    ("supervisor", "artifacts", ["PlayMode"]),
]


class Supervisor:
    def __init__(self, root=JOBS):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = Registry()
        # Exactly five agent identities, including this supervisor. Tools and
        # model task profiles are not registered as agents.
        self.agents = {
            "supervisor": self,
            "designer": GameDesigner(),
            "developer": UnityDeveloper(),
            "art": ArtDesigner(),
            "qa": QADebugger(),
        }
        self.job = None
        marker = self.root / "current.txt"
        if marker.exists():
            path = self.root / marker.read_text().strip() / "job.json"
            if path.exists():
                self.job = Job.load(path)
                if self.job.data["status"] in ("planning", "building"):
                    self.job.data["status"] = "interrupted"
                    self.job.data["error"] = "Process stopped before completion. The saved job will resume when the bot starts."
                    self.job.save()
        self.lock = asyncio.Lock()

    async def notify(self, callback, text):
        if callback:
            try:
                await callback(text)
            except Exception as error:
                if self.job:
                    self.job.event("delivery", "notification_failed", error=type(error).__name__)

    def busy(self):
        return self.lock.locked() or (self.job is not None and self.job.data["status"] in ("planning", "building", "awaiting_approval"))

    def add_instruction(self, instruction):
        """Attach a user update to the active job without replacing its original prompt."""
        text = instruction.strip()
        if not text or len(text) > 4000 or text.casefold() in NOISE:
            return "That update is empty, too long, or not a game instruction."
        job = self.job
        if not job or job.data.get("status") not in ("planning", "building", "awaiting_approval", "interrupted"):
            return "There is no active game run to update."
        entries = job.data.setdefault("additional_instructions", [])
        stage = job.data.get("status")
        instruction_status = ("planning" if stage == "planning" else
                              "pending_approval" if stage in ("awaiting_approval", "interrupted") else "queued")
        entry = {"text": text, "status": instruction_status}
        entries.append(entry)
        job.data["instruction_revision"] = int(job.data.get("instruction_revision", 0)) + 1
        job.save()
        if entry["status"] == "planning":
            return "Added to the current planning prompt. Your original game requirements are preserved."
        if entry["status"] == "pending_approval":
            return "Saved with this game plan. It will be included when you send /approve."
        return "Saved with this job. The Unity build has already started, so this update was queued and was not applied to the current build."

    async def submit(self, request, chat_id=None, notify=None, demo=False, explicit_create=True,
                     initial_instructions=None, android=BUILD_ANDROID):
        if self.busy():
            raise RuntimeError("There is already an active game run. Use /status or /cancel.")
        if not request.strip() or len(request)>4000:
            raise ValueError("Describe a specific game in 4,000 characters or fewer")
        async with self.lock:
            job = Job.create(request.strip(), DEFAULT_CAP, chat_id, self.root)
            self.job = job
            (self.root / "current.txt").write_text(job.data["id"], encoding="utf-8")
            job.data["tasks"] = [dict(role=role, name=name, dependencies=deps, status="pending") for role,name,deps in TASKS]
            job.data["additional_instructions"] = [dict(text=value.strip(), status="planning")
                for value in (initial_instructions or []) if value and value.strip()]
            job.data["instruction_revision"] = len(job.data["additional_instructions"])
            job.save()
            try:
                if request.strip().casefold() in NOISE:
                    job.data["status"]="rejected"; job.data["error"]="I can only create Unity games. Describe a specific game."
                    job.save(); return job
                await self.registry.refresh()
                service = ModelService(self.registry, job)
                # /create is an explicit instruction to build a game. Keep the
                # classifier only for ordinary chat messages; ambiguity defaults
                # to creation and never opens a clarification turn.
                if not demo and not explicit_create:
                    decision = await service.json("supervisor",
                        "Classify an ordinary chat message for a Unity game studio. Return {decision: create|reject, reason: string}. "
                        "Reject only greetings, noise, and clearly unrelated non-game tasks. If it may describe a game, "
                        "create one and use sensible defaults; never ask a question or return clarify. Preserve its genre, "
                        "dimension, mechanics, style, controls and target. Do not replace it with a platformer.",
                        {"message": request, "explicit_create": False}, 500)
                    if decision.get("decision") == "reject":
                        job.data["status"] = "rejected"
                        job.data["error"] = "I only create Unity games. Use /create <game description>."
                        job.save()
                        return job
                await self.notify(notify, "Designer: preparing a game plan within the $2 job cap.")
                spec = demo_spec() if demo else None
                if not demo:
                    # Incorporate messages received while planning, retrying only
                    # when the prompt changed during an in-flight model call.
                    for attempt in range(3):
                        revision = int(job.data.get("instruction_revision", 0))
                        pending = [item for item in job.data.get("additional_instructions", [])
                                   if item.get("status") == "planning"]
                        additions = [item["text"] for item in pending]
                        effective_request = request.strip()
                        if additions:
                            effective_request += "\n\nAdditional user instructions (preserve the original request too):\n" + "\n".join("- " + item for item in additions)
                        job.data["effective_request"] = effective_request
                        job.save()
                        spec = await self.agents["designer"].plan(service, effective_request)
                        if int(job.data.get("instruction_revision", 0)) == revision:
                            for item in pending:
                                item["status"] = "applied"
                            break
                        if attempt == 2:
                            # Keep late changes on the job; do not silently claim
                            # they influenced a plan generated from an older prompt.
                            for item in pending:
                                item["status"] = "applied"
                            for item in job.data.get("additional_instructions", [])[revision:]:
                                item["status"] = "queued"
                        job.save()
                job.check_cancelled()
                # Some providers still populate the legacy clarification field.
                # Missing details use planner defaults; this field never blocks a run.
                if getattr(spec, "version", 1) == 2:
                    spec.clarification_questions = []
                write_json(job.directory / "game_spec.json", spec.model_dump())
                if spec.version == 2:
                    write_json(job.directory / "requirements.json", requirement_report(spec))
                job.data["status"] = "awaiting_approval"
                job.data["tasks"][0]["status"] = "passed"
                job.event("designer", "plan_ready", name=spec.name)
                await self.notify(notify, self.plan_text() + "\n\nSend /approve to start Unity generation, /cancel to discard this plan, or /budget to inspect spending.")
            except Exception as error:
                self.fail(job,error)
                await self.notify(notify, f"Planning stopped: {job.data['error']}")
        return job

    def plan_text(self):
        if not self.job or not (self.job.directory/"game_spec.json").exists():
            return "No saved game plan. Use /create <description>."
        spec = load_game_spec((self.job.directory/"game_spec.json").read_text(encoding="utf-8"))
        if spec.version == 2:
            return (f"{spec.name} — {spec.genre}, {spec.dimension}\n{spec.target_platform}, {spec.camera_perspective}\n"
                + "Mechanics: " + ", ".join(spec.mechanics) + "\nControls: " + ", ".join(spec.controls)
                + "\nScenes: " + ", ".join(spec.scenes) + "\nArt: " + spec.art_style
                + "\nRequirements:\n" + "\n".join(f"{r.id}: {r.original_instruction}" for r in spec.requirements)
                + "\nAssumptions: " + "; ".join(spec.assumptions)
                + "\nKnown limitations: " + ("; ".join(spec.limitations) or "Completion depends on Unity and requirement checks.")
                + "\n" + Ledger(self.job).summary())
        return (f"{spec.name} — {spec.genre}\n{spec.target_platform}, {spec.orientation}\n"
                f"Mechanics: {', '.join(spec.player_mechanics)}\n"
                f"Levels: {', '.join(l.name for l in spec.levels)}\n"
                f"Art: {spec.art_style}\nQA: compilation, assembly, EditMode and PlayMode tests\n"
                + Ledger(self.job).summary())

    def cancel(self):
        if not self.job:
            return "No active job."
        if self.job.data["status"] not in ("planning","building","awaiting_approval","interrupted"):
            return "No active job to cancel."
        self.job.data["cancel_requested"]=True
        if self.job.data["status"] in ("awaiting_approval", "interrupted"):
            self.job.data["status"]="cancelled"
        self.job.save()
        return "Cancellation requested. Active Unity work will stop; completed API charges remain recorded."

    def fail(self, job, error):
        status = "cancelled" if isinstance(error,JobCancelled) or job.data.get("cancel_requested") else "budget_stopped" if isinstance(error,BudgetExceeded) else "failed"
        job.data["status"] = status
        # Errors generated by our bounded services do not include credentials.
        message = str(error) if isinstance(error,(UnityFailure,BudgetExceeded,JobCancelled,ValueError,RuntimeError)) else type(error).__name__
        job.data["error"] = message[:14000]
        job.event("supervisor", status, error=message[:14000])

    async def approve(self, notify=None, android=BUILD_ANDROID):
        job = self.job
        if self.lock.locked():
            raise RuntimeError("A studio operation is already running")
        if job and job.data.get("status") == "failed" and (job.directory / "game_spec.json").is_file():
            # A failed build can be retried from its saved specification without
            # charging for planning again. Jobs that failed before saving a spec
            # (for example a malformed planner response) remain non-resumable.
            job.data["status"] = "awaiting_approval"
            job.data["error"] = ""
            job.save()
        if not job or job.data["status"] not in ("awaiting_approval", "interrupted"):
            raise RuntimeError("No saved plan waiting for approval")
        async with self.lock:
            try:
                with ProcessLock(self.root / ".unity.lock"):
                    job.check_cancelled()
                    # Approval may arrive long after planning; reprice before work.
                    await self.registry.refresh()
                    service=ModelService(self.registry,job)
                    spec=load_game_spec((job.directory/"game_spec.json").read_text(encoding="utf-8"))
                    job.data["resume_implementation"] = (job.directory / "implementation.json").is_file()
                    if spec.version == 2:
                        base_request=job.data.get("effective_request",job.data["request"])
                        for attempt in range(3):
                            revision=int(job.data.get("instruction_revision",0))
                            pending=[item for item in job.data.get("additional_instructions",[])
                                     if item.get("status")=="pending_approval"]
                            effective_request=base_request
                            if pending:
                                job.data["resume_implementation"] = False
                                effective_request += "\n\nAdditional user instructions (preserve the original request too):\n" + "\n".join("- "+item["text"] for item in pending)
                                spec=await self.agents["designer"].plan(service,effective_request)
                                if int(job.data.get("instruction_revision",0))==revision:
                                    for item in pending: item["status"]="applied"
                                    job.data["effective_request"]=effective_request
                                    break
                                if attempt==2:
                                    for item in pending: item["status"]="applied"
                                    for item in job.data.get("additional_instructions",[])[revision:]:
                                        item["status"]="queued"
                                    job.data["effective_request"]=effective_request
                            else:
                                break
                        spec.clarification_questions=[]
                        write_json(job.directory/"game_spec.json",spec.model_dump())
                        write_json(job.directory/"requirements.json",requirement_report(spec))
                    else:
                        for item in job.data.get("additional_instructions",[]):
                            if item.get("status")=="pending_approval": item["status"]="queued"
                    job.data["status"]="building"; job.save()
                    import os
                    mode = os.environ.get("STUDIO_UNITY_MODE", "auto").lower()
                    project_path = job.directory / "UnityProject"
                    opened_before = project_is_open(project_path)
                    if mode not in ("auto", "live", "batch"):
                        raise UnityFailure("project_setup", "STUDIO_UNITY_MODE must be auto, batch or live", "infrastructure")
                    if mode == "batch" and opened_before:
                        raise UnityFailure("project_setup", "Batch mode cannot run against an open Unity project", "infrastructure")
                    use_live_editor = uses_live_editor(mode, opened_before)
                    project=await asyncio.to_thread(bootstrap,job)
                    if use_live_editor:
                        if not project_is_open(project):
                            await self.notify(notify, "Opening this approved game project in the Unity Editor and connecting its MCP bridge.")
                        else:
                            await self.notify(notify, "Connecting to the Unity Editor already open on this approved project.")
                        await ensure_live_editor(job, project)
                        await self.notify(notify, "Unity is connected. The approved game will now be built and verified in this open Editor.")
                    await service.unity_context("supervisor")
                    if spec.version == 2:
                        from studio.generation import build_flexible
                        return await build_flexible(self, service, spec, project, notify, android)
                    await self.notify(notify,"Developer: generating rules and installing tested platformer systems.")
                    scripts=await self.agents["developer"].generate(service,spec,RUNTIME_CONTRACT)
                    save_scripts(project,scripts)
                    job.data["tasks"][1]["status"]="passed"; job.event("developer","scripts_saved",files=[s.filename for s in scripts.scripts])
                    ui=await self.agents["designer"].plan_ui(service,spec)
                    write_json(job.directory/"ui_plan.json",ui.model_dump())
                    job.data["tasks"][2]["status"]="passed"
                    await self.notify(notify,"Art director: generating and validating sprite PNGs; original procedural fallback is available.")
                    art=await self.agents["art"].create(service,spec,project)
                    manifest=json.loads((job.directory/"art_manifest.json").read_text(encoding="utf-8"))
                    write_json(project/"Assets/Resources/studio_art.json",{"assets":manifest})
                    job.data["tasks"][3]["status"]="passed"
                    scenes=self.agents["developer"].assemble_scenes(spec,ui,art)
                    write_json(job.directory/"scene_spec.json",scenes.model_dump())
                    write_json(project/"Assets/Resources/studio_scenes.json",scenes.model_dump())
                    validate_files(project,scenes,scripts)
                    job.data["tasks"][4]["status"]="passed"; job.event("developer","scene_spec_validated",scenes=len(scenes.scenes))
                    runner=UnityRunner(job,project)
                    report={"job_id":job.data["id"],"game":spec.name,"project":str(project),"stages":{},"limitations":[
                        "MVP supports only 2D platformers; generated gameplay extensions are deliberately small.",
                        "Automated checks cover defined prototype mechanics; aesthetic quality and real-device UX require human review."]}
                    if any(a["source"] != "OpenRouter image generation" for a in manifest):
                        report["limitations"].append("One or more assets use the original procedural fallback; see art_manifest.json.")
                    if job.data.get("image_service_blocked"):
                        report["limitations"].append(job.data["image_service_blocked"])
                    for attempt in range(4):
                        try:
                            for task_index,stage,operation in [(5,"compilation",runner.compile),(6,"assembly",runner.assemble),
                                    (7,"EditMode",lambda:runner.tests("EditMode")),(8,"PlayMode",lambda:runner.tests("PlayMode"))]:
                                job.check_cancelled(); job.event("qa",stage,repair_round=attempt)
                                await self.notify(notify,f"Unity QA: {stage} ({'initial run' if attempt==0 else 'repair round '+str(attempt)}).")
                                report["stages"][stage]=await operation()
                                job.data["tasks"][task_index]["status"]="passed"; job.save()
                            break
                        except UnityFailure as error:
                            stage_name="compilation" if error.stage=="compile" else error.stage
                            report["stages"][stage_name]={"status":"failed","error":str(error),"owner":error.owner}
                            report.setdefault("repair_history",[]).append({"round":attempt,"stage":stage_name,"error":str(error),"owner":error.owner})
                            for task in job.data["tasks"]:
                                if task["name"]==stage_name: task["status"]="failed"
                            job.save()
                            write_json(job.directory/"validation_report.json",report)
                            if error.owner=="infrastructure" or attempt>=3:
                                raise
                            job.data["repairs"]=attempt+1
                            for task in job.data["tasks"]:
                                if task["name"] in ("compilation","assembly","EditMode","PlayMode"): task["status"]="pending"
                            await self.notify(notify,"QA found a failure. Routing a bounded repair to "+error.owner+".")
                            if error.owner=="scene":
                                scenes=await self.agents["developer"].repair_scenes(service,spec,scenes,str(error))
                                validate_files(project,scenes,scripts)
                                write_json(job.directory/"scene_spec.json",scenes.model_dump())
                                write_json(project/"Assets/Resources/studio_scenes.json",scenes.model_dump())
                            else:
                                scripts=await self.agents["qa"].repair(service,spec,str(error),scripts.model_dump(),RUNTIME_CONTRACT)
                                save_scripts(project,scripts)
                    report["android"]={"status":"not_requested","device_test":"unverified"}
                    if android:
                        await self.notify(notify,"Android: attempting a development APK build.")
                        try:
                            report["android"]=await runner.android()
                        except UnityFailure as error:
                            report["android"]={"status":"failed","error":str(error),"device_test":"unverified"}
                    queued = [item["text"] for item in job.data.get("additional_instructions", []) if item.get("status") == "queued"]
                    if queued:
                        report["queued_instructions"] = queued
                        report["limitations"].append("These user instructions were saved but arrived after planning; they were not applied to this build: " + " | ".join(queued))
                    report["costs"]=job.data["costs"]
                    report["cost_summary"]=Ledger(job).summary()
                    report["gameplay"]="passed automated platformer suite; real-device play unverified"
                    job.check_cancelled()
                    artifacts=await asyncio.to_thread(package,job,project,report)
                    job.data["tasks"][9]["status"]="passed"
                    job.data["status"]="completed"; job.event("supervisor","completed")
                    update_notice = ("\nSaved but not applied during this build: " + " | ".join(queued) if queued else "")
                    await self.notify(notify,f"Prototype verified. Project: {project}\nEditMode and PlayMode results are attached to /result.\n"+Ledger(job).summary()+"\nUse /result for the project, report and preview."+update_notice)
                    return artifacts
            except Exception as error:
                self.fail(job,error)
                await self.notify(notify,f"Job {job.data['status']}: {job.data['error'][:1800]}\n"+Ledger(job).summary())
                return []
