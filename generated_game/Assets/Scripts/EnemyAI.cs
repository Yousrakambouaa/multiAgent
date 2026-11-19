using UnityEngine;

public class EnemyAI : MonoBehaviour
{
    public float moveSpeed = 2f;
    private bool movingRight = true;
    public Transform groundCheck;
    public LayerMask groundLayer;

    void Update()
    {
        Move();
    }

    void Move()
    {
        transform.Translate(Vector2.right * moveSpeed * Time.deltaTime);
        RaycastHit2D groundInfo = Physics2D.Raycast(groundCheck.position, Vector2.down, 0.1f, groundLayer);

        if (groundInfo.collider == false)
        {
            Flip();
        }
    }

    void Flip()
    {
        movingRight = !movingRight;
        Vector3 enemyScale = transform.localScale;
        enemyScale.x *= -1;
        transform.localScale = enemyScale;
    }
}