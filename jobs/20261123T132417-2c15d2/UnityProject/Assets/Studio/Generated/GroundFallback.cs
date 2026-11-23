using UnityEngine;

[RequireComponent(typeof(SpriteRenderer))]
public class GroundFallback : MonoBehaviour
{
    private SpriteRenderer sr;

    void Awake()
    {
        sr = GetComponent<SpriteRenderer>();
        if (sr.sprite == null)
        {
            sr.sprite = CreateGroundSprite(256, 64);
        }
        sr.color = Color.white;
    }

    private static Sprite CreateGroundSprite(int width, int height)
    {
        Texture2D tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
        tex.filterMode = FilterMode.Point;
        Color green = new Color(0.133f, 0.545f, 0.133f, 1f);
        Color[] pixels = new Color[width * height];
        for (int i = 0; i < pixels.Length; i++) pixels[i] = green;
        tex.SetPixels(pixels);
        tex.Apply();
        return Sprite.Create(tex, new Rect(0, 0, width, height), new Vector2(0.5f, 0.5f), 64f);
    }
}