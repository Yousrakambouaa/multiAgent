using UnityEngine;

public class Collectible : MonoBehaviour
{
    public enum CollectibleType { Coin, PowerUp }
    public CollectibleType type;

    private void OnTriggerEnter2D(Collider2D collision)
    {
        if (collision.CompareTag("Player"))
        {
            if (type == CollectibleType.Coin)
            {
                FindObjectOfType<GameController>().CollectCoin();
            }
            // Handle power-ups here
            Destroy(gameObject);
        }
    }
}