using UnityEngine;
using UnityEngine.SceneManagement;

public class LevelSelectionManager : MonoBehaviour
{
    public void SelectLevel(int levelIndex)
    {
        SceneManager.LoadScene("GameScene"); // Add specific logic for level selection
    }

    public void BackToMainMenu()
    {
        SceneManager.LoadScene("MainMenuScene");
    }
}