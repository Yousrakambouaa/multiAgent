using UnityEngine;

public class TrailHUD : MonoBehaviour
{
    public TrailTile count, hint;
    public GameObject controls, win;
    public int Gems { get; private set; }
    public bool Won { get; private set; }
    float until;

    public void Pick()
    {
        Gems++;
        count.Say(Gems + "/5");
    }
    public void Finish()
    {
        if (Won) return;
        if (Gems == 5)
        {
            Won = true;
            controls.SetActive(false);
            hint.gameObject.SetActive(false);
            win.SetActive(true);
        }
        else
        {
            hint.gameObject.SetActive(true);
            hint.Say("Collect all 5 gems first! (" + Gems + "/5)");
            until = Time.unscaledTime + 2.2f;
        }
    }
    void Update() { if (hint.gameObject.activeSelf && Time.unscaledTime >= until) hint.gameObject.SetActive(false); }
}