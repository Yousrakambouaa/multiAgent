using UnityEngine;
namespace Studio.Runtime {
    public class CameraFollow : MonoBehaviour {
        public Transform target;
        void LateUpdate() {
            if (target == null) return;
            Vector3 p = transform.position;
            p.x = Mathf.Lerp(p.x, Mathf.Max(6, target.position.x + 3), Time.deltaTime * 5);
            transform.position = p;
        }
    }
}
