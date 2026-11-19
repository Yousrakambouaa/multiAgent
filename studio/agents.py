"""Specialists share GameSpec; handoffs are bounded, structured calls."""
import json
import os

from studio.schemas import AssetSpec, CodeBundle, GameSpec, LevelSpec, UIPlan


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
            "You are the Game Designer. Produce a complete, modest 2D platformer GameSpec matching the user's idea. "
            "The MVP supports run, grounded jumping, coins, patrolling enemies, solid ground/platforms and an exit flag. "
            "Do not promise mechanics beyond these. Include a menu scene, all level scenes and a result scene. "
            "Use safe scene identifiers. Use a consistent art palette. Each required asset is a SINGLE sprite, not a sheet. "
            "Your entire response must validate against the given schema.",
            {"request": request, "schema": GameSpec.model_json_schema(), "example": demo_spec().model_dump()}, 6500)
        return GameSpec.model_validate(result)


class UIDesigner:
    async def plan(self, service, spec):
        return UIPlan.model_validate(await service.json("ui",
            "You are the mobile UI/UX designer. Read the shared GameSpec and return a UIPlan. "
            "Use concise English labels, strong contrast, large touch targets and the shared palette. "
            "Return exactly the schema fields. The runtime implements responsive menu, HUD, pause, results and touch controls.",
            {"game_spec": spec.model_dump(), "schema": UIPlan.model_json_schema()}, 1200))


class UnityDeveloper:
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


class SceneArchitect:
    """Compile the shared contract deterministically; no extra paid conversation."""
    def assemble(self, spec, ui, art):
        from studio.scenes import assemble_scenes
        return assemble_scenes(spec, ui, art)

    async def repair(self,service,spec,scenes,error_report):
        from studio.schemas import SceneBundle
        result=await service.json("scene",
            "Repair the provided SceneBundle using actual Unity assembly diagnostics. "
            "Preserve GameSpec, scene identifiers, asset paths and gameplay. Use only component types "
            "already present. Correct serialized field names or references; never invent classes. "
            "Return the complete SceneBundle with exactly its schema fields.",
            {"game_spec":spec.model_dump(),"scene_spec":scenes.model_dump(),"errors":error_report[-10000:],
             "schema":SceneBundle.model_json_schema()},11000)
        return SceneBundle.model_validate(result)


class QADebugger:
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
