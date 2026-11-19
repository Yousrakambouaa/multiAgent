"""Game design, Unity development and QA within the five-agent studio."""
import json
import os

from studio.schemas import AssetSpec, CodeBundle, GameSpec, LevelSpec, UIPlan
from studio.flexible import (GameSpecV2, Implementation, PlanReview,
                             normalize_implementation_response, validate_prompt_trace, validate_implementation)


# Model configuration keys describe tasks, not additional agent identities.
# Keep the existing ui/scene profiles so models, fallbacks and budgets stay intact.
AGENT_MODEL_PROFILES = {
    "supervisor": ("supervisor",),
    "designer": ("designer", "ui"),
    "developer": ("developer", "scene"),
    "art": ("art",),
    "qa": ("qa",),
}


def demo_spec():
    return GameSpec(
        name="Suntrail Adventure", genre="platformer", art_style="warm illustrated woodland adventure, crisp silhouettes, soft golden light",
        palette=["#142C39", "#3D8B68", "#F5BD58", "#EEF3D9"],
        player_mechanics=["run left and right", "jump from solid ground", "collect coins", "reach the exit flag"],
        input_requirements=["A/D or arrow keys", "Space to jump", "large on-screen touch buttons"],
        enemies=["patrolling woodland creatures"], obstacles=["raised platforms", "enemies"],
        collectibles=["gold coins"], levels=[LevelSpec(name="Suntrail", length=36, platforms=5, coins=10, enemies=2)],
        ui_requirements=["main menu with Play", "score and coin HUD", "pause and restart", "touch controls", "completion screen"],
        required_assets=[AssetSpec(id=kind, kind=kind, prompt=prompt, transparent=kind != "background") for kind, prompt in [
            ("player", "friendly small adventurous forest explorer, side view single character"),
            ("ground", "one seamless grass topped earth platform tile, rectangular side view"),
            ("coin", "single gleaming golden collectible coin, centered"),
            ("enemy", "one round friendly looking orange woodland creature, side view"),
            ("background", "wide layered forest hills, sunrise, empty foreground space for platform game")]],
        scenes=["MainMenu", "Suntrail", "Results"], transitions=["MainMenu -> Suntrail", "Suntrail -> Results", "Results -> MainMenu"],
        acceptance_criteria=["player spawns above solid ground", "horizontal input moves player", "jump only from grounded state",
            "coins disappear on contact", "collecting coins increases score", "no missing scripts or serialized references",
            "keyboard and touch controls work", "reaching exit completes level"],
    )


class GameDesigner:
    async def plan(self, service, request):
        backend=os.environ.get("STUDIO_DESIGNER_BACKEND","crewai")
        caller=service.json
        if backend=="crewai":
            from studio.frameworks import crewai_json
            async def caller(role,system,value,max_tokens): return await crewai_json(service,role,system,value,max_tokens)
        elif backend!="direct": raise ValueError("Unknown designer backend")
        result = await caller("designer",
            "You are the Game Designer. Interpret this user's game independently as GameSpec v2. "
            "Any genre, 2D/3D/mixed, perspective and Unity target is allowed. Do not assume platformer, coins, "
            "jumping, Android, landscape, a player character, or a menu/results structure. "
            "Keep every explicit instruction; each requirement quotes an EXACT substring of the request. "
            "Split compound instructions into separate R IDs, including visual style, controls and target. "
            "Plan dependent T tasks for all R IDs using only designer/developer/art/qa. "
            "Record sensible defaults for unspecified details in assumptions. Never ask the user questions; "
            "leave clarification_questions empty and resolve ambiguity with the smallest reasonable assumption. "
            "Do not scope away requested systems: describe development stages and limitations within the actual job cap. "
            "Runtime requirements need behavioral probes; visuals need human review. Actual artwork uses images or "
            "licensed local asset_library files. Images cannot generate 3D models. Without an available model/audio "
            "tool or licensed local asset, declare workflow=unavailable; never replace requested art with placeholders. "
            "Procedural geometry may be implemented in code when appropriate to the requested design. "
            "Return exactly the provided schema, not implementation code.",
            {"request": request, "schema": GameSpecV2.model_json_schema(),
             "cap_usd": service.job.data["cap_usd"],
             "asset_library": available_library()}, 6500)
        spec = GameSpecV2.model_validate(result)
        validate_prompt_trace(spec, request)
        return spec


    async def plan_ui(self, service, spec):
        """Plan UI as a Game Designer task using the existing UI model profile."""
        runtime = ("Describe only UI requested by this game's specification. UI must be implemented by the developer; "
                   "no menu, HUD or touch controls are supplied automatically."
                   if getattr(spec, "version", 1) == 2 else
                   "The runtime implements responsive menu, HUD, pause, results and touch controls.")
        return UIPlan.model_validate(await service.json("ui",
            "You are the Game Designer planning UI/UX for the requested target and controls. Read GameSpec and return a UIPlan. "
            "Use concise English labels, strong contrast, large touch targets and the shared palette. "
            "Return exactly the schema fields. " + runtime,
            {"game_spec": spec.model_dump(), "schema": UIPlan.model_json_schema()}, 1200))


