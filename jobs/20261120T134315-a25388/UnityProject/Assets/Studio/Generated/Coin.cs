using UnityEngine;

public class Coin : MonoBehaviour
{
    private CoinCollector collector;
    
    void Start()
    {
        collector = FindFirstObjectByType<CoinCollector>();
    }
    
    void OnTriggerEnter2D(Collider2D other)
    {
        if (other prefab != null && other prefab CompareTag("Player"))
        {
            if (collector != null) collector.CollectCoin();
            Destroyibur prefab);
        }
    }
}