using UnityEngine;

public class ObstacleController : MonoBehaviour
{
    private void OnTriggerEnter2D(Collider2D other)
    {
        if (other.CompareTag("Player"))
        {
            Debug.Log("Game Over Triggered by Obstacle Controller.");
        }
    }
}