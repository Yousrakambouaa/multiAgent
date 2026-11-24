using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;

[RequireComponent(typeof(Image))]
public class TrailTile : MonoBehaviour, IPointerDownHandler, IPointerUpHandler, IPointerExitHandler, ICancelHandler
{
    public string style;
    public FoxMotor motor;
    Sprite gemIcon;
    Text label;
    Image bg;
    readonly HashSet<int> fingers = new HashSet<int>();
    float pulse;
    Color fill;
    static Sprite rounded;
    static readonly Color dark = new Color(.074f, .19f, .15f, .93f), orange = new Color(.81f, .32f, .08f, .94f), cream = new Color(1, .96f, .84f);
    bool Moving => style == "left" || style == "right" || style == "jump";
    public string Caption => label ? label.text : "";
    public bool Held => fingers.Count > 0;

    static Sprite Rounded()
    {
        if (rounded) return rounded;
        const int n = 64;
        var tex = new Texture2D(n, n, TextureFormat.RGBA32, false);
        for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)
        {
            var d = new Vector2(Mathf.Max(0, Mathf.Abs(x - 31.5f) - 13), Mathf.Max(0, Mathf.Abs(y - 31.5f) - 13));
            tex.SetPixel(x, y, new Color(1, 1, 1, Mathf.Clamp01(18.5f - d.magnitude)));
        }
        tex.Apply(false, true);
        rounded = Sprite.Create(tex, new Rect(0, 0, n, n), Vector2.one * .5f, 100, 0, SpriteMeshType.FullRect, new Vector4(20, 20, 20, 20));
        return rounded;
    }

    static Sprite FindGem()
    {
        var all = Object.FindObjectsByType<SpriteRenderer>(FindObjectsInactive.Include, FindObjectsSortMode.None);
        foreach (var r in all)
            if (r.sprite && r.gameObject.name.EndsWith("Crystal Sprite")) return r.sprite;
        return null;
    }

    void Awake()
    {
        var rt = (RectTransform)transform;
        bg = GetComponent<Image>();
        Vector2 a = Vector2.one * .5f, p = Vector2.zero, size = new Vector2(490, 150);
        string text = "";
        int font = 68;
        fill = dark;
        Color letters = cream;
        switch (style)
        {
            case "title": a = new Vector2(.5f, 1); p.y = -277; size = new Vector2(940, 285); text = "<size=38>F O R E S T   T R A I L</size>\nFoxy Jump"; font = 124; break;
            case "play": a = new Vector2(.5f, 0); p.y = 265; text = "Play  ›"; fill = orange; break;
            case "left": case "right": a = Vector2.zero; p = new Vector2(style == "left" ? 170 : 420, 170); size = new Vector2(220, 220); text = style == "left" ? "◀" : "▶"; font = 85; fill.a = .78f; break;
            case "jump": a = Vector2.right; p = new Vector2(-190, 190); size = new Vector2(260, 260); text = "↑\n<size=40>JUMP</size>"; font = 82; fill = orange; fill.a = .83f; break;
            case "count": a = Vector2.up; p = new Vector2(180, -91); size = new Vector2(280, 102); text = "0/5"; break;
            case "hint": a = new Vector2(.5f, 1); p.y = -174; size = new Vector2(890, 96); font = 48; break;
            case "win": size = new Vector2(900, 600); text = "<size=38>T R A I L   C O M P L E T E</size>\nYou Win!\n<size=55>Gems  5/5</size>\n "; font = 108; fill = cream; letters = dark; break;
            case "restart": p.y = -196; text = "Restart  ›"; fill = orange; break;
        }
        rt.anchorMin = rt.anchorMax = a;
        rt.pivot = Vector2.one * .5f;
        rt.anchoredPosition = p;
        rt.sizeDelta = size;
        bg.sprite = Rounded(); bg.type = Image.Type.Sliced; bg.pixelsPerUnitMultiplier = .55f;
        bg.color = fill;
        bg.raycastTarget = Moving || style == "play" || style == "restart";
        var child = new GameObject("Lettering", typeof(RectTransform), typeof(Text));
        var r = (RectTransform)child.transform;
        r.SetParent(rt, false);
        r.anchorMin = Vector2.zero; r.anchorMax = Vector2.one;
        r.offsetMin = style == "count" ? new Vector2(66, 0) : Vector2.zero;
        r.offsetMax = style == "win" ? new Vector2(0, 57) : Vector2.zero;
        label = child.GetComponent<Text>();
        label.raycastTarget = false; label.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        label.fontStyle = FontStyle.Bold; label.fontSize = font; label.alignment = TextAnchor.MiddleCenter;
        label.color = letters; label.text = text;
        if (style == "count")
        {
            gemIcon = FindGem();
            var icon = new GameObject("Crystal icon", typeof(RectTransform), typeof(Image)).GetComponent<Image>();
            icon.transform.SetParent(rt, false);
            icon.rectTransform.anchorMin = icon.rectTransform.anchorMax = new Vector2(0, .5f);
            icon.rectTransform.anchoredPosition = new Vector2(56, 0); icon.rectTransform.sizeDelta = new Vector2(72, 72);
            icon.sprite = gemIcon; icon.preserveAspect = true; icon.raycastTarget = false;
            icon.enabled = gemIcon != null;
        }
        var button = GetComponent<Button>();
        if (button)
        {
            button.targetGraphic = bg;
            if (Moving)
            {
                button.transition = Selectable.Transition.None;
                button.navigation = new Navigation { mode = Navigation.Mode.None };
            }
            else if (style == "play" || style == "restart") button.onClick.AddListener(OpenTrail);
        }
    }

    void OpenTrail() { SceneManager.LoadScene("Game"); }
    public void Say(string s)
    {
        label.text = s;
        if (style != "count") return;
        pulse = .17f;
        label.transform.localScale = Vector3.one * 1.19f;
        label.color = s == "5/5" ? new Color(.56f, 1, .87f) : cream;
    }
    void Update()
    {
        if (pulse <= 0) return;
        pulse = Mathf.Max(0, pulse - Time.unscaledDeltaTime);
        label.transform.localScale = Vector3.one * (1 + pulse * 1.12f);
    }
    public void OnPointerDown(PointerEventData e)
    {
        if (!Moving || e.button != PointerEventData.InputButton.Left || !fingers.Add(e.pointerId) || fingers.Count != 1) return;
        motor.Pad(style, true);
        transform.localScale = Vector3.one * .92f;
        bg.color = new Color(fill.r * .73f, fill.g * .73f, fill.b * .73f, fill.a);
    }
    void Release()
    {
        motor.Pad(style, false);
        transform.localScale = Vector3.one;
        bg.color = fill;
    }
    void ReleaseAll() { if (Held) { fingers.Clear(); Release(); } }
    public void OnPointerUp(PointerEventData e) { if (fingers.Remove(e.pointerId) && !Held) Release(); }
    public void OnPointerExit(PointerEventData e) { OnPointerUp(e); }
    public void OnCancel(BaseEventData e) { ReleaseAll(); }
    void OnDisable() { ReleaseAll(); }
}