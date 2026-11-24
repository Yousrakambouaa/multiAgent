using UnityEngine;
using UnityEngine.InputSystem;

[RequireComponent(typeof(Rigidbody2D), typeof(BoxCollider2D))]
public sealed class BunnyMotor : MonoBehaviour
{
    public Transform groundCheck;
    public SpriteRenderer art;
    public DashTouch left;
    public DashTouch right;
    public DashTouch jump;
    Rigidbody2D body;
    BoxCollider2D box;
    PhysicsMaterial2D slick;
    ContactFilter2D groundFilter;
    readonly RaycastHit2D[] hits = new RaycastHit2D[8];
    Vector2 spawn;
    Vector3 artOrigin, artScale;
    bool running;
    float axis, buffer;
    public bool Grounded { get; private set; }
    public string Motion { get; private set; } = "Idle";
    public float VisualLift => art.transform.localPosition.y - artOrigin.y;
    public float X => body.position.x;
    public float Y => body.position.y;
    public float HorizontalSpeed => Mathf.Abs(body.linearVelocity.x);

    void Awake()
    {
        body = GetComponent<Rigidbody2D>();
        box = GetComponent<BoxCollider2D>();
        if (!body || !box) { enabled = false; return; }
        slick = new PhysicsMaterial2D("Smooth feet") { friction = 0f, bounciness = 0f };
        box.sharedMaterial = slick;
        groundFilter = new ContactFilter2D { useTriggers = false };
        groundFilter.SetLayerMask(1 << 8);
        spawn = body.position;
        artOrigin = art.transform.localPosition;
        artScale = art.transform.localScale;
        body.simulated = false;
    }

    void OnDestroy() { if (slick) Destroy(slick); }

    internal void BeginRun()
    {
        body.position = spawn;
        transform.position = new Vector3(spawn.x, spawn.y, transform.position.z);
        body.linearVelocity = Vector2.zero;
        body.simulated = true;
        body.WakeUp();
        art.flipX = false;
        Grounded = false;
        axis = buffer = 0f;
        running = true;
    }

    internal void StopRun()
    {
        running = false;
        axis = buffer = 0f;
        body.linearVelocity = Vector2.zero;
        body.simulated = false;
    }

    void Update()
    {
        if (!running) return;
        var kb = Keyboard.current;
        bool l = left.Held || (kb != null && (kb.aKey.isPressed || kb.leftArrowKey.isPressed));
        bool r = right.Held || (kb != null && (kb.dKey.isPressed || kb.rightArrowKey.isPressed));
        axis = (r ? 1f : 0f) - (l ? 1f : 0f);
        bool jumpPressed = jump.TakePress();
        if (jumpPressed || (kb != null && kb.spaceKey.wasPressedThisFrame)) buffer = 0.12f;
    }

    bool FindFloor()
    {
        Bounds b = box.bounds;
        float y = b.min.y + 0.12f;
        for (int i = -1; i <= 1; i++)
        {
            Vector2 origin = new Vector2(b.center.x + i * b.extents.x * 0.85f, y);
            int n = Physics2D.Raycast(origin, Vector2.down, groundFilter, hits, 0.3f);
            for (int j = 0; j < n; j++)
            {
                var hit = hits[j];
                if (hit.collider != box && hit.rigidbody != body && hit.normal.y > 0.6f) return true;
            }
        }
        return false;
    }

    void FixedUpdate()
    {
        if (!running) return;
        Vector2 v = body.linearVelocity;
        Grounded = v.y <= 0.06f && FindFloor();
        if (buffer > 0f && Grounded)
        {
            v.y = 9.4f;
            Grounded = false;
            buffer = 0f;
        }
        buffer = Mathf.Max(0f, buffer - Time.fixedDeltaTime);
        v.x = Mathf.MoveTowards(v.x, axis * 5.4f, (axis == 0f ? 55f : 40f) * Time.fixedDeltaTime);
        Vector2 p = body.position;
        float bounded = Mathf.Clamp(p.x, 0.45f, 29.6f);
        if (bounded != p.x) { body.position = new Vector2(bounded, p.y); v.x = 0f; }
        if ((bounded <= 0.45f && v.x < 0f) || (bounded >= 29.6f && v.x > 0f)) v.x = 0f;
        body.linearVelocity = v;
    }

    void LateUpdate()
    {
        Vector2 v = body.linearVelocity;
        if (running && Mathf.Abs(axis) > 0.1f) art.flipX = axis < 0f;
        Motion = running && !Grounded ? "Jump" : running && Mathf.Abs(v.x) > 0.28f ? "Walk" : "Idle";
        float t = Time.time;
        float lift, stretch, tilt;
        if (Motion == "Jump")
        {
            lift = 0.03f; stretch = Mathf.Lerp(0.98f, 1.12f, Mathf.InverseLerp(-9f, 9f, v.y));
            tilt = Mathf.Clamp(v.y * 0.9f, -8f, 8f) * (art.flipX ? -1f : 1f);
        }
        else if (Motion == "Walk")
        {
            lift = 0.025f + 0.10f * Mathf.Abs(Mathf.Sin(t * 15f));
            stretch = 1f + 0.075f * Mathf.Sin(t * 30f);
            tilt = 5f * Mathf.Sin(t * 15f);
        }
        else
        {
            lift = 0.035f + 0.023f * Mathf.Sin(t * 5f);
            stretch = 1f + 0.028f * Mathf.Sin(t * 5f);
            tilt = 0f;
        }
        art.transform.localPosition = artOrigin + Vector3.up * lift;
        art.transform.localScale = new Vector3(artScale.x / stretch, artScale.y * stretch, artScale.z);
        art.transform.localRotation = Quaternion.Euler(0f, 0f, tilt);
    }
}
