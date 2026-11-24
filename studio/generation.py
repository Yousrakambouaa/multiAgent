"""Version-two execution tools owned by the existing five-agent supervisor."""
import json
import re
import asyncio
from contextlib import suppress

from studio.artifacts import package
from studio.costs import Ledger
from studio.flexible import (FlexibleComponent, FlexibleObject, FlexibleProperty, Implementation,
                             PlanReview, requirement_report, validate_implementation)
from studio.config import safe_path
from studio.state import write_json
from studio.unity import UnityFailure, UnityRunner, compiler_errors
from studio.coordination import finish_stage, invalidate_validation, update_work_items
from studio.failures import model_quality_report
from studio.assets import asset_summary


async def with_progress(supervisor, notify, operation, message, interval=30):
    """Keep a long metered operation visible and cancel it with its caller."""
    task = asyncio.ensure_future(operation)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                return await task
            await supervisor.notify(notify, message)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


_PUBLIC_COMPONENT_FIELD = re.compile(
    r"\bpublic\s+(?P<type>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=[^;]+)?;"
)


def autowire_named_component_references(plan):
    """Connect public component fields to same-named, explicitly declared scene objects."""
    changes = []
    for scene in plan.scenes.scenes:
        component_names = {component.type.rsplit(".", 1)[-1].casefold()
                           for obj in scene.objects for component in obj.components}
        if component_names.intersection({"button", "touchpad", "eventtrigger"}):
            event_objects = [obj for obj in scene.objects if any(
                component.type.rsplit(".", 1)[-1].casefold() == "eventsystem"
                for component in obj.components)]
            event_object = event_objects[0] if event_objects else None
            if event_object is None:
                used_ids = {obj.id.casefold() for obj in scene.objects}
                event_id, suffix = "studioEventSystem", 1
                while event_id.casefold() in used_ids:
                    event_id, suffix = f"studioEventSystem{suffix}", suffix + 1
                used_names = {obj.name.casefold() for obj in scene.objects}
                event_name, suffix = "Studio EventSystem", 1
                while event_name.casefold() in used_names:
                    event_name, suffix = f"Studio EventSystem {suffix}", suffix + 1
                event_object = FlexibleObject(id=event_id, name=event_name, position=(0, 0, 0), components=[])
                scene.objects.append(event_object)
                changes.append({"scene": scene.name, "object": event_id, "component": "EventSystem",
                                "field": "scene_ui_event_support", "target": event_id})
            present = {component.type.rsplit(".", 1)[-1].casefold() for component in event_object.components}
            required = (
                ("eventsystem", "UnityEngine.EventSystems.EventSystem"),
                ("inputsystemuiinputmodule", "UnityEngine.InputSystem.UI.InputSystemUIInputModule"),
            )
            for simple_name, full_name in required:
                if simple_name not in present:
                    event_object.components.append(FlexibleComponent(type=full_name))
                    changes.append({"scene": scene.name, "object": event_object.id, "component": full_name,
                                    "field": "scene_ui_event_support", "target": event_object.id})
        objects_by_id = {obj.id.casefold(): obj for obj in scene.objects}
        objects_by_name = {}
        for obj in scene.objects:
            objects_by_name.setdefault(obj.name.casefold(), []).append(obj)
        for owner in scene.objects:
            for component in owner.components:
                script = next((item for item in plan.scripts.scripts
                    if item.filename[:-3].casefold() == component.type.rsplit(".", 1)[-1].casefold()), None)
                if script is None:
                    continue
                existing = {prop.name.casefold() for prop in component.properties}
                for field in _PUBLIC_COMPONENT_FIELD.finditer(script.content):
                    field_type, field_name = field.group("type"), field.group("name")
                    type_name = field_type.rsplit(".", 1)[-1]
                    if field_name.casefold() in existing:
                        continue
                    target = objects_by_id.get(field_name.casefold())
                    if target is None:
                        matches = objects_by_name.get(field_name.casefold(), [])
                        target = matches[0] if len(matches) == 1 else None
                    if target is None:
                        continue
                    if type_name == "GameObject":
                        target_component = None
                    elif type_name in ("Transform", "RectTransform"):
                        target_component = "UnityEngine." + type_name
                    else:
                        matches = [item.type for item in target.components
                                   if item.type.rsplit(".", 1)[-1].casefold() == type_name.casefold()]
                        target_component = matches[0] if len(matches) == 1 else None
                    if type_name != "GameObject" and target_component is None:
                        continue
                    value = target.id if target_component is None else target.id + ":" + target_component
                    component.properties.append(FlexibleProperty(
                        name=field_name, kind="reference", value=value))
                    existing.add(field_name.casefold())
                    changes.append({"scene": scene.name, "object": owner.id, "component": component.type,
                                    "field": field_name, "target": target.id})
    return changes


