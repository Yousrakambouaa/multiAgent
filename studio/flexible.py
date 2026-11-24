"""GameSpec v2 and validated Unity instructions, independent of game genre.

Version 1 remains readable by the existing platformer pipeline. These are data
contracts and tools, not additional AI agents.
"""
import json
import math
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from studio.schemas import Contract, GameSpec, CodeBundle, ScriptFile, ObjectSpec, PropertySpec


def _is_pipeline_requirement(text):
    text = str(text or "").casefold()
    if re.search(r"\b(?:move|movement|jump|jumping|collect|collection|collision|collisions|touch|controls|player|enemy|enemies|score|win|victory|gameplay)\b", text):
        return False
    return any(re.search(pattern, text) for pattern in (
        r"\b(?:android\s+)?apk\b",
        r"\b(?:edit[- ]mode|play[- ]mode)\s+tests?\b",
        r"\bautomated\s+(?:compile\s+and\s+)?(?:unity\s+)?tests?\b",
        r"\b(?:retry|retries)\b.*\bbudget\b|\bbudget\b.*\b(?:retry|retries)\b",
        r"\btelegram\b.*\b(?:deliver|send|upload)|\b(?:deliver|send|upload)\b.*\btelegram\b",
    ))


class Requirement(Contract):
    id: str = Field(pattern=r"^R[0-9]{1,4}$")
    original_instruction: str = Field(min_length=1, max_length=4000)
    implementation: str = Field(min_length=1, max_length=2000)
    owner: Literal["supervisor", "designer", "developer", "art", "qa"]
    verification_method: Literal["runtime", "structure", "manual", "pipeline"]
    # Status is computed by the supervisor; a model cannot mark itself verified.

    @model_validator(mode="after")
    def classify_pipeline_requirement(self):
        if self.verification_method == "runtime" and _is_pipeline_requirement(self.original_instruction):
            self.verification_method = "pipeline"
        return self


class FlexibleAsset(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    kind: Literal["sprite", "texture", "model", "audio", "animation", "prefab"]
    prompt: str = Field(min_length=5, max_length=1500)
    workflow: Literal["image", "library", "unavailable"]
    library_path: str = ""
    license: str = ""
    width: int = Field(default=256, ge=64, le=2048)
    height: int = Field(default=256, ge=64, le=2048)
    transparent: bool = False
    pixels_per_unit: int = Field(default=128, ge=16, le=1024)
    requirement_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def workflow_matches(self):
        if self.workflow == "image" and self.kind not in ("sprite", "texture"):
            raise ValueError("Image generation cannot produce models, audio, animations or prefabs")
        if self.workflow == "library" and (not self.library_path or not self.license):
            raise ValueError("Library assets require a path and license/provenance")
        return self


class WorkItem(Contract):
    id: str = Field(pattern=r"^T[0-9]{1,4}$")
    description: str = Field(min_length=1, max_length=1000)
    owner: Literal["designer", "developer", "art", "qa"]
    dependencies: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(min_length=1)


class GameSpecV2(Contract):
    version: Literal[2] = 2
    name: str = Field(min_length=3, max_length=70)
    genre: str = Field(min_length=2, max_length=150)
    dimension: Literal["2D", "3D", "mixed"]
    camera_perspective: str = Field(min_length=2, max_length=300)
    target_platform: str = Field(min_length=2, max_length=100)
    orientation: Literal["landscape", "portrait", "auto", "unspecified"] = "unspecified"
    art_style: str = Field(min_length=3, max_length=500)
    palette: list[str] = Field(default_factory=list, max_length=12)
    mechanics: list[str] = Field(min_length=1, max_length=60)
    controls: list[str] = Field(min_length=1, max_length=30)
    level_structure: str = Field(min_length=1, max_length=1500)
    ui_requirements: list[str] = Field(default_factory=list, max_length=30)
    scenes: list[str] = Field(min_length=1, max_length=30)
    required_assets: list[FlexibleAsset] = Field(default_factory=list, max_length=40)
    requirements: list[Requirement] = Field(min_length=1, max_length=100)
    tasks: list[WorkItem] = Field(min_length=1, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=40)
    clarification_questions: list[str] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="before")
    @classmethod
    def plan_every_requirement(cls, value):
        """Fill omitted requirement-to-task links instead of rejecting a usable plan."""
        if not isinstance(value, dict) or not isinstance(value.get("requirements"), list):
            return value
        requirements = value["requirements"]
        tasks = value.get("tasks", [])
        if not isinstance(tasks, list):
            return value
        covered = set()
        for task in tasks:
            if isinstance(task, dict) and isinstance(task.get("requirement_ids"), list):
                covered.update(rid for rid in task["requirement_ids"] if isinstance(rid, str))
        missing = [item for item in requirements if isinstance(item, dict)
                   and isinstance(item.get("id"), str) and item["id"] not in covered]
        if not missing:
            return value

        normalized = dict(value)
        normalized_tasks = [dict(task) if isinstance(task, dict) else task for task in tasks]
        used_ids = {task.get("id") for task in normalized_tasks if isinstance(task, dict)}
        grouped = {}
        for requirement in missing:
            owner = requirement.get("owner", "developer")
            if not isinstance(owner, str): owner = "developer"
            owner = owner if owner in {"designer", "developer", "art", "qa"} else "developer"
            grouped.setdefault(owner, []).append(requirement["id"])
        next_id = 1
        for owner, requirement_ids in grouped.items():
            while f"T{next_id}" in used_ids:
                next_id += 1
            normalized_tasks.append({
                "id": f"T{next_id}",
                "description": "Complete the planned work for requirements: " + ", ".join(requirement_ids),
                "owner": owner,
                "dependencies": [],
                "requirement_ids": requirement_ids,
            })
            used_ids.add(f"T{next_id}")
            next_id += 1
        normalized["tasks"] = normalized_tasks
        return normalized

    @model_validator(mode="after")
    def validate_links(self):
        reqs = {r.id for r in self.requirements}
        if len(reqs) != len(self.requirements):
            raise ValueError("Duplicate requirement IDs")
        if len(set(self.scenes)) != len(self.scenes) or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", s) for s in self.scenes):
            raise ValueError("Scenes must have unique safe identifiers")
        if len({a.id for a in self.required_assets}) != len(self.required_assets):
            raise ValueError("Duplicate asset IDs")
        tasks = {t.id: t for t in self.tasks}
        if len(tasks) != len(self.tasks):
            raise ValueError("Duplicate task IDs")
        visited = set()
        def visit(key, active):
            if key not in tasks or key in active:
                raise ValueError("Missing or cyclic task dependency")
            if key in visited:
                return
            for dep in tasks[key].dependencies:
                visit(dep, active | {key})
            visited.add(key)
        for item in [*self.required_assets, *self.tasks]:
            if not set(item.requirement_ids) <= reqs:
                raise ValueError("Unknown requirement ID")
        for key in tasks:
            visit(key, set())
        if not reqs <= {r for t in self.tasks for r in t.requirement_ids}:
            raise ValueError("Every requirement must have a planned task")
        return self


