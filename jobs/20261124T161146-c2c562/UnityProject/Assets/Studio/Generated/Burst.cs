using UnityEngine;

public class Burst : MonoBehaviour
{
    static Sprite dot;
    Vector3 drift;
    float age, size, lifetime;
    Color tint;
    SpriteRenderer ink;

    public static Sprite Glint
    {
        get
        {
            if (!dot)
            {
                var tex = new Texture2D(16, 16, TextureFormat.RGBA32, false);
                for (int y = 0; y < 16; y++) for (int x = 0; x < 16; x++)
                    tex.SetPixel(x, y, new Color(1, 1, 1, Mathf.Clamp01(8 - Mathf.Abs(x - 7.5f) - Mathf.Abs(y - 7.5f))));
                tex.Apply(false, true);
                dot = Sprite.Create(tex, new Rect(0, 0, 16, 16), Vector2.one * .5f, 16);
            }
            return dot;
        }
    }

    public static void Spawn(Vector3 p, bool gem)
    {
        for (int i = 0; i < (gem ? 10 : 6); i++)
        {
            var b = new GameObject(gem ? "Crystal sparkle" : "Trail dust").AddComponent<Burst>();
            b.transform.position = p;
            b.ink = b.gameObject.AddComponent<SpriteRenderer>();
            b.ink.sprite = Glint;
            b.ink.sortingOrder = 12;
            b.tint = gem ? (i % 2 == 0 ? Color.white : Color.cyan) : new Color(1, .83f, .55f, .68f);
            b.ink.color = b.tint;
            float a = i * 2.39996f;
            b.drift = new Vector3(Mathf.Cos(a) * (gem ? 2.1f : 1.65f), (gem ? 1.3f : .7f) + Mathf.Sin(a) * (gem ? 1.9f : .5f));
            b.size = gem ? .19f : .13f;
            b.lifetime = gem ? .44f : .31f;
            b.transform.localScale = Vector3.one * b.size;
        }
    }

    void Update()
    {
        float dt = Time.deltaTime;
        age += dt;
        transform.position += drift * dt;
        drift.y -= 3.7f * dt;
        float a = Mathf.Clamp01(1 - age / lifetime);
        transform.localScale = Vector3.one * size * (.45f + .55f * a);
        ink.color = new Color(tint.r, tint.g, tint.b, tint.a * a);
        if (age >= lifetime) Destroy(gameObject);
    }
}