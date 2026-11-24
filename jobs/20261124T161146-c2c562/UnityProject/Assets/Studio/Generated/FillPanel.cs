using UnityEngine;

[RequireComponent(typeof(RectTransform))]
public class FillPanel : MonoBehaviour
{
    public bool safe;
    RectTransform rt;
    Rect last;
    int width, height;

    void Awake() { rt = (RectTransform)transform; Layout(); }
    void Update() { if (safe && (last != Screen.safeArea || width != Screen.width || height != Screen.height)) Layout(); }
    void Layout()
    {
        width = Mathf.Max(1, Screen.width); height = Mathf.Max(1, Screen.height);
        last = Screen.safeArea;
        if (safe && last.width > 0 && last.height > 0)
        {
            rt.anchorMin = new Vector2(last.xMin / width, last.yMin / height);
            rt.anchorMax = new Vector2(last.xMax / width, last.yMax / height);
        }
        else { rt.anchorMin = Vector2.zero; rt.anchorMax = Vector2.one; }
        rt.offsetMin = rt.offsetMax = Vector2.zero;
    }
}