class FlexibleProperty(PropertySpec):
    kind: Literal["float", "int", "bool", "string", "reference", "asset", "color", "vector2", "vector3", "enum"]


class FlexibleComponent(Contract):
    type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.]*$")
    properties: list[FlexibleProperty] = Field(default_factory=list, max_length=50)


class Vertex(Contract):
    x: float
    y: float
    z: float


class MeshSpec(Contract):
    vertices: list[Vertex] = Field(min_length=3, max_length=10000)
    triangles: list[int] = Field(min_length=3, max_length=60000)
    @model_validator(mode="after")
    def valid_mesh(self):
        if len(self.triangles) % 3 or any(i < 0 or i >= len(self.vertices) for i in self.triangles):
            raise ValueError("Mesh indices must form valid triangles")
        return self


class FlexibleObject(ObjectSpec):
    rotation: tuple[float, float, float] = (0, 0, 0)
    active: bool = True
    primitive: Literal["", "Cube", "Sphere", "Capsule", "Cylinder", "Plane", "Quad"] = ""
    model: str = ""
    material: str = ""
    mesh: MeshSpec | None = None
    components: list[FlexibleComponent] = Field(default_factory=list, max_length=30)
    requirement_ids: list[str] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def model_path(cls, value):
        return ObjectSpec.asset_path(value)


