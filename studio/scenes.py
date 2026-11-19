from studio.schemas import CollisionRule, ComponentSpec, ObjectSpec, PropertySpec, SceneBundle, SceneSpec


def component(type_name, **properties):
    values = []
    for name, value in properties.items():
        kind = "bool" if isinstance(value, bool) else "int" if isinstance(value, int) else "float" if isinstance(value, float) else "string"
        if isinstance(value, tuple):
            kind, value = value
        values.append(PropertySpec(name=name, kind=kind, value=str(value).lower() if kind == "bool" else str(value)))
    return ComponentSpec(type=type_name, properties=values)


def assemble_scenes(spec, ui, art):
    scenes = []
    level_names = {level.name for level in spec.levels}
    menu_name = next(name for name in spec.scenes if name not in level_names)
    result_name = next(name for name in reversed(spec.scenes) if name not in level_names and name != menu_name)
    for index, name in enumerate(spec.scenes):
        kind = "level" if name in level_names else "menu" if name == menu_name else "result"
        director = ObjectSpec(id="director", name="Game Director", role="controller", position=(0,0,0),
            components=[component("Studio.Runtime.GameDirector", title=ui.title, subtitle=ui.subtitle,
                firstScene=spec.levels[0].name, menuScene=menu_name, resultScene=result_name, sceneKind=kind,
                playLabel=ui.play_label, touchButtonSize=ui.touch_button_size,
                referenceWidth=ui.reference_width,referenceHeight=ui.reference_height,accentColor=("color",ui.accent),
                nextScene=(spec.levels[min(len(spec.levels)-1, [l.name for l in spec.levels].index(name)+1)].name if name in level_names and name != spec.levels[-1].name else result_name)),
                component("GeneratedRules")])
        objects = [director]
        if "background" in art:
            objects.append(ObjectSpec(id="backdrop", name="Forest backdrop", position=(0,2,5),
                scale=(24,13.5,1), sprite=art["background"], sorting_order=-20,
                components=[component("Studio.Runtime.ParallaxBackdrop", factor=1.0)]))
        if kind == "level":
            level = next(l for l in spec.levels if l.name == name)
            objects += [ObjectSpec(id="ground", name="Ground", role="ground", position=(level.length/2-3,-1,0),
                scale=(level.length+6,1.5,1), sprite=art["ground"], tag="Ground", layer=8,
                components=[component("UnityEngine.BoxCollider2D")]),
                ObjectSpec(id="player", name="Explorer", role="player", position=(0,1,0), scale=(0.8,1.15,1),
                sprite=art["player"], tag="Player", layer=9, prefab=True,
                components=[component("UnityEngine.Rigidbody2D", gravityScale=2.5, freezeRotation=True),
                    component("UnityEngine.BoxCollider2D"), component("Studio.Runtime.PlayerMotor", moveSpeed=spec.move_speed,
                    jumpSpeed=spec.jump_speed, director=("reference", "director:Studio.Runtime.GameDirector"))])]
            for i in range(level.platforms):
                x = 4 + i * (level.length-9)/level.platforms
                y = 0.8 + (i%2)*1.2
                objects.append(ObjectSpec(id=f"platform{i}", name=f"Grass ledge {i+1}", role="platform",
                    position=(x,y,0), scale=(3,0.45,1), sprite=art["ground"], layer=8, tag="Ground",
                    components=[component("UnityEngine.BoxCollider2D")]))
            for i in range(level.coins):
                x = 2 + i * (level.length-5)/level.coins
                objects.append(ObjectSpec(id=f"coin{i}", name=f"Coin {i+1}", role="coin", position=(x,0.8,0),
                    scale=(0.4,0.4,1), sprite=art["coin"], sorting_order=2, prefab=i==0,
                    components=[component("UnityEngine.CircleCollider2D", isTrigger=True),
                        component("Studio.Runtime.CoinPickup", director=("reference", "director:Studio.Runtime.GameDirector"))]))
            for i in range(level.enemies):
                x = 10 + i * 9
                objects.append(ObjectSpec(id=f"enemy{i}", name=f"Patrol {i+1}", role="enemy", position=(x,0.25,0),
                    scale=(0.8,0.8,1), sprite=art.get("enemy",art["coin"]), prefab=i==0,
                    components=[component("UnityEngine.BoxCollider2D", isTrigger=True),
                        component("Studio.Runtime.PatrolEnemy", distance=2.0, speed=1.2)]))
            objects.append(ObjectSpec(id="exit", name="Finish beacon", role="exit", position=(level.length-3,1.2,0),
                scale=(0.6,2.7,1), sprite=art["coin"], color=ui.accent,
                components=[component("UnityEngine.BoxCollider2D", isTrigger=True),
                    component("Studio.Runtime.LevelExit", director=("reference", "director:Studio.Runtime.GameDirector"))]))
        scenes.append(SceneSpec(name=name, kind=kind, objects=objects, background_color=spec.palette[0]))
    return SceneBundle(title=spec.name, orientation=spec.orientation, palette=spec.palette, scenes=scenes,
                       collision_rules=[CollisionRule(layer_a=9, layer_b=9, ignore=True)])
