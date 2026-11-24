using UnityEngine;

public sealed class DashGame : MonoBehaviour
{
    public BunnyMotor bunny;
    public DashUI ui;
    public Transform harvest;
    CarrotPickup[] carrots;
    public int Count { get; private set; }
    public int CarrotTotal => carrots.Length;
    public bool Playing { get; private set; }
    public bool Won { get; private set; }

    void Start()
    {
        carrots = harvest.GetComponentsInChildren<CarrotPickup>(true);
        ui.Bind(this);
        ui.Show(true, false, 0, carrots.Length);
    }

    public void Play()
    {
        if (!Playing && !Won) BeginRun();
    }

    public void Restart()
    {
        if (Won) BeginRun();
    }

    void BeginRun()
    {
        Count = 0;
        Won = false;
        foreach (CarrotPickup carrot in carrots) carrot.Restore();
        bunny.BeginRun();
        Playing = true;
        ui.Show(false, false, Count, CarrotTotal);
    }

    internal void EarnCarrot()
    {
        if (!Playing) return;
        Count++;
        if (Count == CarrotTotal)
        {
            Won = true;
            Playing = false;
            bunny.StopRun();
        }
        ui.Show(false, Won, Count, CarrotTotal);
    }
}
