using UnityEngine;
using UnityEngine.UI;

public class CoinCollector : MonoBehaviour
{
    [Header("Game Settings")]
    public int totalCoins = 5;
    public Text scoreText;
    public Text winText;
    
    private int collectedCoins = 0;
    private bool gameWon = false;
    
    void Start()
    {
        // Initially hide win text
        if (winText != null) winText.enabled = false;
        UpdateScore();
    }
    
    public void CollectCoin()
    {
        collectedCoins++;
        UpdateScore();
        
        if (collectedCoins >= totalCoins)
        {
            WinGame();
        }
    }
    
    void UpdateScore()
    {
        if (scoreText != null) scoreText.text = "Score: " + collectedCoins;
    }
    
    void WinGame()
    {
        gameWon = true;
        if (winText != null)
        {
            winText.enabled = true;
            winText.text = "YOU WIN!";
        }
    }
    
    public bool IsGameWon()
    {
        return gameWon;
    }
    
    public int GetCollectedCoins()
    {
        return collectedCoins;
    }
}