class UnityDeveloper:
    async def implement(self, service, spec, ui, art):
        ui_data = ui.model_dump() if hasattr(ui, "model_dump") else ui
        result = await service.json("developer", FLEXIBLE_DEVELOPER_PROMPT,
            {"original_request": service.job.data.get("effective_request", service.job.data["request"]), "game_spec": spec.model_dump(),
             "ui_plan": ui_data, "assets": art, "schema": Implementation.model_json_schema()}, 16000)
        normalized = normalize_implementation_response(result, spec)
        return validate_implementation(spec, Implementation.model_validate(normalized))

    async def generate(self, service, spec, runtime_contract):
        result = await service.json("developer",
            "You are the Unity Developer. Create ONE complete GeneratedRules.cs file containing public class "
            "GeneratedRules : Studio.Runtime.LevelRules. Override public override void Configure(Studio.Runtime.PlayerMotor player) "
            "and set player.moveSpeed and player.jumpSpeed from the GameSpec. You may override "
            "public override int CoinPoints() to return a positive score value. The tested runtime supplies "
            "all core mechanics, menus, input, enemies and scoring. Do not duplicate them. "
            "Use UnityEngine if needed. Do not add namespaces or editor code. No external packages, file IO, "
            "processes, networking, reflection or runtime code loading. Return {scripts:[{filename,content}]}.",
            {"game_spec": spec.model_dump(), "runtime_contract": runtime_contract, "schema": CodeBundle.model_json_schema()}, 4000)
        bundle = CodeBundle.model_validate(result)
        if [s.filename for s in bundle.scripts] != ["GeneratedRules.cs"]:
            raise ValueError("Developer must implement the agreed GeneratedRules.cs contract")
        return bundle


    def assemble_scenes(self, spec, ui, art):
        """Use the deterministic scene tool as a Unity Developer task."""
        from studio.scenes import assemble_scenes
        return assemble_scenes(spec, ui, art)

    async def repair_scenes(self,service,spec,scenes,error_report):
        from studio.schemas import SceneBundle
        result=await service.json("scene",
            "You are the Unity Developer. Repair the provided SceneBundle using actual Unity assembly diagnostics. "
            "Preserve GameSpec, scene identifiers, asset paths and gameplay. Use only component types "
            "already present. Correct serialized field names or references; never invent classes. "
            "Return the complete SceneBundle with exactly its schema fields.",
            {"game_spec":spec.model_dump(),"scene_spec":scenes.model_dump(),"errors":error_report[-10000:],
             "schema":SceneBundle.model_json_schema()},11000)
        return SceneBundle.model_validate(result)


