using Studio.Runtime;

public class GeneratedRules : LevelRules
{
    public override void Configure(PlayerMotor player)
    {
        player.moveSpeed = 6.0f;
        player.jumpSpeed = 11.0f;
    }

    public override int CoinPoints()
    {
        return 100;
    }
}