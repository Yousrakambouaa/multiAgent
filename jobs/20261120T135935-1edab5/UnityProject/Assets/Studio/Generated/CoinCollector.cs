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
        // Initially hide win text
        winText.enabled = false;
    }
    
    public void CollectCoin()
    {
        collectedCoins++;
        
        // Update score display
        scoreText.text = $"Score: {collectedCoins}";
        
        // Check win condition
        if (collectedCoins >= totalCoins)
        {
            WinGame();
        }
    }
    
    void WinGame()
    {
        winText.enabled = true;
        // Optionally disable player input here
    }
}