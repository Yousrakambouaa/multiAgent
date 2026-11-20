using System;
using System.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

public class MobileControlsTests
{
    GameObject root;
    Component player;
    Component controls;
    Button left;
    Button right;
    Button jump;
    EventSystem events;

    [SetUp] public void Setup()
    {
        root = new GameObject("MobileControlVerification");
        events = new GameObject("Events", typeof(EventSystem)).GetComponent<EventSystem>();
        events.transform.SetParent(root.transform);
        player = root.AddComponent(Type.GetType("PlayerController, Assembly-CSharp", true));
        controls = root.AddComponent(Type.GetType("MobileControls, Assembly-CSharp", true));
        left = Button("Left"); right = Button("Right"); jump = Button("Jump");
        Set("leftButton", left); Set("rightButton", right); Set("jumpButton", jump); Set("playerController", player);
        controls.GetType().GetMethod("BindControls").Invoke(controls, null);
    }
    Button Button(string name)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image), typeof(Button));
        go.transform.SetParent(root.transform);
        return go.GetComponent<Button>();
    }
    void Set(string name, object value) { controls.GetType().GetField(name).SetValue(controls, value); }
    bool Flag(string name) { return (bool)player.GetType().GetField(name).GetValue(player); }
    void Send(Button button, EventTriggerType type, int pointer = 1)
    {
        var data = new PointerEventData(events) { pointerId = pointer };
        foreach (var entry in button.GetComponent<EventTrigger>().triggers.Where(e => e.eventID == type))
            entry.callback.Invoke(data);
    }
    [TearDown] public void Cleanup() { UnityEngine.Object.DestroyImmediate(root); }
    [Test] public void HoldAndPointerUpReleaseMovement()
    {
        Send(left, EventTriggerType.PointerDown); Assert.IsTrue(Flag("moveLeft"));
        player.SendMessage("Update"); Assert.IsTrue(Flag("moveLeft"), "Keyboard processing must preserve touch input");
        Send(left, EventTriggerType.PointerUp); Assert.IsFalse(Flag("moveLeft"));
    }
    [Test] public void ExitAndCancelReleaseMovement()
    {
        Send(right, EventTriggerType.PointerDown); Send(right, EventTriggerType.PointerExit); Assert.IsFalse(Flag("moveRight"));
        Send(left, EventTriggerType.PointerDown); Send(left, EventTriggerType.Cancel); Assert.IsFalse(Flag("moveLeft"));
    }
    [Test] public void MultipleTouchesDoNotReleaseUntilLastPointer()
    {
        Send(left, EventTriggerType.PointerDown, 1); Send(left, EventTriggerType.PointerDown, 2);
        Send(left, EventTriggerType.PointerUp, 1); Assert.IsTrue(Flag("moveLeft"));
        Send(left, EventTriggerType.PointerUp, 2); Assert.IsFalse(Flag("moveLeft"));
    }
    [Test] public void JumpIsQueuedUntilPhysicsConsumesIt()
    {
        Send(jump, EventTriggerType.PointerDown); Assert.IsTrue(Flag("jump"));
        player.SendMessage("Update"); Assert.IsTrue(Flag("jump"));
    }
    [Test] public void DisableReleasesAndRebindDoesNotDuplicateCallbacks()
    {
        Send(left, EventTriggerType.PointerDown);
        ((MonoBehaviour)controls).enabled = false; Assert.IsFalse(Flag("moveLeft"));
        controls.GetType().GetMethod("BindControls").Invoke(controls, null);
        Assert.AreEqual(4,left.GetComponent<EventTrigger>().triggers.Count);
    }
}
