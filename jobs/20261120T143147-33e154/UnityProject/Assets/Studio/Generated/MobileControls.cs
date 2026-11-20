using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.Events;
using UnityEngine.InputSystem;
using UnityEngine.UI;

public class MobileControls : MonoBehaviour
{
    [Header("UI References")]
    public Button leftButton;
    public Button rightButton;
    public Button jumpButton;

    [Header("Player Reference")]
    private PlayerController playerController;

    void Start()
    {
        playerController = FindObjectOfType<PlayerController>();
        if (playerController == null)
        {
            Debug.LogError("PlayerController not found in scene!");
            return;
        }

        SetupMobileControls();
        if (Keyboard.current != null)
            gameObject.SetActive(false);
    }

    void SetupMobileControls()
    {
        AddHeldButton(leftButton, MoveLeft, ReleaseLeft);
        AddHeldButton(rightButton, MoveRight, ReleaseRight);
        AddHeldButton(jumpButton, Jump, ReleaseJump);
    }

    static void AddHeldButton(Button button, Action pressed, Action released)
    {
        if (button == null) return;

        var trigger = button.GetComponent<EventTrigger>();
        if (trigger == null) trigger = button.gameObject.AddComponent<EventTrigger>();
        if (trigger.triggers == null) trigger.triggers = new List<EventTrigger.Entry>();

        AddEvent(trigger, EventTriggerType.PointerDown, _ => pressed());
        AddEvent(trigger, EventTriggerType.PointerUp, _ => released());
        AddEvent(trigger, EventTriggerType.PointerExit, _ => released());
        AddEvent(trigger, EventTriggerType.Cancel, _ => released());
    }

    static void AddEvent(EventTrigger trigger, EventTriggerType eventType, UnityAction<BaseEventData> callback)
    {
        var entry = new EventTrigger.Entry { eventID = eventType };
        entry.callback.AddListener(callback);
        trigger.triggers.Add(entry);
    }

    void MoveLeft()
    {
        if (playerController != null) playerController.SetMoveInput(new Vector2(-1f, 0f));
    }

    void MoveRight()
    {
        if (playerController != null) playerController.SetMoveInput(new Vector2(1f, 0f));
    }

    void Jump()
    {
        if (playerController != null) playerController.SetJumpPressed(true);
    }

    void ReleaseLeft()
    {
        if (playerController != null && playerController.GetMoveInput().x < 0f)
            playerController.SetMoveInput(Vector2.zero);
    }

    void ReleaseRight()
    {
        if (playerController != null && playerController.GetMoveInput().x > 0f)
            playerController.SetMoveInput(Vector2.zero);
    }

    void ReleaseJump()
    {
        if (playerController != null) playerController.SetJumpPressed(false);
    }

    void OnDisable()
    {
        if (playerController == null) return;
        playerController.SetMoveInput(Vector2.zero);
        playerController.SetJumpPressed(false);
    }
}
