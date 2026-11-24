using UnityEngine;

[RequireComponent(typeof(Camera))]
public sealed class MeadowCamera : MonoBehaviour
{
    public Transform target;
    public SpriteRenderer background;
    public Texture2D meadow;
    Camera view;
    Sprite image;
    float velocity;
    public float CenterX => transform.position.x;
    public float CenterY => transform.position.y;

    void Awake()
    {
        view = GetComponent<Camera>();
        if (!view) { enabled = false; return; }
        if (meadow)
        {
            image = Sprite.Create(meadow, new Rect(0, 0, meadow.width, meadow.height), new Vector2(0.5f, 0.5f), 128f, 0, SpriteMeshType.FullRect);
            background.sprite = image;
        }
    }

    void LateUpdate()
    {
        float half = view.orthographicSize * view.aspect;
        float center = half < 16f ? Mathf.Clamp(target.position.x + 0.65f, -1f + half, 31f - half) : 15f;
        Vector3 p = transform.position;
        p.x = Mathf.SmoothDamp(p.x, center, ref velocity, 0.16f);
        transform.position = p;
        if (!image) return;
        float width = half * 2f;
        float scale = Mathf.Max(width * 1.13f / image.bounds.size.x, view.orthographicSize * 2.04f / image.bounds.size.y);
        background.transform.localScale = new Vector3(scale, scale, 1f);
        float slack = (image.bounds.size.x * scale - width) * 0.5f;
        float shift = Mathf.Lerp(slack * 0.85f, -slack * 0.85f, Mathf.InverseLerp(2f, 28f, target.position.x));
        background.transform.position = new Vector3(p.x + shift, p.y, 4f);
    }

    void OnDestroy() { if (image) Destroy(image); }
}
