"""Deterministic integration fixtures, NOT templates used by the application.

Keep these separate from live AI results: they verify contracts/tools only.
"""
from studio.flexible import GameSpecV2, Implementation


REQUESTS = {
    "platformer": "Create a simple 2D game with jumping and collectible objects.",
    "racing": "Create a simple 3D driving game with a vehicle, track, camera, and finish condition.",
    "puzzle": "Create a puzzle game with interactive elements and a win condition.",
}


def prop(name, value, kind="float"):
    return dict(name=name,kind=kind,value=str(value))


def comp(name, *props):
    return dict(type=name,properties=list(props))


def obj(key, position=(0,0,0), components=(), **kwargs):
    return dict(id=key,name=key,position=position,components=list(components),**kwargs)


def examples(kind):
    request=REQUESTS[kind]
    if kind=="platformer":
        dimension="2D"; mechanics=["jumping","collectible objects"]
        camera=comp("UnityEngine.Camera",prop("orthographic","true","bool"),prop("orthographicSize",6))
        objects=[obj("camera",(0,2,-10),[camera],tag="MainCamera"),
            obj("floor",(0,-1,0),[comp("UnityEngine.BoxCollider2D",prop("size","[20,1]","vector2"))]),
            obj("runner",(0,1,0),[comp("UnityEngine.Rigidbody2D",prop("gravityScale",2)),comp("UnityEngine.BoxCollider2D"),comp("JumpCollector")]),
            obj("collectible",(3,0,0),[comp("UnityEngine.CircleCollider2D",prop("isTrigger","true","bool"))],tag="Finish")]
        scripts=[dict(filename="JumpCollector.cs",content='''using UnityEngine;
using UnityEngine.InputSystem;
public class JumpCollector : MonoBehaviour {
    public int collected; Rigidbody2D body;
    public float Height => transform.position.y;
    void Awake() { body=GetComponent<Rigidbody2D>(); }
    void Update() { if(Keyboard.current!=null && Keyboard.current.spaceKey.wasPressedThisFrame) Jump(); }
    public void Jump() { body.linearVelocity=new Vector2(0,9); }
    void OnTriggerEnter2D(Collider2D other) { if(other.CompareTag("Finish")) { collected++; Destroy(other.gameObject); } }
}''')]
        probes=[dict(id="jump",scene="Game",requirement_ids=["R1"],
            steps=[dict(action="wait",frames=60),dict(action="key",member="Space",value="true",frames=5)],
            assertions=[dict(kind="property",object_id="runner",component="UnityEngine.Rigidbody2D",member="linearVelocity",value="unused")]),
            dict(id="collect",scene="Game",requirement_ids=["R2"],
            steps=[dict(action="set",object_id="runner",component="UnityEngine.Transform",member="position",value="[3,0,0]",frames=10)],
            assertions=[dict(kind="property",object_id="runner",component="JumpCollector",member="collected",comparison="greater",value="0")])]
        # Check height gain rather than treating compile/labels as behavior.
        probes[0]["assertions"]=[dict(kind="property",object_id="runner",component="JumpCollector",member="Height",comparison="greater",value="0.5")]
    elif kind=="racing":
        dimension="3D"; mechanics=["vehicle", "finish condition"]
        objects=[obj("camera",(0,6,-9),[comp("UnityEngine.Camera",prop("orthographic","false","bool")),comp("RaceCamera",prop("target","car:UnityEngine.Transform","reference"))],tag="MainCamera",rotation=(20,0,0)),
            obj("light",(0,5,0),[comp("UnityEngine.Light",prop("type","Directional","enum")),],rotation=(45,30,0)),
            obj("track",(0,-.5,10),[],primitive="Cube",scale=(8,1,40),material="road"),
            obj("car",(0,1,0),[comp("UnityEngine.Rigidbody",prop("constraints","FreezeRotation","enum")),comp("RaceCar")],primitive="Cube",scale=(1,.7,2),material="carpaint"),
            obj("finish",(0,1,5),[comp("UnityEngine.BoxCollider",prop("isTrigger","true","bool"))],scale=(8,3,.5),tag="Finish")]
        scripts=[dict(filename="RaceCar.cs",content='''using UnityEngine;
using UnityEngine.InputSystem;
public class RaceCar : MonoBehaviour {
    public bool won;
    void Update() { if(Keyboard.current!=null && Keyboard.current.wKey.isPressed) Drive(); }
    public void Drive() { GetComponent<Rigidbody>().linearVelocity=new Vector3(0,0,8); }
    void OnTriggerEnter(Collider other) { if(other.CompareTag("Finish")) won=true; }
}'''),dict(filename="RaceCamera.cs",content='''using UnityEngine;
public class RaceCamera : MonoBehaviour { public Transform target; void LateUpdate() { if(target!=null) transform.position=target.position+new Vector3(0,6,-9); } }''')]
        probes=[dict(id="drive_to_finish",scene="Game",requirement_ids=["R1","R2"],
            steps=[dict(action="key",member="W",value="true",frames=180)],
            assertions=[dict(kind="position_changed",object_id="car",value="2"),dict(kind="property",object_id="car",component="RaceCar",member="won",value="true")])]
    else:
        dimension="2D"; mechanics=["interactive elements", "win condition"]
        objects=[obj("camera",(0,0,-10),[comp("UnityEngine.Camera",prop("orthographic","true","bool"))],tag="MainCamera"),
            obj("puzzle",(0,0,0),[comp("TogglePuzzle")])]
        scripts=[dict(filename="TogglePuzzle.cs",content='''using UnityEngine;
using UnityEngine.InputSystem;
public class TogglePuzzle : MonoBehaviour {
    public bool solved; public int moves;
    void Update() { if(Keyboard.current!=null && Keyboard.current.spaceKey.wasPressedThisFrame) Toggle(); }
    public void Toggle() { moves++; solved=moves==1; }
}''')]
        probes=[dict(id="solve",scene="Game",requirement_ids=["R1","R2"],
            steps=[dict(action="key",member="Space",value="true",frames=5)],
            assertions=[dict(kind="property",object_id="puzzle",component="TogglePuzzle",member="moves",comparison="greater",value="0"),dict(kind="property",object_id="puzzle",component="TogglePuzzle",member="solved",value="true")])]
    requirements=[dict(id=f"R{i+1}",original_instruction=m,implementation=m,owner="developer",verification_method="runtime") for i,m in enumerate(mechanics)]
    spec=GameSpecV2(name=kind.title()+" test",genre=kind,dimension=dimension,
        camera_perspective="third person" if dimension=="3D" else "side" if kind=="platformer" else "front",
        target_platform="Desktop",art_style="geometric test fixture",mechanics=mechanics,
        controls=["W to drive"] if kind=="racing" else ["Space"],level_structure="one scene",scenes=["Game"],requirements=requirements,
        tasks=[dict(id="T1",description="Implement requested mechanics",owner="developer",requirement_ids=["R1","R2"])],
        assumptions=["Desktop controls chosen for unspecified target"])
    materials=[dict(id="road",color="#303030"),dict(id="carpaint",color="#E54030")] if kind=="racing" else []
    plan=Implementation.model_validate(dict(scripts={"scripts":scripts},scenes=dict(version=2,title=spec.name,dimension=dimension,scenes=[dict(name="Game",objects=objects)],materials=materials),probes=probes,implemented_requirement_ids=["R1","R2"]))
    return request,spec,plan
