using UnityEngine;
using UnityEngine.UI;

public class CoinCollectorGame : MonoBehaviour
{
    [Header("Game Settings")]
    public int totalCoins = 5;
    public Text scoreText;
    public Text winText;
    
    [Header("UI Elements")]
    public GameObject mobileControls;
    
    private int collectedCoins = 0;
    
    void Start()
    {
        // Hide win text initially
        winText.gameObject.SetActive(false);
        
        // Check if we're on mobile and show/hide controls accordingly
        #if UNITY_ANDROID || UNITY_IOS
        mobileControls.SetActive(true);
        #else
        mobileControls.SetActive(false);
        #endif
    }
    
    public void CoinCollected()
    {
        collectedCoins++;
        scoreText.text = $"Score: {collectedCoins}";
        
        if (collectedCoins >= totalCoins)
        {
            WinGame();
        }
    }
    
    void WinGame()
    {
        winText.gameObject.SetActive(true);
    }
}