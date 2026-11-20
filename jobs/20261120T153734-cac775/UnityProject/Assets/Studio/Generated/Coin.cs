using UnityEngine;

public class Coin : MonoBehaviour
{
    [Header("Coin Settings")]
    public CoinCollector collector;
    
    void OnTriggerEnter2D(Collider2D other)
    {
        if (other.CompareTag("Player"))
        {
            CollectCoin();
        }
    }

    public void CollectCoin()
    {
        if (!gameObject.activeSelf) return;
        gameObject.SetActive(false);
        if (collector != null) collector.CollectCoin();
    }
}
