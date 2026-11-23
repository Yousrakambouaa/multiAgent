using UnityEngine;

[RequireComponent(typeof(SpriteRenderer))]
public class SkyBackgroundFitter : MonoBehaviour
{
    public Camera targetCamera;

    private SpriteRenderer sr;

    void Awake()
    {
        sr = GetComponent<SpriteRenderer>();
        if (targetCamera == null) targetCamera = Camera.main;
        if (sr.sprite == null)
        {
            sr.sprite = CreateSkySprite(512, 288);
        }
    }

    private static Sprite CreateSkySprite(int width, int height)
    {
        Texture2D tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
        tex.filterMode = FilterMode.Bilinear;
        Color sky = new Color(0.529f, 0.808f, 0.922f, 1f);
        Color[] pixels = new Color[width * height];
        for (int y = 0; y < height; y++)
        {
            for (int x = 0; x < width; x++)
            {
                pixels[y * width + x] = sky;
            }
        }
        DrawCloud(pixels, width, height, 110, 190, 58);
        DrawCloud(pixels, width, height, 320, 225, 42);
        DrawCloud(pixels, width, height, 430, 145, 32);
        tex.SetPixels(pixels);
        tex.Apply();
        return Sprite.Create(tex, new Rect(0, 0, width, height), new Vector2(0.5f, 0.5f), 100f);
    }

    private static void DrawCloud(Color[] pixels, int width, int height, int cx, int cy, int radius)
    {
        Color cloud = new Color(1f, 1f, 1f, 1f);
        for (int y = -radius; y <= radius; y++)
        {
            for (int x = -radius; x <= radius; x++)
            {
                if (x * x + y * y <= radius * radius)
                {
                    int px = cx + x;
                    int py = cy + y;
                    if (px >= 0 && px < width && py >= 0 && py < height)
                    {
                        pixels[py * width + px] = cloud;
                    }
                }
            }
        }
    }

    void Start()
    {
        Fit();
    }

    void LateUpdate()
    {
        Fit();
    }

    private void Fit()
    {
        if (sr == null || sr.sprite == null || targetCamera == null) return;

        float worldHeight = targetCamera.orthographicSize * 2f;
        float worldWidth = worldHeight * targetCamera.aspect;

        Vector2 spriteSize = sr.sprite.bounds.size;
        if (spriteSize.x <= 0f || spriteSize.y <= 0f) return;

        float scaleX = worldWidth / spriteSize.x;
        float scaleY = worldHeight / spriteSize.y;
        float scale = Mathf.Max(scaleX, scaleY);

        transform.localScale = new Vector3(scale, scale, 1f);
        Vector3 camPos = targetCamera.transform.position;
        transform.position = new Vector3(camPos.x, camPos.y, transform.position.z);
    }
}