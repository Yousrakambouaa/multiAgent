using UnityEngine;
using UnityEngine.UI;

public class CoinCollector : MonoBehaviour
{
    [Header("Game Settings")]
    public int totalCoins = 5;
    public Text scoreText;
    public Text winText;
    
    private int collectedCoins = 0;
    
    void Start()
    {
        if (winText != null) winText.enabled = false;
    }
    
    public void CollectCoin()
    {
        collectedCoins++;
        if (scoreText != null) scoreText.text = "Score: " + collectedCoins;
        
        if (collectedCoins >= totalCoins)
        {
            if (winText != null) winText.enabled = true;
        }
    }
}