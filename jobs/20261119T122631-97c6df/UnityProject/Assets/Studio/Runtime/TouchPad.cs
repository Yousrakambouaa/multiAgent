using UnityEngine;
using UnityEngine.EventSystems;
namespace Studio.Runtime {
    public class TouchPad : MonoBehaviour, IPointerDownHandler, IPointerUpHandler {
        public static bool Left, Right, Jump;
        public string action;
        public void OnPointerDown(PointerEventData data) { if (action == "left") Left = true; if (action == "right") Right = true; if (action == "jump") Jump = true; }
        public void OnPointerUp(PointerEventData data) { if (action == "left") Left = false; if (action == "right") Right = false; }
        public static void Reset() { Left = Right = Jump = false; }
        void OnDisable() { Reset(); }
    }
}
