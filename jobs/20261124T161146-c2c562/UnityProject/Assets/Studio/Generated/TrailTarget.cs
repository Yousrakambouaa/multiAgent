using UnityEngine;

public class TrailTarget : MonoBehaviour
{
    public bool finish;
    public TrailHUD hud;
    public Transform crystal;
    bool taken;
    Material ink;

    void Start()
    {
        if (!finish) return;
        ink = new Material(Shader.Find("Sprites/Default"));
        Stroke("Cedar flagstaff", new Vector2(0, -1), new Vector2(0, 1.16f), .08f, .08f, new Color(.36f, .19f, .10f), 6);
        Stroke("Saffron pennant", new Vector2(.04f, .94f), new Vector2(.91f, .83f), .40f, .015f, new Color(1, .50f, .14f), 7);
        Stroke("Pennant highlight", new Vector2(.055f, 1.085f), new Vector2(.79f, .85f), .04f, .011f, new Color(1, .84f, .47f), 8);
    }

    void Stroke(string title, Vector2 a, Vector2 b, float w, float tip, Color color, int order)
    {
        var line = new GameObject(title).AddComponent<LineRenderer>();
        line.transform.SetParent(transform, false);
        line.useWorldSpace = false; line.sharedMaterial = ink;
        line.startColor = line.endColor = color;
        line.startWidth = w; line.endWidth = tip;
        line.positionCount = 2; line.SetPosition(0, a); line.SetPosition(1, b);
        line.sortingOrder = order;
    }

    void Update()
    {
        if (!finish)
        {
            float wave = Mathf.Sin(Time.time * 3.3f + transform.position.x);
            crystal.localPosition = Vector3.up * (.072f * wave);
            crystal.localScale = Vector3.one * (.86f + .055f * wave);
        }
    }

    void OnTriggerEnter2D(Collider2D other)
    {
        if (taken || hud.Won || !other.GetComponent<FoxMotor>()) return;
        if (finish) { hud.Finish(); return; }
        taken = true;
        Burst.Spawn(crystal.position, true);
        hud.Pick();
        gameObject.SetActive(false);
    }

    void OnDestroy() { if (ink) Destroy(ink); }
}