using UnityEngine;

public class CollectibleController : MonoBehaviour
{
    public int pointsValue = 1;

    private void OnTriggerEnter2D(Collider2D other)
    {
        if (other.CompareTag("Player"))
        {
            // Assuming there's a GameManager to handle the score
            GameManager.instance.AddCoin();
            Destroy(gameObject);
        }
    }
}