using UnityEngine;

public class GeneratedRules : Studio.Runtime.LevelRules
{
    public override void Configure(Studio.Runtime.PlayerMotor player)
    {
        player.moveSpeed = 6.0f;
        player.jumpSpeed = 11.0f;
    }
    
    public override int CoinPoints()
    {
        return 100;
    }
    
    public override string NextLevel()
    {
        return "Results";
    }
}