using UnityEngine;
namespace Studio.Runtime {
    public class LevelExit : MonoBehaviour {
        public GameDirector director;
        private bool done;
        void OnTriggerEnter2D(Collider2D other) {
            if (done || other.GetComponent<PlayerMotor>() == null) return;
            done = true;
            if (director == null) Debug.LogError("Exit has no GameDirector reference");
            else director.CompleteLevel();
        }
    }
}
