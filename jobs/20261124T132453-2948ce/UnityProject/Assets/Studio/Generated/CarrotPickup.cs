using UnityEngine;

[RequireComponent(typeof(Rigidbody2D), typeof(CircleCollider2D), typeof(SpriteRenderer))]
public sealed class CarrotPickup : MonoBehaviour
{
    public DashGame game;
    Rigidbody2D body;
    CircleCollider2D trigger;
    SpriteRenderer art;
    Vector3 rest, baseScale;
    float elapsed;
    const float PopDuration = 0.62f;
    public bool Collected { get; private set; }
    public bool PopVisible => Collected && art.enabled;

    void Awake()
    {
        body = GetComponent<Rigidbody2D>();
        trigger = GetComponent<CircleCollider2D>();
        art = GetComponent<SpriteRenderer>();
        if (!body || !trigger || !art) { enabled = false; return; }
        rest = transform.position;
        baseScale = transform.localScale;
    }

    internal void Restore()
    {
        Collected = false;
        elapsed = 0f;
        body.position = (Vector2)rest;
        transform.position = rest;
        transform.localScale = baseScale;
        art.color = Color.white;
        art.enabled = true;
        trigger.enabled = true;
    }

    void OnTriggerEnter2D(Collider2D other)
    {
        if (!enabled || Collected || !game.Playing || other.GetComponentInParent<BunnyMotor>() != game.bunny) return;
        Collected = true;
        elapsed = 0f;
        trigger.enabled = false;
        game.EarnCarrot();
    }

    void Update()
    {
        if (!PopVisible) return;
        elapsed += Time.deltaTime;
        float t = Mathf.Clamp01(elapsed / PopDuration);
        transform.position = rest + Vector3.up * (0.65f * (1f - (1f-t)*(1f-t)));
        transform.localScale = baseScale * (1f + 0.5f * Mathf.Sin(Mathf.PI * t) + 0.25f * t);
        art.color = new Color(1f, 1f, 1f, 1f - t * t);
        if (t >= 1f) art.enabled = false;
    }
}
