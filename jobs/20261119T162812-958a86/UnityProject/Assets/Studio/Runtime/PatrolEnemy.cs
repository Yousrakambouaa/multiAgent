using UnityEngine;
namespace Studio.Runtime {
    public class PatrolEnemy : MonoBehaviour {
        public float distance = 2, speed = 1.2f;
        private Vector3 start;
        void Start() { start = transform.position; }
        void Update() { transform.position = start + Vector3.right * Mathf.Sin(Time.time * speed) * distance; }
        void OnTriggerEnter2D(Collider2D other) {
            var player = other.GetComponent<PlayerMotor>();
            if (player == null) return;
            if (player.Body.linearVelocity.y < -1 && player.transform.position.y > transform.position.y + .3f) {
                player.Body.linearVelocity = new Vector2(player.Body.linearVelocity.x, 7); Destroy(gameObject);
            } else player.Respawn();
        }
    }
}
