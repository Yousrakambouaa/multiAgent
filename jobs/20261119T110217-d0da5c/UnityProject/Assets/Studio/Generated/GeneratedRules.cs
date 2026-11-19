using Studio.Runtime;

public class GeneratedRules : LevelRules
{
    public override void Configure(PlayerMotor player)
    {
        player.moveSpeed = GameSpec.Instance.moveSpeed;
        player.jumpSpeed = GameSpec.Instance.jumpSpeed;
    }

    public override int CoinPoints()
    {
        return 100;
    }
}