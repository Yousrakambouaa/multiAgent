using UnityEngine;
using UnityEngine.SceneManagement;

public class MainMenu : MonoBehaviour
{
    public void StartGame()
    {
        SceneManager.LoadScene("GameplayScene");
    }

    public void ShowInstructions()
    {
        // Implementation for showing instructions (could change UI state)
    }

    public void ExitGame()
    {
        Application.Quit();
    }
}