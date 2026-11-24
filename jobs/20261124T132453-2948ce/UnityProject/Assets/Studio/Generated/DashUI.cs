using UnityEngine;
using UnityEngine.UI;

public sealed class DashUI : MonoBehaviour
{
    public RectTransform safeRoot;
    public RectTransform menu, hud, victory;
    public Button play, restart;
    public Text counter;
    public Image carrotIcon;
    public SpriteRenderer carrotSource;
    public DashTouch left, right, jump;
    Sprite disk, panel;
    Texture2D diskTexture, panelTexture;
    Font font;
    Rect lastSafe;
    int lastW, lastH;
    static readonly Color Cream = new Color32(255,244,194,255);
    static readonly Color Ink = new Color32(47,57,43,255);
    static readonly Color Pine = new Color32(42,83,65,255);
    public string CounterText => counter.text;

    void Awake()
    {
        if (Application.platform == RuntimePlatform.Android) Screen.orientation = ScreenOrientation.LandscapeLeft;
        font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        disk = Shape(64, out diskTexture);
        panel = Shape(16, out panelTexture);
        Stretch(menu); Stretch(hud); Stretch(victory);
        menu.GetComponent<Image>().color = new Color(0.10f,0.22f,0.22f,0.30f);
        victory.GetComponent<Image>().color = new Color(0.08f,0.17f,0.16f,0.63f);
        Overlay(menu, "BUNNY DASH", "MEADOW RUN", "Hop through the meadow and find all five carrots.", play, "Play");
        Overlay(victory, "You Win!", "HARVEST COMPLETE", "All five carrots are tucked away!", restart, "Restart");

        Image chip = Plate(hud, "Harvest badge", new Vector2(0f,1f), new Vector2(145f,-76f), new Vector2(198f,86f), Ink);
        chip.transform.SetAsFirstSibling();
        Place(carrotIcon.rectTransform, new Vector2(0f,1f), new Vector2(91f,-76f), new Vector2(68f,68f));
        carrotIcon.sprite = carrotSource.sprite;
        carrotIcon.preserveAspect = true;
        carrotIcon.raycastTarget = false;
        Place(counter.rectTransform, new Vector2(0f,1f), new Vector2(163f,-76f), new Vector2(120f,80f));
        TextStyle(counter, 48, Cream, FontStyle.Bold);
        Touch(left, new Vector2(0f,0f), new Vector2(126f,116f), 152f, "<", Pine, 74);
        Touch(right, new Vector2(0f,0f), new Vector2(306f,116f), 152f, ">", Pine, 74);
        Touch(jump, new Vector2(1f,0f), new Vector2(-134f,118f), 174f, "JUMP", new Color32(151,66,42,255), 42);
        FitSafeArea();
    }

    internal void Bind(DashGame game)
    {
        play.onClick.AddListener(game.Play);
        restart.onClick.AddListener(game.Restart);
    }

    internal void Show(bool atMenu, bool won, int count, int total)
    {
        counter.text = count + "/" + total;
        menu.gameObject.SetActive(atMenu);
        victory.gameObject.SetActive(won);
        hud.gameObject.SetActive(!atMenu && !won);
    }

    void Overlay(RectTransform root, string title, string kicker, string note, Button button, string verb)
    {
        Vector2 center = new Vector2(0.5f,0.5f);
        Plate(root, "Card shadow", center, new Vector2(0f,-13f), new Vector2(806f,464f), new Color(0.12f,0.25f,0.21f,0.48f)).transform.SetSiblingIndex(0);
        Plate(root, "Cream card", center, Vector2.zero, new Vector2(806f,464f), Cream).transform.SetSiblingIndex(1);
        Label(root, kicker, center, new Vector2(0f,157f), new Vector2(650f,45f), 27, Pine, FontStyle.Bold);
        Label(root, title, center, new Vector2(0f,72f), new Vector2(760f,126f), 100, Ink, FontStyle.Bold);
        Label(root, note, center, new Vector2(0f,-26f), new Vector2(746f,62f), 34, Ink, FontStyle.Normal);
        RectTransform rect = (RectTransform)button.transform;
        Place(rect, center, new Vector2(0f,-135f), new Vector2(342f,108f));
        var face = button.GetComponent<Image>();
        face.sprite = panel;
        face.type = Image.Type.Sliced;
        face.color = Pine;
        button.targetGraphic = face;
        Label(rect, verb, center, Vector2.zero, new Vector2(326f,100f), 50, Color.white, FontStyle.Bold);
        rect.SetAsLastSibling();
    }

