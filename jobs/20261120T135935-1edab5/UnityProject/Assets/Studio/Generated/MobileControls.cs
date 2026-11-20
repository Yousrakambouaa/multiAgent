using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.EventSystems;
using UnityEngine.UI;

public class MobileControls : MonoBehaviour
{
    public Button leftButton;
    public Button rightButton;
    public Button jumpButton;
    public PlayerController playerController;
    readonly List<(EventTrigger trigger, EventTrigger.Entry entry)> bindings = new();
    readonly HashSet<int> leftPointers = new();
    readonly HashSet<int> rightPointers = new();

    void Start()
    {
        BindControls();
        bool mobile = Application.isMobilePlatform;
        foreach (var button in new[] { leftButton, rightButton, jumpButton })
            if (button != null) button.gameObject.SetActive(mobile);
    }

    public void BindControls()
    {
        UnbindControls();
        BindMovement(leftButton, leftPointers, true);
        BindMovement(rightButton, rightPointers, false);
        Add(jumpButton, EventTriggerType.PointerDown, data =>
        {
            if (jumpButton.IsInteractable() && playerController != null) playerController.RequestJump();
        });
    }

    void BindMovement(Button button, HashSet<int> pointers, bool left)
    {
        Add(button, EventTriggerType.PointerDown, data =>
        {
            if (!button.IsInteractable() || data is not PointerEventData pointer) return;
            pointers.Add(pointer.pointerId);
            Apply(left, pointers.Count > 0);
        });
        UnityAction<BaseEventData> release = data =>
        {
            if (data is PointerEventData pointer) pointers.Remove(pointer.pointerId);
            else pointers.Clear();
            Apply(left, pointers.Count > 0);
        };
        Add(button, EventTriggerType.PointerUp, release);
        Add(button, EventTriggerType.PointerExit, release);
        Add(button, EventTriggerType.Cancel, release);
    }

    void Apply(bool left, bool pressed)
    {
        if (playerController == null) return;
        if (left) playerController.SetMobileLeft(pressed);
        else playerController.SetMobileRight(pressed);
    }

    void Add(Button button, EventTriggerType type, UnityAction<BaseEventData> callback)
    {
        if (button == null) return;
        var trigger = button.GetComponent<EventTrigger>() ?? button.gameObject.AddComponent<EventTrigger>();
        trigger.triggers ??= new List<EventTrigger.Entry>();
        var entry = new EventTrigger.Entry { eventID = type };
        entry.callback.AddListener(callback);
        trigger.triggers.Add(entry);
        bindings.Add((trigger, entry));
    }

    void Release()
    {
        leftPointers.Clear(); rightPointers.Clear();
        Apply(true, false); Apply(false, false);
    }
    void UnbindControls()
    {
        Release();
        foreach (var binding in bindings)
            if (binding.trigger != null) binding.trigger.triggers.Remove(binding.entry);
        bindings.Clear();
    }
    void OnDisable() { Release(); }
    void OnApplicationFocus(bool focused) { if (!focused) Release(); }
    void OnDestroy() { UnbindControls(); }
}
