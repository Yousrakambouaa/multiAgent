using UnityEngine;
namespace Studio.Runtime {
    public class ParallaxBackdrop : MonoBehaviour {
        public float factor = .12f;
        private float initialX;
        void Start() { initialX = transform.position.x; }
        void LateUpdate() { if (Camera.main != null) { var p = transform.position; p.x = initialX + Camera.main.transform.position.x * factor; transform.position = p; } }
    }
}
