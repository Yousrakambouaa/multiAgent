using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;

public class GameController : MonoBehaviour
{
    public Text scoreText;
    public GameObject gameOverPanel;
    public int lives = 3;
    private int score;

    void Start()
    {
        score = 0;
        UpdateScore();
        gameOverPanel.SetActive(false);
    }

    public void CollectCoin()
    {
        score += 10;
        UpdateScore();
    }

    void UpdateScore()
    {
        scoreText.text = "Score: " + score;
    }

    public void PlayerDied()
    {
        lives--;
        if (lives <= 0)
        {
            GameOver();
        }
    }

    void GameOver()
    {
        gameOverPanel.SetActive(true);
        Time.timeScale = 0; // Pause the game
    }

    public void RestartGame()
    {
        Time.timeScale = 1; // Unpause the game
        SceneManager.LoadScene(SceneManager.GetActiveScene().buildIndex);
    }
}