using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

[RequireComponent(typeof(Image))]
public sealed class DashTouch : MonoBehaviour, IPointerDownHandler, IPointerUpHandler, IPointerExitHandler, ICancelHandler
{
    int? owner;
    bool press;
    Image face;
    Color normal, down;
    public bool Held => owner.HasValue;
    public float Diameter => ((RectTransform)transform).rect.width;

    internal void Style(Sprite disk, Color tint)
    {
        face = GetComponent<Image>();
        face.sprite = disk;
        face.raycastTarget = true;
        normal = tint;
        down = Color.Lerp(tint, Color.black, 0.23f);
        face.color = normal;
    }

    internal bool TakePress() { bool p = press; press = false; return p; }

    public void OnPointerDown(PointerEventData e)
    {
        if (e.button != PointerEventData.InputButton.Left || Held || !isActiveAndEnabled) return;
        owner = e.pointerId;
        press = true;
        if (face) face.color = down;
    }

    public void OnPointerUp(PointerEventData e) { if (owner == e.pointerId) Release(); }
    public void OnPointerExit(PointerEventData e) { if (owner == e.pointerId) Cancel(); }
    public void OnCancel(BaseEventData e) { Cancel(); }
    void OnDisable() { Cancel(); }
    void OnApplicationFocus(bool focused) { if (!focused) Cancel(); }
    void Cancel() { press = false; Release(); }
    void Release() { owner = null; if (face) face.color = normal; }
}
