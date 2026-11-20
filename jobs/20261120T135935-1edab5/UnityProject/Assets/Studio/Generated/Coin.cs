using UnityEngine;

public class Coin : MonoBehaviour
{
    [Header("Coin Settings")]
    public CoinCollector collector;
    
    void OnTriggerEnter2D(Collider2D other)
    {
        if (other.CompareTag("Player"))
        {
            // Destroy this coin
            Destroy(gameObject);
            
            // Notify collector
            collector.CollectCoin();
        }
    }
}