using UnityEngine;

public class CoinCollectorGame : MonoBehaviour
{
    [Header("Game Settings")]
    public bool isMobile = false;
    
    void Start()
    {
        // Initialize game
        if (isMobile)
        {
            // Mobile setup
        }
        else
        {
            // PC setup
        }
    }
    
    void Update()
    {
        // Game logic
    }
}