using UnityEngine;
using UnityEngine.InputSystem;

[RequireComponent(typeof(Rigidbody2D))]
[RequireComponent(typeof(BoxCollider2D))]
[RequireComponent(typeof(SpriteRenderer))]
public class PlayerSquareController : MonoBehaviour
{
    public float moveSpeed = 6f;
    public float jumpForce = 9f;
    public Transform groundCheck;
    public float groundCheckRadius = 0.25f;
    public LayerMask groundLayer = ~0;

    private Rigidbody2D rb;
    private BoxCollider2D box;
    private SpriteRenderer sr;
    private bool grounded;
    private float moveInput;
    private bool jumpQueued;

    public bool IsGrounded => grounded;
    public float MoveInput => moveInput;

    void Awake()
    {
        rb = GetComponent<Rigidbody2D>();
        box = GetComponent<BoxCollider2D>();
        sr = GetComponent<SpriteRenderer>();
        if (rb == null || box == null || sr == null)
        {
            Debug.LogError("PlayerSquareController requires Rigidbody2D, BoxCollider2D and SpriteRenderer.");
            enabled = false;
            return;
        }

        if (sr.sprite == null)
        {
            sr.sprite = CreateSquareSprite(64, new Color(1f, 0.843f, 0f, 1f));
        }
        sr.color = Color.white;
    }

    private static Sprite CreateSquareSprite(int size, Color color)
    {
        Texture2D tex = new Texture2D(size, size, TextureFormat.RGBA32, false);
        tex.filterMode = FilterMode.Point;
        Color[] pixels = new Color[size * size];
        for (int i = 0; i < pixels.Length; i++) pixels[i] = color;
        tex.SetPixels(pixels);
        tex.Apply();
        return Sprite.Create(tex, new Rect(0, 0, size, size), new Vector2(0.5f, 0.5f), size);
    }

    void Update()
    {
        moveInput = 0f;
        var kb = Keyboard.current;
        if (kb != null)
        {
            if (kb.aKey.isPressed || kb.leftArrowKey.isPressed) moveInput -= 1f;
            if (kb.dKey.isPressed || kb.rightArrowKey.isPressed) moveInput += 1f;
            if (kb.spaceKey.wasPressedThisFrame || kb.wKey.wasPressedThisFrame) jumpQueued = true;
        }
    }

    void FixedUpdate()
    {
        if (rb == null) return;

        grounded = CheckGrounded();

        Vector2 v = rb.linearVelocity;
        v.x = moveInput * moveSpeed;
        rb.linearVelocity = v;

        if (jumpQueued && grounded)
        {
            v = rb.linearVelocity;
            v.y = jumpForce;
            rb.linearVelocity = v;
            grounded = false;
        }
        jumpQueued = false;
    }

    private bool CheckGrounded()
    {
        if (rb != null && rb.linearVelocity.y > 0.05f) return false;

        if (box != null && box.IsTouchingLayers(groundLayer)) return true; // Studio QA: confirm real ground contact after the upward-motion guard.

        if (box == null) return false;

        Vector2 origin;
        if (groundCheck != null)
        {
            origin = groundCheck.position;
        }
        else
        {
            origin = new Vector2(box.bounds.center.x, box.bounds.min.y);
        }

        Collider2D[] hits = Physics2D.OverlapCircleAll(origin, groundCheckRadius, groundLayer);
        for (int i = 0; i < hits.Length; i++)
        {
            Collider2D hit = hits[i];
            if (hit == null) continue;
            if (hit == box) continue;
            if (hit.attachedRigidbody == rb) continue;
            return true;
        }

        return false;
    }

    public void Jump()
    {
        jumpQueued = true;
    }
}