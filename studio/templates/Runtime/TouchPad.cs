using UnityEngine;
using UnityEngine.EventSystems;
namespace Studio.Runtime {
    public class TouchPad : MonoBehaviour, IPointerDownHandler, IPointerUpHandler, IPointerExitHandler, ICancelHandler {
        public static bool Left, Right, Jump;
        public string action;
        public void OnPointerDown(PointerEventData data) { if (action == "left") Left = true; if (action == "right") Right = true; if (action == "jump") Jump = true; }
        public void OnPointerUp(PointerEventData data) { Release(); }
        public void OnPointerExit(PointerEventData data) { Release(); }
        public void OnCancel(BaseEventData data) { Release(); }
        void Release() { if (action == "left") Left = false; if (action == "right") Right = false; if (action == "jump") Jump = false; }
        public static void Reset() { Left = Right = Jump = false; }
        void OnDisable() { Release(); }
    }
}
