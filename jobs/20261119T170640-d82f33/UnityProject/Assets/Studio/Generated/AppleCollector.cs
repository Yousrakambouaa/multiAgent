using UnityEngine;

public class AppleCollector : MonoBehaviour
{
    public int applesCollected = 0;
    public int totalApples = 0;
    public UnityEngine.UI.Text appleCounterText;
    public UnityEngine.UI.Text winMessageText;

    void Start()
    {
        totalApples = FindObjectsOfType<Apple>().Length;
        UpdateAppleCounter();
        winMessageText.gameObject.SetActive(false);
    }

    public void CollectApple()
    {
        applesCollected++;
        UpdateAppleCounter();
        CheckWinCondition();
    }

    void UpdateAppleCounter()
    {
        if (appleCounterText != null)
        {
            appleCounterText.text = $"Apples: {applesCollected}/{totalApples}";
        }
    }

    void CheckWinCondition()
    {
        if (applesCollected >= totalApples)
        {
            winMessageText.gameObject.SetActive(true);
        }
    }
}