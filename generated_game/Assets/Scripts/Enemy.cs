using UnityEngine;

public class Enemy : MonoBehaviour
{
    public float moveSpeed = 2f;
    public float moveRange = 5f;
    private Vector2 startPos;
    private bool movingRight = true;

    void Start() {
        startPos = transform.position;
    }

    void Update() {
        Move();
    }

    void Move() {
        transform.Translate(Vector2.right * moveSpeed * Time.deltaTime * (movingRight ? 1 : -1));
        if (Vector2.Distance(startPos, transform.position) >= moveRange) {
            movingRight = !movingRight;
        }
    }
}