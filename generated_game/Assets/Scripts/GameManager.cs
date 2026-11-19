using UnityEngine;
using UnityEngine.SceneManagement;

public class GameManager : MonoBehaviour
{
    public static GameManager instance;
    public int score;
    public int highScore;

    private void Awake()
    {
        if (instance == null)
        {
            instance = this;
            DontDestroyOnLoad(gameObject);
        }
    }

    public void StartGame()
    {
        SceneManager.LoadScene("GameScene");
        score = 0;
    }

    public void GameOver()
    {
        SceneManager.LoadScene("GameOverScene");
    }

    public void RestartGame()
    {
        SceneManager.LoadScene("GameScene");
    }

    public void AddCoin()
    {
        score += 1; // Increase score by 1 for each coin collected
    }
}