    void Touch(DashTouch touch, Vector2 anchor, Vector2 offset, float size, string label, Color tint, int textSize)
    {
        RectTransform r = (RectTransform)touch.transform;
        Place(r, anchor, offset, new Vector2(size,size));
        touch.Style(disk, tint);
        Label(r, label, new Vector2(0.5f,0.5f), Vector2.zero, new Vector2(size,size), textSize, Color.white, FontStyle.Bold);
    }

    void LateUpdate() { FitSafeArea(); }

    void FitSafeArea()
    {
        Rect safe = Screen.safeArea;
        int w = Screen.width, h = Screen.height;
        if (w < 1 || h < 1 || (w == lastW && h == lastH && safe == lastSafe)) return;
        lastSafe = safe; lastW = w; lastH = h;
        if (safe.width < 1f || safe.height < 1f) safe = new Rect(0,0,w,h);
        safeRoot.anchorMin = new Vector2(safe.xMin/w, safe.yMin/h);
        safeRoot.anchorMax = new Vector2(safe.xMax/w, safe.yMax/h);
        safeRoot.offsetMin = safeRoot.offsetMax = Vector2.zero;
    }

    static void Stretch(RectTransform r)
    {
        r.anchorMin = Vector2.zero; r.anchorMax = Vector2.one;
        r.offsetMin = r.offsetMax = Vector2.zero;
    }

    static void Place(RectTransform r, Vector2 anchor, Vector2 at, Vector2 size)
    {
        r.anchorMin = r.anchorMax = anchor;
        r.pivot = new Vector2(0.5f,0.5f);
        r.sizeDelta = size;
        r.anchoredPosition = at;
    }

    Image Plate(RectTransform parent, string title, Vector2 anchor, Vector2 at, Vector2 size, Color color)
    {
        var o = new GameObject(title, typeof(RectTransform), typeof(Image));
        o.transform.SetParent(parent, false);
        Image img = o.GetComponent<Image>();
        img.raycastTarget = false;
        img.sprite = panel; img.type = Image.Type.Sliced; img.color = color;
        Place(img.rectTransform, anchor, at, size);
        return img;
    }

    void Label(RectTransform parent, string line, Vector2 anchor, Vector2 at, Vector2 size, int px, Color tint, FontStyle style)
    {
        var o = new GameObject(line, typeof(RectTransform), typeof(Text));
        o.transform.SetParent(parent, false);
        var text = o.GetComponent<Text>();
        Place(text.rectTransform, anchor, at, size);
        TextStyle(text, px, tint, style);
        text.text = line;
    }

    void TextStyle(Text text, int size, Color tint, FontStyle style)
    {
        text.font = font; text.fontSize = size; text.color = tint; text.fontStyle = style;
        text.alignment = TextAnchor.MiddleCenter; text.raycastTarget = false;
        text.resizeTextForBestFit = true; text.resizeTextMinSize = size/2; text.resizeTextMaxSize = size;
    }

    static Sprite Shape(int radius, out Texture2D tex)
    {
        int size = radius == 64 ? 128 : 36;
        tex = new Texture2D(size,size,TextureFormat.RGBA32,false) { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
        var data = new Color32[size*size];
        for (int y = 0; y < size; y++) for (int x = 0; x < size; x++)
        {
            float dx = Mathf.Max(0f, radius - Mathf.Min(x+0.5f, size-x-0.5f));
            float dy = Mathf.Max(0f, radius - Mathf.Min(y+0.5f, size-y-0.5f));
            float a = Mathf.Clamp01(radius - Mathf.Sqrt(dx*dx + dy*dy));
            data[y*size+x] = new Color32(255,255,255,(byte)(255*a));
        }
        tex.SetPixels32(data); tex.Apply(false, true);
        return Sprite.Create(tex, new Rect(0,0,size,size), new Vector2(0.5f,0.5f), 100f, 0, SpriteMeshType.FullRect, radius == 64 ? Vector4.zero : new Vector4(16,16,16,16));
    }

    void OnDestroy() { if (disk) Destroy(disk); if (panel) Destroy(panel); if (diskTexture) Destroy(diskTexture); if (panelTexture) Destroy(panelTexture); }
}
