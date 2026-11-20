using UnityEngine;

public class Coin : MonoBehaviour
{
    [Header("Coin Settings")]
    public int points = 1;
    
    private void OnTriggerEnter2D(Collider2D other)
    {
        if (other.CompareTag("Player"))
        {
            // Notify the game controller that a coin was collected
            GameObject gameController = GameObject.FindGameObjectWithTag("GameController");
            if (gameController != null)
            {
                CoinCollectorGame gameManager = gameController.GetComponent<CoinCollectorGame>();
                if (gameManager != null)
                {
                    gameManager.CoinCollected();
                }
            }
            
            // Destroy this coin
            Destroy(gameObject);
        }
    }
}