using UnityEngine;
using UnityEngine.InputSystem;

[RequireComponent(typeof(Rigidbody2D), typeof(CapsuleCollider2D))]
public class FoxMotor : MonoBehaviour
{
    public Transform feet;
    public TrailHUD hud;
    public FoxPose art;
    Rigidbody2D body;
    CapsuleCollider2D shell;
    Vector2 spawn, footOffset;
    ContactFilter2D floorFilter;
    readonly Collider2D[] floorHits = new Collider2D[8];
    float buffer, grace, land;
    bool left, right;
    int axis;
    public bool Grounded { get; private set; }
    public float X => body.position.x;
    public float Y => body.position.y;
    public float VerticalSpeed => body.linearVelocity.y;
    public string State { get; private set; } = "Fall";
    public int Facing { get; private set; } = 1;
    public int Landings { get; private set; }

    void Awake()
    {
        body = GetComponent<Rigidbody2D>();
        shell = GetComponent<CapsuleCollider2D>();
        if (!body || !shell) { enabled = false; return; }
        spawn = body.position;
        footOffset = feet.position - transform.position;
        floorFilter.SetLayerMask(1 << 8);
        floorFilter.useTriggers = false;
    }

    public void Pad(string action, bool down)
    {
        if (action == "left") left = down;
        else if (action == "right") right = down;
        else if (action == "jump" && down) buffer = .13f;
    }

    void Update()
    {
        var k = Keyboard.current;
        bool l = left || (k != null && (k.aKey.isPressed || k.leftArrowKey.isPressed));
        bool r = right || (k != null && (k.dKey.isPressed || k.rightArrowKey.isPressed));
        axis = (r ? 1 : 0) - (l ? 1 : 0);
        if (k != null && k.spaceKey.wasPressedThisFrame) buffer = .13f;
    }

    bool OnFloor(Vector2 p)
    {
        int count = Physics2D.OverlapBox(p, new Vector2(.55f, .13f), 0, floorFilter, floorHits);
        for (int i = 0; i < count; i++)
        {
            var hit = floorHits[i];
            if (hit && hit != shell && hit.attachedRigidbody != body && hit.bounds.max.y <= p.y + .045f) return true;
        }
        return false;
    }

    void FixedUpdate()
    {
        if (hud.Won) { if (body.simulated) { body.linearVelocity = Vector2.zero; body.simulated = false; } return; }
        if (body.position.y < -5)
        {
            body.position = spawn;
            body.linearVelocity = Vector2.zero;
            buffer = grace = land = 0;
            Grounded = false;
            State = "Fall";
            return;
        }
        float dt = Time.fixedDeltaTime;
        var v = body.linearVelocity;
        Vector2 foot = body.position + footOffset;
        bool was = Grounded;
        Grounded = v.y > -1f && v.y < .15f && OnFloor(foot);
        if (Grounded)
        {
            grace = .09f;
            if (!was) { land = .20f; Landings++; Burst.Spawn(foot, false); }
        }
        else grace = Mathf.Max(0, grace - dt);
        v.x = Mathf.MoveTowards(v.x, axis * 5.5f, (axis == 0 ? 52 : 42) * dt);
        if (axis != 0) Facing = axis;
        if (buffer > 0 && grace > 0)
        {
            v.y = 9.6f;
            Grounded = false;
            grace = buffer = land = 0;
            Burst.Spawn(foot, false);
        }
        buffer = Mathf.Max(0, buffer - dt);
        body.linearVelocity = v;
        State = !Grounded ? (v.y > .12f ? "Jump" : "Fall") : land > 0 ? "Land" : Mathf.Abs(v.x) > .12f ? "Run" : "Idle";
        land = Mathf.Max(0, land - dt);
    }

    void LateUpdate() { art.Draw(hud.Won ? "Idle" : State, Facing); }
}