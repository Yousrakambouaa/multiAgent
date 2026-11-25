using UnityEngine;
using UnityEngine.InputSystem;
namespace Studio.Runtime {
    [RequireComponent(typeof(Rigidbody2D), typeof(BoxCollider2D))]
    public class PlayerMotor : MonoBehaviour {
        public float moveSpeed = 6, jumpSpeed = 11;
        public GameDirector director;
        public Rigidbody2D Body { get; private set; }
        public bool Grounded { get; private set; }
        public bool TestInput;
        public float TestAxis;
        private BoxCollider2D hitbox;
        private bool jumpRequested;
        private Vector3 spawn;
        private float lastHit = -10;
        void Awake() { Body = GetComponent<Rigidbody2D>(); hitbox = GetComponent<BoxCollider2D>(); spawn = transform.position; }
        void Update() {
            var keyboard = Keyboard.current;
            if (!TestInput && ((keyboard != null && (keyboard.spaceKey.wasPressedThisFrame || keyboard.upArrowKey.wasPressedThisFrame)) || TouchPad.Jump)) jumpRequested = true;
            TouchPad.Jump = false;
            if (transform.position.y < -5) Respawn();
        }
        void FixedUpdate() {
            if (Time.timeScale == 0) return;
            Bounds b = hitbox.bounds;
            Grounded = Physics2D.BoxCast(new Vector2(b.center.x, b.min.y + .07f), new Vector2(b.size.x * .8f, .06f), 0, Vector2.down, .18f, 1 << 8).collider != null;
            float axis = TestInput ? TestAxis : (TouchPad.Right ? 1 : 0) - (TouchPad.Left ? 1 : 0);
            var k = Keyboard.current;
            if (!TestInput && k != null) axis += (k.dKey.isPressed || k.rightArrowKey.isPressed ? 1 : 0) - (k.aKey.isPressed || k.leftArrowKey.isPressed ? 1 : 0);
            Body.linearVelocity = new Vector2(Mathf.Clamp(axis, -1, 1) * moveSpeed, Body.linearVelocity.y);
            if (jumpRequested) { TryJump(); jumpRequested = false; }
            if (Mathf.Abs(axis) > .1f && TryGetComponent<SpriteRenderer>(out var sprite)) sprite.flipX = axis < 0;
        }
        public bool TryJump() {
            if (!Grounded) return false;
            Body.linearVelocity = new Vector2(Body.linearVelocity.x, jumpSpeed); Grounded = false; return true;
        }
        public void Respawn() {
            if (Time.time - lastHit < .4f) return;
            lastHit = Time.time; Body.linearVelocity = Vector2.zero; transform.position = spawn;
            if (director != null) director.ShowHint("Try again — follow the trail!");
        }
        void OnDisable() { TouchPad.ResetState(); }
    }
}