def normalize_ground_check_anchors(plan):
    """Place referenced 2D ground probes just below their actor in world space.

    The Unity scene builder preserves world positions when parenting. Model output
    commonly supplies a local offset for a parented GroundCheck, leaving it near
    the actor's center. Normalize only explicit GroundCheck references on objects
    with a 2D collider; unrelated transforms and scenes are untouched.
    """
    def vector(value, defaults):
        if not value:
            return defaults
        values = [float(part) for part in re.findall(r"-?\d+(?:\.\d+)?", str(value))]
        return tuple(values[:len(defaults)]) if len(values) >= len(defaults) else defaults

    changes = []
    for scene in plan.scenes.scenes:
        objects = {obj.id.casefold(): obj for obj in scene.objects}
        for actor in scene.objects:
            ground_reference = next((prop for component in actor.components for prop in component.properties
                if prop.name.casefold() in ("groundcheck", "ground_check") and prop.kind == "reference"), None)
            if ground_reference is None:
                continue
            anchor = objects.get(ground_reference.value.split(":", 1)[0].casefold())
            if anchor is None:
                continue
            script_names = {component.type.rsplit(".", 1)[-1].casefold() for component in actor.components}
            if any(script.filename[:-3].casefold() in script_names and "Physics2D.Raycast" in script.content
                   for script in plan.scripts.scripts):
                # Raycasts require an origin above the floor, unlike overlap
                # circles. Moving every authored sensor below the feet can make
                # rays start inside the ground and reject all zero-distance hits.
                # Preserve their authored origins and let real physics tests/QA
                # diagnose them instead of imposing overlap-circle geometry.
                continue
            collider = next((component for component in actor.components
                if component.type.rsplit(".", 1)[-1] in ("BoxCollider2D", "CapsuleCollider2D", "CircleCollider2D")), None)
            if collider is None:
                continue
            properties = {prop.name.casefold(): prop.value for prop in collider.properties}
            kind = collider.type.rsplit(".", 1)[-1]
            scale = actor.scale
            offset = vector(properties.get("offset"), (0.0, 0.0))
            if kind == "CircleCollider2D":
                radius = float(properties.get("radius", "0.5"))
                half_height = radius
            else:
                default_size = (1.0, 2.0) if kind == "CapsuleCollider2D" else (1.0, 1.0)
                size = vector(properties.get("size"), default_size)
                half_height = size[1] / 2
            radius_prop = next((prop.value for component in actor.components for prop in component.properties
                                if prop.name.casefold() in ("groundcheckradius", "ground_check_radius")), "0.1")
            try:
                radius = max(0.01, float(radius_prop))
            except (TypeError, ValueError):
                radius = 0.1
            gap = max(0.01, min(0.05, radius * 0.25))
            position = (actor.position[0] + offset[0] * scale[0],
                        actor.position[1] + (offset[1] - half_height) * scale[1] - gap,
                        actor.position[2])
            if anchor.parent != actor.id or any(abs(a - b) > 0.001 for a, b in zip(anchor.position, position)):
                old = anchor.position
                anchor.parent = actor.id
                anchor.position = position
                changes.append({"scene": scene.name, "actor": actor.id, "anchor": anchor.id,
                                "old_world_position": old, "world_position": position})
    return changes


def normalize_implicit_2d_box_collider_sizes(plan):
    """Give sprite-less 2D box colliders a usable default size.

    Unity serializes a newly added BoxCollider2D with an effectively zero size.
    That is unusable for actors whose sprite is created at runtime (or whose
    primitive has no imported sprite). Preserve authored sizes and sprite-backed
    collider sizing; only fill the omitted size for those sprite-less objects.
    """
    changes = []
    for scene in plan.scenes.scenes:
        for obj in scene.objects:
            if obj.sprite:
                continue
            collider = next((component for component in obj.components
                if component.type.rsplit(".", 1)[-1] == "BoxCollider2D"), None)
            if collider is None or any(prop.name.casefold() == "size" for prop in collider.properties):
                continue
            collider.properties.append(FlexibleProperty(name="size", kind="vector2", value="1,1"))
            changes.append({"scene": scene.name, "object": obj.id, "collider": "BoxCollider2D",
                            "size": [1.0, 1.0], "reason": "sprite-less collider had no authored bounds"})
    return changes


