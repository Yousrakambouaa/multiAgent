using UnityEngine;

public class Coin : MonoBehaviour
{
    [Header("References")]
    private CoinCollector coinCollector;
    
    void Start()
    {
        // Find the coin collector in the scene
        coinCollector = FindObjectOfType<CoinCollector>();
        
        if (coinCollector == null)
        {
            Debug.LogError("CoinCollector not found in scene!");
        }
    }
    
    void OnTriggerEnter2D(Collider2D other)
    {
        // Only collect if the player hits this coin
        if (other.CompareTag("Player"))
        {
            // Notify coin collector
            coinCollector.OnCoinCollected();
            
            // Remove the coin
            Destroy(gameObject);
        }
    }
}