using UnityEngine;
namespace Studio.Runtime {
    public class LevelRules : MonoBehaviour {
        public virtual void Configure(PlayerMotor player) { }
        public virtual int CoinPoints() { return 10; }
    }
}