def normalize_ground_contact_queries(plan):
    """Keep 2D ground state tied to downward movement and actual collider contact."""
    changes = []
    for script in plan.scripts.scripts:
        source = script.content
        if ("Physics2D.OverlapCircle" not in source or "LayerMask groundLayer" not in source):
            continue
        collider = re.search(r"\b(?:BoxCollider2D|Collider2D|CapsuleCollider2D|CircleCollider2D)\s+(\w+)\s*;", source)
        rigidbody = re.search(r"\bRigidbody2D\s+(\w+)\s*;", source)
        method = re.search(r"(?m)^(?P<indent>\s*)private\s+bool\s+CheckGrounded\s*\(\s*\)\s*\{", source)
        if not collider or not rigidbody or not method:
            continue
        rb_name = rigidbody.group(1)
        upward_guard = re.search(
            rf"(?m)^(?P<indent>\s*)if\s*\(\s*{re.escape(rb_name)}\s*!=\s*null\s*&&\s*{re.escape(rb_name)}\.(?:linearVelocity|velocity)\.y\s*>\s*[^)]+\)\s*return false;",
            source)
        changed = False
        if upward_guard is None:
            indent = method.group("indent") + "    "
            guard = f"{indent}if ({rb_name} != null && {rb_name}.linearVelocity.y > 0.05f) return false;"
            insert_at = method.end()
            source = source[:insert_at] + "\n" + guard + source[insert_at:]
            upward_guard = re.search(
                rf"(?m)^(?P<indent>\s*)if\s*\(\s*{re.escape(rb_name)}\s*!=\s*null\s*&&\s*{re.escape(rb_name)}\.linearVelocity\.y\s*>\s*0\.05f\s*\)\s*return false;",
                source)
            changed = True
        contact_fallback = f"{collider.group(1)}.IsTouchingLayers(groundLayer)"
        if upward_guard and contact_fallback not in source:
            insertion = (upward_guard.group(0) + "\n\n" + upward_guard.group("indent") +
                         f"if ({collider.group(1)} != null && {contact_fallback}) return true; "
                         "// Studio QA: confirm real ground contact after the upward-motion guard.")
            source = source.replace(upward_guard.group(0), insertion, 1)
            changed = True
        if changed:
            script.content = source
            changes.append({"script": script.filename, "collider": collider.group(1), "rigidbody": rb_name,
                            "repair": "guard upward motion and use physical 2D contact for grounding"})
    return changes


def save_implementation(job, project, spec, plan):
    validate_implementation(spec, plan)
    autowired = autowire_named_component_references(plan)
    validate_implementation(spec, plan)
    if autowired:
        job.event("developer", "named_component_references_wired", references=autowired)
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
        if re.search(r"\bInput\.(?:GetAxis|GetKey|GetButton|GetMouseButton|touchCount|touches)\b", script.content):
            raise ValueError("Use UnityEngine.InputSystem; this project disables legacy UnityEngine.Input")
        required = set(re.findall(r"(?<![\w.])GetComponent\s*<\s*(?:UnityEngine\.)?(Rigidbody2D|Rigidbody|BoxCollider2D|CircleCollider2D|CapsuleCollider2D|BoxCollider|SphereCollider|CapsuleCollider)\s*>", script.content))
        required.update(re.findall(r"\btypeof\(\s*(?:UnityEngine\.)?(Rigidbody2D|Rigidbody|BoxCollider2D|CircleCollider2D|BoxCollider|SphereCollider|CapsuleCollider)\s*\)", script.content))
        for scene in plan.scenes.scenes:
            for obj in scene.objects:
                types = {c.type.rsplit(".", 1)[-1] for c in obj.components}
                if name not in types:
                    continue
                # CreatePrimitive supplies a 3D shape, except when the builder
                # deliberately removes it for a declared 2D physics object.
                implicit = {"Cube": "BoxCollider", "Sphere": "SphereCollider", "Capsule": "CapsuleCollider"}.get(obj.primitive)
                if implicit and not any(t.endswith("2D") for t in types):
                    types.add(implicit)
                missing = required - types
                if missing:
                    raise ValueError(f"Object {obj.id!r} uses {name} but is missing required components: {', '.join(sorted(missing))}")
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


