using UnityEngine;

[RequireComponent(typeof(BoxCollider2D), typeof(SpriteRenderer))]
public sealed class MeadowTerrain : MonoBehaviour
{
    public bool stone;
    Texture2D texture;
    Sprite sprite;

    void Awake()
    {
        var box = GetComponent<BoxCollider2D>();
        var art = GetComponent<SpriteRenderer>();
        if (!box || !art) { enabled = false; return; }
        const float ppu = 32f;
        int w = Mathf.RoundToInt(box.size.x * ppu), h = Mathf.RoundToInt(box.size.y * ppu);
        texture = new Texture2D(w, h, TextureFormat.RGBA32, false) { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
        var pixels = new Color32[w*h];
        Color32 soil = new Color32(152, 106, 73, 255), dark = new Color32(128, 86, 64, 255);
        Color32 grass = new Color32(95, 168, 74, 255), rim = new Color32(183, 228, 160, 255);
        Color32 rock = new Color32(139, 121, 126, 255), highlight = new Color32(204, 179, 169, 255);
        int radius = stone ? 8 : Mathf.Min(5, h/3);
        for (int y = 0; y < h; y++) for (int x = 0; x < w; x++)
        {
            int dx = Mathf.Max(0, radius - Mathf.Min(x, w-1-x));
            int dy = Mathf.Max(0, radius - Mathf.Min(y, h-1-y));
            int depth = h-1-y;
            if (dx*dx + dy*dy >= radius*radius) continue;
            if (stone)
            {
                bool facet = ((x+2*y)%23 < 2 && y > 5 && y < h-8) || depth < 4;
                pixels[y*w+x] = facet ? highlight : rock;
            }
            else
            {
                int tuft = 8 + Mathf.RoundToInt(2f * Mathf.Sin(x * 0.25f));
                pixels[y*w+x] = depth < 3 ? rim : depth < tuft ? grass : ((x + 7 * (y/4)) % 29 < 4 && y % 11 < 2 ? dark : soil);
            }
        }
        texture.SetPixels32(pixels);
        texture.Apply(false, true);
        sprite = Sprite.Create(texture, new Rect(0, 0, w, h), new Vector2(0.5f,0.5f), ppu, 0, SpriteMeshType.FullRect);
        art.sprite = sprite;
    }

    void OnDestroy() { if (sprite) Destroy(sprite); if (texture) Destroy(texture); }
}
