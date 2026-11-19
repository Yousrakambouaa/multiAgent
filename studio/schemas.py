"""Versioned contracts; nested arbitrary scene dictionaries are never accepted."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AssetSpec(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    kind: Literal["player", "ground", "coin", "enemy", "background", "decoration"]
    prompt: str = Field(min_length=5, max_length=1500)
    width: int = Field(default=256, ge=64, le=2048)
    height: int = Field(default=256, ge=64, le=2048)
    transparent: bool = True
    pixels_per_unit: int = Field(default=128, ge=16, le=1024)


class LevelSpec(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    length: int = Field(default=36, ge=24, le=80)
    platforms: int = Field(default=5, ge=3, le=12)
    coins: int = Field(default=10, ge=4, le=24)
    enemies: int = Field(default=2, ge=0, le=5)


class GameSpec(Contract):
    version: int = 1
    name: str = Field(min_length=3, max_length=70)
    genre: Literal["platformer"]
    target_platform: Literal["Android", "Desktop"] = "Android"
    orientation: Literal["landscape", "portrait"] = "landscape"
    art_style: str = Field(min_length=5, max_length=500)
    palette: list[str] = Field(min_length=3, max_length=6)
    player_mechanics: list[str] = Field(min_length=2, max_length=10)
    input_requirements: list[str] = Field(min_length=2, max_length=8)
    enemies: list[str] = Field(max_length=5)
    obstacles: list[str] = Field(max_length=8)
    collectibles: list[str] = Field(min_length=1, max_length=5)
    levels: list[LevelSpec] = Field(min_length=1, max_length=3)
    ui_requirements: list[str] = Field(min_length=2, max_length=10)
    required_assets: list[AssetSpec] = Field(min_length=4, max_length=8)
    scenes: list[str] = Field(min_length=3, max_length=5)
    transitions: list[str] = Field(min_length=2, max_length=8)
    acceptance_criteria: list[str] = Field(min_length=6, max_length=15)
    move_speed: float = Field(default=6, ge=3, le=9)
    jump_speed: float = Field(default=11, ge=9, le=15)

    @field_validator("palette")
    @classmethod
    def colors(cls, value):
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in value):
            raise ValueError("Palette colors must be #RRGGBB")
        return value

    @model_validator(mode="after")
    def cross_references(self):
        names = [level.name for level in self.levels]
        if len(set(self.scenes)) != len(self.scenes) or not set(names).issubset(self.scenes):
            raise ValueError("Level scenes must appear in the unique scene list")
        if len(self.scenes)!=len(names)+2 or self.scenes[0] in names or self.scenes[-1] in names:
            raise ValueError("Scenes must start with a menu, include the levels, and end with a result scene")
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", s) for s in self.scenes):
            raise ValueError("Unsafe scene name")
        kinds = {a.kind for a in self.required_assets}
        if not {"player", "ground", "coin", "background"}.issubset(kinds):
            raise ValueError("Missing required platformer art")
        if len({a.id for a in self.required_assets}) != len(self.required_assets):
            raise ValueError("Duplicate asset IDs")
        return self


class PropertySpec(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    kind: Literal["float", "int", "bool", "string", "reference", "color"]
    value: str


class ComponentSpec(Contract):
    type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.]*$")
    properties: list[PropertySpec] = Field(default_factory=list, max_length=20)


class ObjectSpec(Contract):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str
    role: str = "decoration"
    parent: str = ""
    position: tuple[float, float, float]
    scale: tuple[float, float, float] = (1, 1, 1)
    tag: str = "Untagged"
    layer: int = Field(default=0, ge=0, le=31)
    sprite: str = ""
    color: str = "#FFFFFF"
    sorting_order: int = 0
    prefab: bool = False
    components: list[ComponentSpec] = Field(default_factory=list, max_length=12)

    @field_validator("sprite")
    @classmethod
    def asset_path(cls, value):
        if value and (not value.startswith("Assets/") or ".." in value or "\\" in value or ":" in value):
            raise ValueError("Sprite paths must stay within Assets")
        return value


class SceneSpec(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    kind: Literal["menu", "level", "result"]
    background_color: str = "#10243A"
    camera_size: float = Field(default=6, ge=3, le=16)
    objects: list[ObjectSpec] = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def references(self):
        ids = {o.id for o in self.objects}
        if len(ids) != len(self.objects):
            raise ValueError("Duplicate GameObject IDs")
        by_id = {o.id: o for o in self.objects}
        for obj in self.objects:
            if obj.parent and obj.parent not in ids:
                raise ValueError("Missing parent")
            seen = {obj.id}
            parent = obj.parent
            while parent:
                if parent in seen:
                    raise ValueError("Cyclic hierarchy")
                seen.add(parent)
                parent = by_id[parent].parent
            for component in obj.components:
                for prop in component.properties:
                    if prop.kind == "reference" and prop.value.split(":")[0] not in ids:
                        raise ValueError("Broken serialized reference")
        if self.kind == "level" and not {"player", "ground", "coin"}.issubset({o.role for o in self.objects}):
            raise ValueError("A platformer level requires player, ground and coins")
        return self


class CollisionRule(Contract):
    layer_a: int = Field(ge=0, le=31)
    layer_b: int = Field(ge=0, le=31)
    ignore: bool


class SceneBundle(Contract):
    version: int = 1
    title: str
    orientation: str
    palette: list[str]
    scenes: list[SceneSpec] = Field(min_length=3, max_length=5)
    collision_rules: list[CollisionRule] = Field(default_factory=list)


class UIPlan(Contract):
    title: str = Field(max_length=70)
    subtitle: str = Field(max_length=120)
    play_label: str = Field(max_length=30)
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    touch_button_size: int = Field(ge=80, le=160)
    reference_width: int = 1280
    reference_height: int = 720


class ScriptFile(Contract):
    filename: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*\.cs$")
    content: str = Field(min_length=30, max_length=60000)


class CodeBundle(Contract):
    scripts: list[ScriptFile] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique(self):
        if len({s.filename for s in self.scripts}) != len(self.scripts):
            raise ValueError("Duplicate script filename")
        return self