async def compile_with_repairs(supervisor, service, spec, project, plan, runner=None, notify=None, max_repairs=3):
    """Compile real files, apply bounded targeted QA replacements, then recompile."""
    import hashlib
    if not 0 <= max_repairs <= 3:
        raise ValueError("Compiler repair limit must be between zero and three")
    runner = runner or UnityRunner(service.job, project)
    seen = set()
    history = []
    for attempt in range(max_repairs + 1):
        service.job.check_cancelled()
        save_implementation(service.job, project, spec, plan)
        try:
            result = await runner.compile()
            write_json(service.job.directory / "compiler_repairs.json", {"status": "passed", "history": history})
            return plan, result
        except UnityFailure as error:
            errors = compiler_errors(str(error))
            history.append({"attempt": attempt, "owner": error.owner, "errors": errors, "message": str(error)})
            write_json(service.job.directory / "compiler_repairs.json", {"status": "failed", "history": history})
            if error.owner == "infrastructure" or not errors or attempt == max_repairs:
                raise
            fingerprint = hashlib.sha256((json.dumps(errors, sort_keys=True) +
                json.dumps(plan.scripts.model_dump(), sort_keys=True)).encode()).hexdigest()
            if fingerprint in seen:
                raise UnityFailure("compile", "Identical compiler errors and code repeated; repair stopped") from error
            seen.add(fingerprint)
            if hasattr(service, "record_validation"):
                service.record_validation(getattr(service, "code_owner", "developer"), False)
            service.job.event("developer", "compiler_errors_assigned", diagnostics=errors)
            await supervisor.notify(notify, "QA: repairing generated C# from compiler diagnostics, then recompiling.")
            repaired = await supervisor.agents["qa"].repair_scripts(service, spec, plan, project, str(error))
            # save_implementation validates scope and writes actual replacements, not prose.
            save_implementation(service.job, project, spec, repaired)
            changed = [s.filename for s, old in zip(repaired.scripts.scripts, plan.scripts.scripts) if s.content != old.content]
            service.job.data["repairs"] += 1
            service.job.event("qa", "compiler_repair_saved", files=changed, attempt=attempt + 1)
            plan = repaired
            service.code_owner = "qa"


