using UnityEngine;
using UnityEngine.UI;

public class CoinCollector : MonoBehaviour
{
    [Header("Game Elements")]
    public Text scoreText;
    public Text winText;
    public int totalCoins = 5;
    public int collectedCoins = 0;
    
    [Header("UI Settings")]
    private CanvasGroup winCanvasGroup;
    
    void Start()
    {
        // Initialize win text to invisible
        winText.gameObject.SetActive(false);
        winCanvasGroup = winText.GetComponent<CanvasGroup>();
        if (winCanvasGroup != null)
        {
            winCanvasGroup.alpha = 0f;
        }
        
        // Initialize score text
        UpdateScoreDisplay();
    }
    
    public void OnCoinCollected()
    {
        collectedCoins++;
        UpdateScoreDisplay();
        
        // Check win condition
        if (collectedCoins >= totalCoins)
        {
            ShowWinMessage();
        }
    }
    
    void UpdateScoreDisplay()
    {
        if (scoreText != null)
        {
            scoreText.text = "Score: " + collectedCoins;
        }
    }
    
    void ShowWinMessage()
    {
        if (winText != null)
        {
            winText.gameObject.SetActive(true);
            if (winCanvasGroup != null)
            {
                winCanvasGroup.alpha = 1f;
            }
        }
    }
}