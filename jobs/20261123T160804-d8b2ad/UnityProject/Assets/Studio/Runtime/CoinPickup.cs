using UnityEngine;
namespace Studio.Runtime {
    public class CoinPickup : MonoBehaviour {
        public GameDirector director;
        public bool Collected { get; private set; }
        private Vector3 start;
        void Start() { start = transform.position; }
        void Update() { if (!Collected) transform.position = start + Vector3.up * Mathf.Sin(Time.time * 2.5f + start.x) * .08f; }
        void OnTriggerEnter2D(Collider2D other) {
            if (Collected || other.GetComponent<PlayerMotor>() == null) return;
            Collected = true;
            if (director == null) { Debug.LogError("Coin has no GameDirector reference"); return; }
            director.AddCoin(); gameObject.SetActive(false); Destroy(gameObject);
        }
    }
}