async def build_flexible(supervisor, service, spec, project, notify, android):
    job = service.job
    report = {"job_id": job.data["id"], "game": spec.name, "dimension": spec.dimension,
              "camera_perspective": spec.camera_perspective, "target_platform": spec.target_platform,
              "original_request": job.data["request"],
              "project": str(project), "spec_version": 2, "stages": {}, "limitations": list(spec.limitations),
              "requirements": requirement_report(spec), "completion_status": "partial"}
    runtime = None
    manifest = []
    plan = None
    ground_scene_repair_attempted = False
    def save_report():
        report["requirements"] = requirement_report(spec, plan, runtime, manifest)
        report["costs"] = job.data["costs"]
        report["cost_summary"] = Ledger(job).summary()
        report["model_quality"] = model_quality_report(job)
        write_json(job.directory / "requirements.json", report["requirements"])
        write_json(job.directory / "validation_report.json", report)
        update_work_items(job, report["requirements"])
    try:
        finish_stage(job, "game_spec", evidence="game_spec.json")
        for task in job.data["tasks"]:
            if task["name"] == "scripts":
                task["dependencies"] = ["game_spec", "ui_plan", "sprites"]
        # The v2 UI contract describes only this game's requested screens/controls.
        # Keep the legacy menu/touch-specific UIPlan on the v1 path.
        await supervisor.notify(notify, "Designer: planning UI for the requested game and target.")
        ui_path = job.directory / "ui_plan.json"
        if (job.data.get("resume_implementation") or job.data.get("resume_inputs")) and ui_path.is_file():
            ui = json.loads(ui_path.read_text(encoding="utf-8"))
        else:
            ui = await supervisor.agents["designer"].plan_ui(service, spec)
            ui = ui.model_dump()
        write_json(job.directory / "ui_plan.json", ui)
        finish_stage(job, "ui_plan", evidence="ui_plan.json")
        resuming = job.data.get("resume_implementation") and (job.directory / "implementation.json").is_file()
        if resuming:
            await supervisor.notify(notify, "Resuming the saved implementation and local script repairs.")
            art = {}
        else:
            await supervisor.notify(notify, "Art Designer: generating or importing requested assets; unavailable assets will be listed.")
            art = await with_progress(supervisor, notify,
                supervisor.agents["art"].create(service, spec, project),
                "Art Designer: asset generation is still running. Scene assembly follows implementation and compilation.")
        manifest = json.loads((job.directory / "art_manifest.json").read_text(encoding="utf-8"))
        await supervisor.notify(notify, asset_summary(manifest))
        write_json(project / "Assets/Resources/studio_art.json", {"assets": manifest})
        finish_stage(job, "sprites", "passed" if all(a["status"] == "ready" for a in manifest) else "partial", "art_manifest.json")
        await supervisor.notify(notify, "Developer: generating scripts, scene data and gameplay checks. "
                                "Unity objects appear after this data validates and C# compiles.")
        if resuming:
            plan = Implementation.model_validate_json((job.directory / "implementation.json").read_text(encoding="utf-8"))
            for script in plan.scripts.scripts:
                script.content = safe_path(project / "Assets/Studio/Generated", script.filename).read_text(encoding="utf-8")
            validate_implementation(spec, plan)
        else:
            plan = await with_progress(supervisor, notify,
                supervisor.agents["developer"].implement(service, spec, ui, art),
                "Developer: still generating or correcting the implementation. "
                "Scene assembly has not started yet; /status shows the latest stage.")
        grounding_fixes = {"anchors": normalize_ground_check_anchors(plan),
                           "collider_sizes": normalize_implicit_2d_box_collider_sizes(plan),
                           "contact_queries": normalize_ground_contact_queries(plan)}
        if grounding_fixes["anchors"] or grounding_fixes["contact_queries"]:
            job.data["repairs"] += 1
            job.event("qa", "grounding_repaired", **grounding_fixes)
        # Keep a validated implementation before any further paid QA call. A
        # provider failure in review must not discard the completed development.
        try:
            save_implementation(job, project, spec, plan)
        except ValueError as error:
            # Scope/file checks also belong to the existing bounded repair loop
            # below. Preserve the candidate without publishing unsafe scripts.
            write_json(job.directory / "implementation_candidate.json", plan.model_dump())
            job.event("developer", "implementation_preflight_failed", error=str(error)[:2000])
        review = PlanReview()
        runner = UnityRunner(job, project)
        async def compile_generated():
            nonlocal plan
            plan, result = await compile_with_repairs(supervisor, service, spec, project, plan,
                runner=runner, notify=notify, max_repairs=max(0, 3 - job.data["repairs"]))
            return result
        seen_failures = set()
        for attempt in range(4):
            try:
                save_implementation(job, project, spec, plan)
                finish_stage(job, "scripts", evidence="implementation.json")
                finish_stage(job, "scene_spec", evidence="scene_spec.json")
                for index, stage, operation in [(5, "compilation", compile_generated), (6, "assembly", runner.assemble),
                        (7, "EditMode", lambda: runner.tests("EditMode")), (8, "PlayMode", lambda: runner.tests("PlayMode"))]:
                    job.check_cancelled()
                    await supervisor.notify(notify, f"Unity QA: {stage}, attempt {attempt + 1}.")
                    report["stages"][stage] = await operation()
                    finish_stage(job, stage, evidence=report["stages"][stage])
                    if hasattr(service, "record_validation"):
                        service.record_validation(getattr(service, "code_owner", "developer") if stage == "compilation" else "scene" if stage == "assembly" else "qa", True)
                    job.event("qa", stage, repair_round=attempt)
                    save_report()
                runtime = json.loads((project / "Artifacts/runtime_checks.json").read_text(encoding="utf-8"))
                # Review the actual built/tested implementation. Missing future
                # APK/delivery evidence cannot trigger speculative rewrites
                # before compilation or prevent any scene from being assembled.
                service.verification_evidence = {"stages": report["stages"], "runtime": runtime,
                                                "phase": "after_unity_tests_before_android_and_delivery"}
                await supervisor.notify(notify, "QA: reviewing the assembled game with compilation and play-test evidence.")
                review = await supervisor.agents["qa"].review_implementation(service, spec, plan)
                write_json(job.directory / "qa_review.json", review.model_dump())
                report["qa_review"] = review.model_dump()
                report["limitations"] = list(spec.limitations) + review.missing_prompt_instructions + review.concerns
                report["limitations"].extend("QA identified incomplete requirement " + r for r in review.missing_requirement_ids)
                if review.missing_requirement_ids or review.missing_prompt_instructions or review.concerns:
                    raise UnityFailure("qa_review", "QA review found concrete implementation concerns: " +
                                       json.dumps(review.model_dump()), "qa")
                break
            except (UnityFailure, ValueError) as error:
                owner = error.owner if isinstance(error, UnityFailure) else "developer"
                if isinstance(error, UnityFailure):
                    failed_stage = "compilation" if error.stage == "compile" else error.stage
                    report["stages"][failed_stage] = {"status": "failed", "error": str(error), "owner": owner}
                    for task in job.data["tasks"]:
                        if task["name"] == failed_stage:
                            task["status"] = "failed"
                report.setdefault("repair_history", []).append({"round": attempt, "error": str(error), "owner": owner})
                save_report()
                fingerprint = (str(error), plan.model_dump_json())
                if fingerprint in seen_failures:
                    raise UnityFailure("repair", "The same implementation and failure repeated; repair stopped", owner) from error
                seen_failures.add(fingerprint)
                if owner == "infrastructure" or attempt == 3 or job.data["repairs"] >= 3:
                    raise
                failed_stage = error.stage if isinstance(error, UnityFailure) else ""
                if hasattr(service, "record_validation") and failed_stage != "qa_review":
                    failed_role = (getattr(service, "scene_owner", "developer") if owner == "scene" else
                                   getattr(service, "code_owner", "developer"))
                    service.record_validation(failed_role, False)
                job.data["repairs"] += 1
                invalidate_validation(job)
                report["stages"] = {}
                await supervisor.notify(notify, "QA: repairing against the saved diagnostics, preserving the test plan.")
                runtime_evidence_path = project / "Artifacts/runtime_checks.json"
                runtime_diagnostics = {}
                if runtime_evidence_path.is_file():
                    try:
                        runtime_diagnostics = json.loads(runtime_evidence_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                failed_ids = {item.get("id") for item in runtime_diagnostics.get("checks", [])
                              if item.get("passed") is not True}
                # The first ground-contact failure gets a hierarchy/physics repair;
                # if it persists, send it to QA for code/layer-mask repair.
                ground_probe_failed = failed_stage == "PlayMode" and "probe_ground_collision" in failed_ids
                route_to_scene = (owner == "scene" and
                    not (ground_probe_failed and ground_scene_repair_attempted))
                diagnostics = {"unity_error": str(error), "failed_probe_ids": sorted(failed_ids),
                    "runtime_checks": runtime_diagnostics,
                    "scene_spec": plan.scenes.model_dump(),
                    "repair_round": job.data["repairs"]}
                if route_to_scene:
                    if ground_probe_failed:
                        ground_scene_repair_attempted = True
                    repaired = plan.model_copy(deep=True)
                    try:
                        repaired.scenes = await supervisor.agents["developer"].repair_scenes(service, spec, plan.scenes,
                            json.dumps(diagnostics, ensure_ascii=False))
                    except ValueError:
                        # Malformed/no-op scene handoff must not consume the whole
                        # recovery round when QA can make the corresponding code fix.
                        repaired.scenes = plan.scenes
                    if repaired.scenes == plan.scenes:
                        route_to_scene = False
                    else:
                        plan = validate_implementation(spec, repaired)
                        service.scene_owner = "developer"
                if not route_to_scene:
                    plan = await supervisor.agents["qa"].repair_implementation(service, spec, plan,
                        json.dumps(diagnostics, ensure_ascii=False))
                    service.code_owner = "qa"
        report["limitations"].extend(plan.limitations)
        report["limitations"].extend(a.get("reason", "Unavailable asset: " + a["id"]) for a in manifest if a["status"] != "ready")
        queued = [item["text"] for item in job.data.get("additional_instructions", []) if item.get("status") == "queued"]
        if queued:
            report["queued_instructions"] = queued
            report["limitations"].append("These user instructions were saved but arrived after planning; they were not applied to this build: " + " | ".join(queued))
        report["android"] = {"status": "not_requested", "device_test": "unverified"}
        if android:
            await supervisor.notify(notify, "Android: switching target and building the verified game APK.")
            try:
                report["android"] = await runner.android()
            except UnityFailure as error:
                report["android"] = {"status": "failed", "error": str(error), "device_test": "unverified"}
                report["limitations"].append("Android build failed: " + str(error))
                report["completion_status"] = "failed"
                save_report()
                raise
        report["gameplay"] = "Only the linked runtime probes passed; untested mechanics and visual quality remain unverified."
        save_report()
        complete = all(r["verification_result"] == "passed" and r["implementation_status"] != "incomplete"
                       for r in report["requirements"]) and not report["limitations"]
        report["completion_status"] = "completed" if complete else "partial"
        save_report()
        artifacts = package(job, project, report)
        job.data["status"] = report["completion_status"]
        finish_stage(job, "artifacts", evidence="validation_report.json")
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
