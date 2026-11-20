"""GameSpec v2 and validated Unity instructions, independent of game genre.

Version 1 remains readable by the existing platformer pipeline. These are data
contracts and tools, not additional AI agents.
"""
import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from studio.schemas import Contract, GameSpec, CodeBundle, ObjectSpec, PropertySpec


class Requirement(Contract):
    id: str = Field(pattern=r"^R[0-9]{1,4}$")
    original_instruction: str = Field(min_length=1, max_length=4000)
    implementation: str = Field(min_length=1, max_length=2000)
    owner: Literal["supervisor", "designer", "developer", "art", "qa"]
    verification_method: Literal["runtime", "structure", "manual"]
    # Status is computed by the supervisor; a model cannot mark itself verified.


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
        def visit(key, active):
            if key not in tasks or key in active:
                raise ValueError("Missing or cyclic task dependency")
            for dep in tasks[key].dependencies:
                visit(dep, active | {key})
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
        if len(ids) != len(self.objects) or len({o.name for o in self.objects}) != len(ids):
            raise ValueError("Object IDs and names must be unique within a scene")
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
                        raise ValueError("Missing serialized reference target")
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


class Implementation(Contract):
    scripts: CodeBundle
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


def normalize_implementation_response(value, spec):
    """Adapt common flattened model output to the versioned implementation contract."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if isinstance(normalized.get("scripts"), list):
        normalized["scripts"] = {"scripts": normalized["scripts"]}

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
        bundle.setdefault("version", 2)
        bundle.setdefault("title", normalized.pop("title", spec.name))
        bundle.setdefault("dimension", normalized.pop("dimension", spec.dimension))
        bundle.setdefault("orientation", normalized.pop("orientation", spec.orientation))
        outer_materials = normalized.pop("materials", [])
        bundle.setdefault("materials", outer_materials)
        normalized["scenes"] = bundle
    return normalized


class PlanReview(Contract):
    missing_requirement_ids: list[str] = Field(default_factory=list)
    missing_prompt_instructions: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


def load_game_spec(value):
    if isinstance(value, str):
        value = json.loads(value)
    return (GameSpecV2 if value.get("version") == 2 else GameSpec).model_validate(value)


def validate_prompt_trace(spec, request):
    """Require actual prompt excerpts and keep all status outside model control."""
    for req in spec.requirements:
        if req.original_instruction not in request:
            raise ValueError(f"{req.id}: original_instruction must quote the user's prompt exactly")


def validate_implementation(spec, plan):
    reqs = {r.id for r in spec.requirements}
    if [s.name for s in plan.scenes.scenes] != spec.scenes or plan.scenes.dimension != spec.dimension:
        raise ValueError("Implementation changed scene structure or dimension")
    if plan.scenes.orientation != spec.orientation:
        raise ValueError("Implementation changed requested orientation")
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
        for obj in scene.objects:
            if not set(obj.requirement_ids) <= reqs or (obj.material and obj.material not in materials):
                raise ValueError("Unknown object requirement or material")
            component_names = {component.type.rsplit(".", 1)[-1] for component in obj.components}
            graphics = component_names.intersection({"Image", "RawImage", "Text", "TextMeshProUGUI", "TMP_Text"})
            if len(graphics) > 1:
                raise ValueError(f"Object {obj.id!r} has multiple UGUI Graphic components; place Text labels on child objects.")
            if "Button" in component_names and not graphics.intersection({"Image", "RawImage", "Text", "TextMeshProUGUI", "TMP_Text"}):
                raise ValueError(f"Button {obj.id!r} needs a Graphic component such as Image.")
    for probe in plan.probes:
        if not set(probe.requirement_ids) <= reqs or probe.scene not in spec.scenes:
            raise ValueError("Unknown probe requirement or scene")
        objects = next(s.objects for s in plan.scenes.scenes if s.name == probe.scene)
        ids = {o.id for o in objects}
        names = {o.name: o.id for o in objects}
        available = ", ".join(sorted(ids))
        for section, items in (("steps", probe.steps), ("assertions", probe.assertions)):
            for index, item in enumerate(items):
                # These operations never look up an object in the Unity harness.
                if section == "steps" and item.action in ("wait", "key"):
                    continue
                if section == "assertions" and item.kind == "scene":
                    continue
                # Models sometimes use the display name instead of the stable ID.
                # Names are unique within a scene; never guess a different object.
                if item.object_id not in ids and item.object_id in names:
                    item.object_id = names[item.object_id]
                if item.object_id not in ids:
                    raise ValueError(
                        f"Unknown probe object {item.object_id!r}: probe {probe.id!r}, "
                        f"scene {probe.scene!r}, {section}[{index}]. "
                        f"Use an object ID declared in that scene. Available IDs: {available}")
    return plan


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