class QADebugger:
    async def review_implementation(self, service, spec, plan):
        return PlanReview.model_validate(await service.json("qa",
            "You are QA & Debugger. Compare the ORIGINAL user request, every GameSpec requirement, implementation "
            "and behavioral probes. Report omitted prompt instructions, missing R IDs and concrete concerns. "
            "Check genre, dimension, camera, controls, progression, assets and UI. Compilation is not gameplay evidence. "
            "This is a design review, not proof that any test passed. Do not ask the user for details; resolve optional gaps "
            "using the recorded assumptions. Return only the schema fields.",
            {"original_request": service.job.data.get("effective_request", service.job.data["request"]), "game_spec": spec.model_dump(),
             "implementation": plan.model_dump(), "schema": PlanReview.model_json_schema()}, 3000))

    async def repair_implementation(self, service, spec, plan, diagnostics):
        caller = service.json
        backend = os.environ.get("STUDIO_QA_BACKEND", "autogen")
        if backend == "autogen":
            from studio.frameworks import autogen_json
            async def caller(role, system, value, max_tokens):
                return await autogen_json(service, role, system, value, max_tokens)
        elif backend != "direct":
            raise ValueError("Unknown QA backend")
        result = await caller("qa", FLEXIBLE_DEVELOPER_PROMPT +
            " You are QA & Debugger. Repair the implementation using actual diagnostics. "
            "Compare every original prompt instruction with GameSpec and the implementation. "
            "Preserve requirements and probe IDs. Fix the implementation, never weaken assertions to conceal failure.",
            {"original_request": service.job.data["request"], "game_spec": spec.model_dump(),
             "implementation": plan.model_dump(), "diagnostics": diagnostics,
             "schema": Implementation.model_json_schema()}, 16000)
        normalized = normalize_implementation_response(result, spec)
        repaired = validate_implementation(spec, Implementation.model_validate(normalized))
        if [p.model_dump() for p in repaired.probes] != [p.model_dump() for p in plan.probes]:
            raise ValueError("QA repairs must preserve behavioral probes; review a changed test plan separately")
        return repaired

    async def repair(self, service, spec, error_report, scripts, runtime_contract):
        backend=os.environ.get("STUDIO_QA_BACKEND","autogen")
        caller=service.json
        if backend=="autogen":
            from studio.frameworks import autogen_json
            async def caller(role,system,value,max_tokens): return await autogen_json(service,role,system,value,max_tokens)
        elif backend!="direct": raise ValueError("Unknown QA backend")
        bundle = CodeBundle.model_validate(await caller("qa",
            "You are the Unity QA debugging specialist. Repair GeneratedRules.cs using actual Unity diagnostics. "
            "Return the complete corrected file in {scripts:[{filename,content}]}. Preserve the provided runtime contract. "
            "Only GeneratedRules.cs is editable. Never claim tests passed; Unity reruns them after your patch. "
            "Include 'using Studio.Runtime;' if referring to PlayerMotor or LevelRules without a namespace. "
            "Configure must match public override void Configure(Studio.Runtime.PlayerMotor player). "
            "Do not add editor code, IO, networking, reflection or external dependencies.",
            {"game_spec": spec.model_dump(), "errors": error_report[-12000:], "scripts": scripts,
             "runtime_contract": runtime_contract, "schema": CodeBundle.model_json_schema()}, 4000))
        if [s.filename for s in bundle.scripts] != ["GeneratedRules.cs"]:
            raise ValueError("QA tried to modify files outside its assigned scope")
        return bundle


def available_library():
    from studio.config import ROOT
    root = ROOT / "asset_library"
    return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.suffix.lower()
            in (".png", ".jpg", ".fbx", ".obj", ".wav", ".ogg", ".anim", ".prefab")][:100] if root.exists() else []


FLEXIBLE_DEVELOPER_PROMPT = """You are the Unity Developer implementing the original prompt in Unity 6.
Return an Implementation with scripts:{scripts:[{filename,content}]}, scenes:{version:2,title,dimension,orientation,scenes:[...],materials:[...]}, and behavioral probes.
Do not replace requested mechanics, genre, dimension, camera, controls or art. List unfinished R IDs in limitations.
Generate standalone public MonoBehaviour classes with file names matching class names, no namespaces.
Use UnityEngine and UnityEngine.InputSystem (Keyboard.current/Mouse.current), not the legacy Input API.
No editor APIs, file IO, networking, reflection, processes, unsafe code, external dependencies or package installs.
Use multiple scripts as needed; never force LevelRules or GeneratedRules on a game. Bounded code output is not a license to drop requirements.
Every scene is EXPLICIT: include Camera components, projection/FOV/size/position/rotation, lights for 3D,
all gameplay components, physics 2D or 3D as appropriate, UI and references. No camera/player/ground/menu is injected.
The builder supports primitives, custom mesh vertices/triangles, model/prefab paths and material IDs.
Primitives are appropriate only when the design calls for geometric/procedural forms, not substitutes for requested artwork.
Asset paths come from the art manifest; status != ready is a missing asset, not completed work.
For model/prefab use object.model; for sprites use object.sprite; texture paths go on materials.
Scene properties: kind float/int/bool/string/enum/color/vector2/vector3/reference/asset, value always string.
Vectors use JSON arrays in value, enums use their enum name, reference uses objectID:Full.Component.Type
or objectID for a GameObject; asset uses Assets/... path. Transform references use UnityEngine.Transform.
Object position/scale/rotation are arrays; rotation uses Euler degrees. Parent IDs preserve WORLD transforms.
UI can be created dynamically by gameplay scripts using UnityEngine.UI and InputSystemUIInputModule, or explicit components.
Do not use UnityEditor in runtime. Scene transitions use UnityEngine.SceneManagement.
Expose real gameplay operations/state for tests. Probes operate each from a freshly loaded named scene.
Probe steps: wait(frames), key(member=InputSystem key name,value=true/false),
invoke(object_id,component,member=public zero-arg gameplay method), set(public field/property,value string),
click(object_id=Button ID). No test-only methods which fake success.
Assertions: exists/active(object_id,value=true/false), property(object_id,component,member,comparison,value),
position_changed(object_id,value=min distance), scene(value=scene name).
Test actual controls, movement, collisions/progression and win conditions, not simply labels or configuration.
Tie probes and objects to R IDs. Do not claim verification; only Unity execution can produce evidence.
"""