class MaterialSpec(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    color: str = Field(default="#FFFFFF", pattern=r"^#[A-Fa-f0-9]{6}([A-Fa-f0-9]{2})?$")
    texture: str = ""
    metallic: float = Field(default=0, ge=0, le=1)
    smoothness: float = Field(default=.3, ge=0, le=1)
    unlit: bool = False


class FlexibleScene(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    kind: str = Field(default="gameplay", max_length=60)
    background_color: str = "#18202A"
    requirement_ids: list[str] = Field(default_factory=list)
    objects: list[FlexibleObject] = Field(min_length=1, max_length=1000)
    @model_validator(mode="after")
    def links(self):
        ids = {o.id for o in self.objects}
        if len(ids) != len(self.objects):
            raise ValueError("Object IDs must be unique within a scene")
        by_id = {o.id: o for o in self.objects}
        for obj in self.objects:
            chain, parent = {obj.id}, obj.parent
            while parent:
                if parent not in ids or parent in chain:
                    raise ValueError("Missing or cyclic parent")
                chain.add(parent)
                parent = by_id[parent].parent
            for comp in obj.components:
                for prop in comp.properties:
                    if prop.kind == "reference" and prop.value.split(":")[0] not in ids:
                        target = prop.value.split(":", 1)[0]
                        raise ValueError(f"Missing serialized reference target {target!r} in "
                            f"{self.name}/{obj.id}/{comp.type}.{prop.name}. "
                            "References require a declared object ID; omit unused optional properties instead of "
                            "supplying an empty reference. Available IDs: " + ", ".join(sorted(ids)))
        return self


class SceneBundleV2(Contract):
    version: Literal[2] = 2
    title: str
    dimension: Literal["2D", "3D", "mixed"]
    orientation: Literal["landscape", "portrait", "auto", "unspecified"] = "unspecified"
    scenes: list[FlexibleScene] = Field(min_length=1, max_length=30)
    materials: list[MaterialSpec] = Field(default_factory=list, max_length=80)


class ProbeStep(Contract):
    action: Literal["wait", "invoke", "set", "key", "click", "pointer"]
    object_id: str = ""
    component: str = ""
    member: str = ""
    value: str = ""
    frames: int = Field(default=1, ge=1, le=600)


class ProbeAssertion(Contract):
    kind: Literal["exists", "active", "property", "position_changed", "scene"]
    object_id: str = ""
    component: str = ""
    member: str = ""
    comparison: Literal["equals", "greater", "less"] = "equals"
    value: str = ""


class RuntimeProbe(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    requirement_ids: list[str] = Field(min_length=1)
    scene: str
    steps: list[ProbeStep] = Field(default_factory=list, max_length=40)
    assertions: list[ProbeAssertion] = Field(min_length=1, max_length=30)


class GameCodeBundle(CodeBundle):
    # The legacy rules-only bundle allowed eight files. A standalone game needs
    # separate components for controls, UI, progression and gameplay systems.
    scripts: list[ScriptFile] = Field(min_length=1, max_length=32)


class Implementation(Contract):
    scripts: GameCodeBundle
    scenes: SceneBundleV2
    probes: list[RuntimeProbe] = Field(min_length=1, max_length=100)
    # Art and other unfinished requirements must be declared, never silently dropped.
    implemented_requirement_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_script_bundle(cls, value):
        """Accept the common flattened {scripts:[...]} response shape as well."""
        if isinstance(value, dict) and isinstance(value.get("scripts"), list):
            normalized = dict(value)
            normalized["scripts"] = {"scripts": value["scripts"]}
            return normalized
        return value


class ImplementationEdit(Contract):
    op: Literal["add", "replace", "remove"]
    path: str = Field(pattern=r"^/(scripts|scenes|probes)/.+")
    value: object = None


class ImplementationEdits(Contract):
    edits: list[ImplementationEdit] = Field(min_length=1, max_length=30)


def apply_implementation_edits(candidate, response):
    """Apply bounded data edits, then let the complete contract validate them.

    Existing probes are immutable; a repair may only append a new probe.
    No generated code is executed.
    """
    from copy import deepcopy
    result = deepcopy(candidate)
    for edit in ImplementationEdits.model_validate(response).edits:
        parts = [part.replace("~1", "/").replace("~0", "~") for part in edit.path[1:].split("/")]
        if parts[0] == "probes" and (edit.op != "add" or parts != ["probes", "-"]):
            raise ValueError("Probe edits may only append a new probe at /probes/-")
        target = result
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) and part.isdigit() else target[part]
            key = parts[-1]
            if isinstance(target, list):
                index = len(target) if key == "-" and edit.op == "add" else int(key)
                if index < 0 or index >= len(target) + (edit.op == "add"):
                    raise ValueError("List index out of range")
                if edit.op == "add": target.insert(index, edit.value)
                elif edit.op == "remove": target.pop(index)
                else: target[index] = edit.value
            elif isinstance(target, dict):
                if edit.op != "add" and key not in target:
                    raise ValueError("Object key does not exist")
                if edit.op == "remove": del target[key]
                else: target[key] = edit.value
            else:
                raise ValueError("Edit target is not a container")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid implementation edit path {edit.path!r}: {error}") from error
    return result


def preserves_existing_probes(original, candidate):
    original_probes = original.get("probes", []) if isinstance(original, dict) else None
    candidate_probes = candidate.get("probes") if isinstance(candidate, dict) else None
    return (isinstance(original_probes, list) and isinstance(candidate_probes, list)
            and len(candidate_probes) >= len(original_probes)
            and candidate_probes[:len(original_probes)] == original_probes)


def normalize_implementation_response(value, spec):
    """Adapt common flattened model output to the versioned implementation contract."""
    if not isinstance(value, dict):
        return value
    from copy import deepcopy
    normalized = deepcopy(value)
    if isinstance(normalized.get("scripts"), list):
        normalized["scripts"] = {"scripts": normalized["scripts"]}
    script_bundle = normalized.get("scripts")
    if isinstance(script_bundle, dict):
        for key in ("probes", "implemented_requirement_ids", "limitations"):
            if key in script_bundle:
                nested_value = script_bundle.pop(key)
                if key in normalized and normalized[key] != nested_value:
                    raise ValueError(f"Conflicting implementation field: {key}")
                normalized[key] = nested_value
        scripts = script_bundle.get("scripts")
        if isinstance(scripts, list):
            script_requirement_ids = []
            for script in scripts:
                if not isinstance(script, dict) or not isinstance(script.get("requirement_ids"), list):
                    continue
                script_requirement_ids.extend(script.pop("requirement_ids"))
            if script_requirement_ids:
                current = normalized.get("implemented_requirement_ids", [])
                if not isinstance(current, list):
                    raise ValueError("implemented_requirement_ids must be a list")
                normalized["implemented_requirement_ids"] = list(dict.fromkeys(
                    [*current, *script_requirement_ids]))

    scenes = normalized.get("scenes")
    if isinstance(scenes, list):
        raw_bundle = {}
        for key, fallback in (("title", spec.name), ("dimension", spec.dimension),
                              ("orientation", spec.orientation)):
            raw_bundle[key] = normalized.pop(key, fallback)
        raw_bundle["version"] = 2
        raw_bundle["scenes"] = scenes
        raw_bundle["materials"] = normalized.pop("materials", [])
        normalized["scenes"] = raw_bundle
    elif isinstance(scenes, dict):
        bundle = dict(scenes)
        explicit_bundle_fields = set(bundle)
        lifted_fields = {}
        for scene in bundle.get("scenes", []):
            if not isinstance(scene, dict):
                continue
            for key in ("version", "title", "dimension", "orientation", "materials"):
                if key not in scene:
                    continue
                nested_value = scene.pop(key)
                previous = lifted_fields.get(key, bundle.get(key) if key in explicit_bundle_fields else None)
                if previous is not None and previous != nested_value:
                    raise ValueError(f"Conflicting scene bundle field: {key}")
                lifted_fields[key] = nested_value
                if key not in explicit_bundle_fields:
                    bundle[key] = nested_value
        for key in ("version", "title", "dimension", "orientation", "materials"):
            if key in normalized:
                outer_value = normalized.pop(key)
                previous = bundle.get(key) if key in explicit_bundle_fields or key in lifted_fields else None
                if previous is not None and previous != outer_value:
                    raise ValueError(f"Conflicting scene bundle field: {key}")
                bundle[key] = outer_value
        bundle.setdefault("version", 2)
        bundle.setdefault("title", spec.name)
        bundle.setdefault("dimension", spec.dimension)
        bundle.setdefault("orientation", spec.orientation)
        bundle.setdefault("materials", [])
        normalized["scenes"] = bundle
    # These fields belong to Implementation, even when a model nests them in
    # the scene envelope. Conflicting copies are errors, never silently erased.
    bundle = normalized.get("scenes")
    if isinstance(bundle, dict):
        for key in ("probes", "implemented_requirement_ids", "limitations"):
            if key in bundle:
                if key in normalized and normalized[key] != bundle[key]:
                    raise ValueError(f"Conflicting implementation field: {key}")
                normalized[key] = bundle.pop(key)
        for scene in bundle.get("scenes", []):
            if not isinstance(scene, dict):
                continue
            for obj in scene.get("objects", []):
                if not isinstance(obj, dict):
                    continue
                for component in obj.get("components", []):
                    if not isinstance(component, dict):
                        continue
                    properties = component.get("properties")
                    if isinstance(properties, dict):
                        # Only typed maps are losslessly convertible. Raw Unity
                        # values need actual member types (int vs float, enum vs
                        # string, Component vs GameObject); don't guess them.
                        if all(isinstance(p, dict) and "kind" in p and "value" in p
                               and "name" not in p for p in properties.values()):
                            component["properties"] = [dict(name=name, **prop)
                                                       for name, prop in properties.items()]
    return normalized


class PlanReview(Contract):
    missing_requirement_ids: list[str] = Field(default_factory=list)
    missing_prompt_instructions: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class GameUIPlan(Contract):
    screens: list[str] = Field(default_factory=list, max_length=30)
    controls: list[str] = Field(min_length=1, max_length=30)
    layout: list[str] = Field(default_factory=list, max_length=30)
    accessibility: list[str] = Field(default_factory=list, max_length=20)


def load_game_spec(value):
    if isinstance(value, str):
        value = json.loads(value)
    return (GameSpecV2 if value.get("version") == 2 else GameSpec).model_validate(value)


def validate_prompt_trace(spec, request):
    """Require actual prompt excerpts and keep all status outside model control."""
    for req in spec.requirements:
        if req.original_instruction not in request:
            raise ValueError(f"{req.id}: original_instruction must quote the user's prompt exactly")


def explicit_requested_dimension(request):
    """Return a clearly stated 2D or 3D game dimension from a user request."""
    text = str(request or "")
    mentions = []
    patterns = (("2D", r"(?<![A-Za-z0-9])(?:2\s*D|two[ -]?dimensional)(?![A-Za-z0-9])"),
                ("3D", r"(?<![A-Za-z0-9])(?:3\s*D|three[ -]?dimensional)(?![A-Za-z0-9])"))
    game_mentions = []
    for dimension, pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            prefix = text[max(0, match.start() - 18):match.start()].casefold()
            if re.search(r"\b(?:not|no|without|avoid|instead of)\s+(?:a\s+)?$", prefix):
                continue
            mentions.append((match.start(), dimension))
            following = text[match.end():match.end() + 70]
            preceding = text[max(0, match.start() - 45):match.start()]
            if (re.match(r"(?:\s+[\w'-]+){0,5}\s+(?:game|gameplay|platformer|racing|shooter|puzzle|"
                         r"simulation|roguelike|adventure|strategy)\b", following, re.IGNORECASE)
                    or re.search(r"\b(?:game|gameplay|world|levels?)\s+(?:in|as|for)\s+$",
                                 preceding, re.IGNORECASE)):
                game_mentions.append((match.start(), dimension))
    game_dimensions = {dimension for _, dimension in game_mentions}
    if len(game_dimensions) == 1:
        return next(iter(game_dimensions))
    dimensions = {dimension for _, dimension in mentions}
    if len(dimensions) == 1:
        return next(iter(dimensions))
    return None


def enforce_requested_dimension(request, spec):
    """Make an unambiguous explicit prompt dimension authoritative over planner drift."""
    if getattr(spec, "version", 1) != 2:
        return None
    requested = explicit_requested_dimension(request)
    if requested is None or requested == spec.dimension:
        return None
    planned = spec.dimension
    spec.dimension = requested
    return {"requested": requested, "planned": planned}


def align_tooling_requirements(spec):
    """Route build/delivery/workflow checks outside the gameplay harness.

    Preserve the original instructions and all gameplay probes. Structural
    requirements remain unverified in reports until reviewed; this never marks
    an APK, a Telegram delivery or a repair loop as a passing gameplay test.
    """
    if getattr(spec, "version", 1) != 2:
        return []
    changes = []
    gameplay = re.compile(r"\b(?:move|movement|jump|jumping|collect|collection|collision|collisions|touch|controls|player|enemy|enemies|score|win|victory|gameplay)\b", re.I)
    for req in spec.requirements:
        text = req.original_instruction
        if req.verification_method != "runtime" or gameplay.search(text):
            continue
        tooling = (re.search(r"\bAPK\b|\bTelegram\b", text, re.I) or
                   re.search(r"\brepair\b.*\b(?:retry|budget|limits)\b", text, re.I))
        if tooling:
            req.verification_method = "structure"
            changes.append({"requirement_id": req.id, "from": "runtime", "to": "structure",
                            "reason": "build/delivery/supervisor evidence is outside Unity gameplay probes"})
    return changes


def _property_vector(value, size, context):
    try:
        parsed = json.loads(value) if isinstance(value, str) and value.lstrip().startswith("[") else value
        if isinstance(parsed, str):
            parsed = [part.strip() for part in parsed.split(",")]
        if not isinstance(parsed, (list, tuple)) or len(parsed) != size:
            raise ValueError
        result = tuple(float(part) for part in parsed)
        if not all(math.isfinite(part) for part in result):
            raise ValueError
        return result
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{context} must contain exactly {size} finite numbers") from error


def _validate_scene_values(spec, plan):
    for scene in plan.scenes.scenes:
        objects = {obj.id: obj for obj in scene.objects}
        for obj in scene.objects:
            if any(abs(value) < 1e-6 for value in obj.scale):
                raise ValueError(f"Object {obj.id!r} has a zero transform scale")
            for component in obj.components:
                properties = {}
                for prop in component.properties:
                    key = prop.name.casefold()
                    if key in properties:
                        raise ValueError(f"Object {obj.id!r} declares property {prop.name!r} more than once")
                    properties[key] = prop
                    context = f"{scene.name}/{obj.id}/{component.type}.{prop.name}"
                    try:
                        if prop.kind in ("float", "int"):
                            number = float(prop.value)
                            if not math.isfinite(number) or (prop.kind == "int" and not number.is_integer()):
                                raise ValueError
                        elif prop.kind == "bool" and prop.value.casefold() not in ("true", "false"):
                            raise ValueError
                        elif prop.kind == "color" and not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", prop.value):
                            raise ValueError
                        elif prop.kind == "vector2":
                            _property_vector(prop.value, 2, context)
                        elif prop.kind == "vector3":
                            _property_vector(prop.value, 3, context)
                    except (TypeError, ValueError) as error:
                        raise ValueError(f"Invalid value for {context}: {prop.value!r}") from error
                    if prop.kind == "reference":
                        target_id, separator, target_type = prop.value.partition(":")
                        target = objects.get(target_id)
                        if target is None:
                            raise ValueError(f"Object {obj.id!r} has a missing reference target {target_id!r}")
                        if separator and target_type not in {"UnityEngine.Transform", "Transform",
                                                              "UnityEngine.GameObject", "GameObject"} and target_type not in {
                                item.type for item in target.components} and target_type.rsplit(".", 1)[-1] not in {
                                item.type.rsplit(".", 1)[-1] for item in target.components}:
                            raise ValueError(f"Reference {prop.value!r} names a component not declared on {target_id!r}")
                    if prop.kind == "asset" and (not prop.value.startswith("Assets/") or ".." in prop.value or "\\" in prop.value):
                        raise ValueError(f"Object {obj.id!r} has an invalid asset reference {prop.value!r}")

                component_name = component.type.rsplit(".", 1)[-1]
                vector_size = 2 if component_name.endswith("2D") else 3
                vector_fields = {"BoxCollider2D": ("size",), "BoxCollider": ("size",),
                                 "CapsuleCollider2D": ("size",), "CapsuleCollider": ("size",)}
                for field in vector_fields.get(component_name, ()):
                    prop = properties.get(field.casefold())
                    if prop is None:
                        continue
                    dimensions = _property_vector(prop.value, vector_size,
                        f"{scene.name}/{obj.id}/{component.type}.{field}")
                    if any(value <= 0 for value in dimensions):
                        raise ValueError(f"Collider dimensions must be positive: {scene.name}/{obj.id}/{component.type}.{field}")
                radius_fields = {"CircleCollider2D": ("radius",), "SphereCollider": ("radius",),
                                 "CapsuleCollider": ("radius", "height")}
                for field in radius_fields.get(component_name, ()):
                    prop = properties.get(field)
                    if prop is not None:
                        try:
                            valid = math.isfinite(float(prop.value)) and float(prop.value) > 0
                        except (TypeError, ValueError):
                            valid = False
                        if not valid:
                            raise ValueError(f"Collider {field} must be a positive finite number: {scene.name}/{obj.id}/{component.type}")


def validate_implementation(spec, plan):
    reqs = {r.id for r in spec.requirements}
    if [s.name for s in plan.scenes.scenes] != spec.scenes or plan.scenes.dimension != spec.dimension:
        raise ValueError("Implementation changed scene structure or dimension. "
                         f"Expected {spec.dimension} scenes {spec.scenes!r}; received "
                         f"{plan.scenes.dimension} scenes {[s.name for s in plan.scenes.scenes]!r}")
    if plan.scenes.orientation != spec.orientation:
        raise ValueError("Implementation changed requested orientation")
    _validate_scene_values(spec, plan)
    if not set(plan.implemented_requirement_ids) <= reqs:
        raise ValueError("Unknown implementation requirement")
    if len({p.id for p in plan.probes}) != len(plan.probes):
        raise ValueError("Duplicate probe IDs")
    materials = {m.id for m in plan.scenes.materials}
    if len(materials) != len(plan.scenes.materials):
        raise ValueError("Duplicate material IDs")
    for scene in plan.scenes.scenes:
        if not set(scene.requirement_ids) <= reqs:
            raise ValueError("Unknown scene requirement")
        if not any(component.type.rsplit(".", 1)[-1] == "Camera"
                   for obj in scene.objects for component in obj.components):
            raise ValueError(f"Scene {scene.name!r} must explicitly declare a Camera so the game is visible.")
        if spec.dimension == "mixed" and not any(
                component.type.rsplit(".", 1)[-1] == "Camera" and any(
                    prop.name.casefold() == "orthographic" for prop in component.properties)
                for obj in scene.objects for component in obj.components):
            raise ValueError(f"Mixed-dimension scene {scene.name!r} must explicitly set its Camera projection.")
        for obj in scene.objects:
            if not set(obj.requirement_ids) <= reqs or (obj.material and obj.material not in materials):
                raise ValueError("Unknown object requirement or material")
            component_names = {component.type.rsplit(".", 1)[-1] for component in obj.components}
            if len(component_names) != len(obj.components):
                raise ValueError(f"Object {obj.id!r} declares duplicate component types")
            two_d = component_names.intersection({"Rigidbody2D", "BoxCollider2D", "CircleCollider2D", "CapsuleCollider2D", "PolygonCollider2D", "EdgeCollider2D", "CompositeCollider2D"})
            three_d = component_names.intersection({"Rigidbody", "BoxCollider", "SphereCollider", "CapsuleCollider", "MeshCollider", "CharacterController"})
            if two_d and three_d:
                raise ValueError(f"Object {obj.id!r} mixes 2D and 3D physics")
            if spec.dimension == "2D" and three_d:
                raise ValueError(f"2D game object {obj.id!r} declares 3D physics components")
            if spec.dimension == "3D" and two_d:
                raise ValueError(f"3D game object {obj.id!r} declares 2D physics components")
            graphics = component_names.intersection({"Image", "RawImage", "Text", "TextMeshProUGUI", "TMP_Text"})
            if len(graphics) > 1:
                raise ValueError(f"Object {obj.id!r} has multiple UGUI Graphic components; place Text labels on child objects.")
            if "Button" in component_names and not graphics.intersection({"Image", "RawImage", "Text", "TextMeshProUGUI", "TMP_Text"}):
                raise ValueError(f"Button {obj.id!r} needs a Graphic component such as Image.")
        _validate_screen_ui_bounds(scene)
    for probe in plan.probes:
        if not set(probe.requirement_ids) <= reqs or probe.scene not in spec.scenes:
            raise ValueError("Unknown probe requirement or scene")
        objects = next(s.objects for s in plan.scenes.scenes if s.name == probe.scene)
        ids = {o.id for o in objects}
        # Unity permits repeated display names (for example a Text child on
        # several buttons). Stable IDs remain unique; aliases must be unambiguous.
        names = {o.name: o.id for o in objects if sum(other.name == o.name for other in objects) == 1}
        available = ", ".join(sorted(ids))
        for section, items in (("steps", probe.steps), ("assertions", probe.assertions)):
            for index, item in enumerate(items):
                # These operations never look up an object in the Unity harness.
                if section == "steps" and item.action in ("wait", "key"):
                    continue
                if section == "assertions" and item.kind == "scene":
                    continue
                # Models sometimes use the display name instead of the stable ID.
                # Resolve unique display names only; never guess among duplicates.
                if item.object_id not in ids and item.object_id in names:
                    item.object_id = names[item.object_id]
                if item.object_id not in ids:
                    raise ValueError(
                        f"Unknown probe object {item.object_id!r}: probe {probe.id!r}, "
                        f"scene {probe.scene!r}, {section}[{index}]. "
                        f"Use an object ID declared in that scene. Available IDs: {available}")
    covered = {rid for p in plan.probes for rid in p.requirement_ids}
    missing = {r.id for r in spec.requirements if r.verification_method == "runtime"} - covered
    if missing:
        raise ValueError("Missing behavioral probes for runtime requirements: " + ", ".join(sorted(missing)))
    return plan


def _parse_ui_vector(value, fallback):
    if value is None:
        return fallback
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if isinstance(parsed, (list, tuple)) and len(parsed) >= 2:
            return (float(parsed[0]), float(parsed[1]))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return fallback


def _validate_screen_ui_bounds(scene):
    """Catch centered UI rectangles that the generated Canvas would clip."""
    by_id = {obj.id: obj for obj in scene.objects}
    canvas = next((obj for obj in scene.objects if any(
        component.type.rsplit(".", 1)[-1] == "Canvas" for component in obj.components)), None)
    if canvas is None:
        return
    canvas_components = {component.type.rsplit(".", 1)[-1]: component for component in canvas.components}
    canvas_props = {prop.name.casefold(): prop.value for component in canvas.components for prop in component.properties}
    if canvas_props.get("rendermode", "ScreenSpaceOverlay").casefold() in {"worldspace", "2"}:
        return
    scaler = canvas_components.get("CanvasScaler")
    reference = next((prop.value for prop in scaler.properties if prop.name.casefold() == "referenceresolution"), None) if scaler else None
    canvas_size = _parse_ui_vector(reference, (800.0, 600.0))
    rect_cache = {canvas.id: ((0.0, 0.0), canvas_size)}

    def rect_for(obj):
        if obj.id in rect_cache:
            return rect_cache[obj.id]
        parent = by_id.get(obj.parent)
        if parent is None:
            return ((0.0, 0.0), (0.0, 0.0))
        parent_center, parent_size = rect_for(parent)
        rect_component = next((component for component in obj.components
            if component.type.rsplit(".", 1)[-1] == "RectTransform"), None)
        props = {prop.name.casefold(): prop.value for prop in rect_component.properties} if rect_component else {}
        anchor_min = _parse_ui_vector(props.get("anchormin"), (0.5, 0.5))
        anchor_max = _parse_ui_vector(props.get("anchormax"), anchor_min)
        pivot = _parse_ui_vector(props.get("pivot"), (0.5, 0.5))
        size_delta = _parse_ui_vector(props.get("sizedelta"), (100.0, 100.0))
        size = (size_delta[0] + (anchor_max[0] - anchor_min[0]) * parent_size[0],
                size_delta[1] + (anchor_max[1] - anchor_min[1]) * parent_size[1])
        anchor_center = ((anchor_min[0] + anchor_max[0]) * 0.5,
                         (anchor_min[1] + anchor_max[1]) * 0.5)
        pivot_position = (parent_center[0] + (anchor_center[0] - 0.5) * parent_size[0] + obj.position[0],
                          parent_center[1] + (anchor_center[1] - 0.5) * parent_size[1] + obj.position[1])
        center = (pivot_position[0] + (0.5 - pivot[0]) * size[0],
                  pivot_position[1] + (0.5 - pivot[1]) * size[1])
        rect_cache[obj.id] = (center, size)
        return rect_cache[obj.id]

    for obj in scene.objects:
        graphic = any(component.type.rsplit(".", 1)[-1] in
                      {"Image", "RawImage", "Text", "TextMeshProUGUI", "TMP_Text", "Button"}
                      for component in obj.components)
        has_rect_size = any(prop.name.casefold() == "sizedelta"
                            for component in obj.components for prop in component.properties)
        if not graphic or not has_rect_size:
            continue
        parent_id = obj.parent
        under_canvas = False
        while parent_id and parent_id in by_id:
            if parent_id == canvas.id:
                under_canvas = True
                break
            parent_id = by_id[parent_id].parent
        if not under_canvas:
            continue
        center, size = rect_for(obj)
        left, right = center[0] - size[0] / 2, center[0] + size[0] / 2
        bottom, top = center[1] - size[1] / 2, center[1] + size[1] / 2
        if (left < -canvas_size[0] / 2 - 1 or right > canvas_size[0] / 2 + 1 or
                bottom < -canvas_size[1] / 2 - 1 or top > canvas_size[1] / 2 + 1):
            raise ValueError(
                f"UI object {obj.id!r} extends beyond the {canvas_size[0]:g}x{canvas_size[1]:g} "
                "Canvas reference frame. Move or resize it so it stays on-screen.")


def requirement_report(spec, plan=None, runtime=None, assets=None):
    """Only fresh harness evidence can pass a runtime requirement.

    Structural/visual review remains unverified until suitable evidence exists;
    generated labels or a successful compile cannot turn a requirement green.
    """
    results = {r.id: dict(r.model_dump(), implementation_status="planned", verification_result="not_run", evidence=[])
               for r in spec.requirements}
    if not plan:
        return list(results.values())
    for key in plan.implemented_requirement_ids:
        results[key]["implementation_status"] = "implemented_unverified"
    records = (runtime or {}).get("checks", [])
    by_id = {r.get("id"): r for r in records}
    for req in spec.requirements:
        probes = [p for p in plan.probes if req.id in p.requirement_ids]
        if probes and req.verification_method == "runtime":
            evidence = [by_id.get(p.id) for p in probes]
            result = results[req.id]
            result["evidence"] = [e for e in evidence if e]
            if all(e and e.get("passed") is True for e in evidence):
                result["verification_result"] = "passed"
            elif any(e and e.get("passed") is False for e in evidence):
                result["verification_result"] = "failed"
        if req.verification_method != "runtime":
            results[req.id]["verification_result"] = "manual_review_required"
    for asset in assets or []:
        if asset.get("status") != "ready":
            for key in asset.get("requirement_ids", []):
                results[key]["implementation_status"] = "incomplete"
                results[key]["verification_result"] = "asset_unavailable"
    return list(results.values())
