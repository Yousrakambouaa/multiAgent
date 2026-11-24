using System;
using System.IO;
using System.Linq;
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Studio.Runtime;
using UnityEngine.EventSystems;
using UnityEngine.UI;

public class PlatformerTests {
    [Serializable] class Bundle { public SceneInfo[] scenes; }
    [Serializable] class SceneInfo { public string name, kind; }
    private PlayerMotor player;
    private GameDirector director;
    [UnitySetUp] public IEnumerator LoadGeneratedLevel() {
        Time.timeScale=1;
        var bundle=JsonUtility.FromJson<Bundle>(Resources.Load<TextAsset>("studio_scenes").text);
        var level=bundle.scenes.First(s=>s.kind=="level").name;
        yield return SceneManager.LoadSceneAsync(level);
        player=UnityEngine.Object.FindFirstObjectByType<PlayerMotor>();
        director=UnityEngine.Object.FindFirstObjectByType<GameDirector>();
        Assert.NotNull(player); Assert.NotNull(director);
        player.TestInput=true; player.TestAxis=0;
        for(int i=0;i<45;i++) yield return new WaitForFixedUpdate();
    }
    [UnityTest] public IEnumerator PlayerSpawnsAndFindsGround() {
        Assert.Greater(player.transform.position.y,-1); Assert.IsTrue(player.Grounded); Assert.NotNull(player.Body);
        yield return null;
    }
    [UnityTest] public IEnumerator HorizontalInputMovesPlayer() {
        float start=player.transform.position.x; player.TestAxis=1;
        for(int i=0;i<12;i++) yield return new WaitForFixedUpdate();
        Assert.Greater(player.transform.position.x,start+.5f);
    }
    [UnityTest] public IEnumerator JumpGainsHeightAndPreventsDoubleJump() {
        float start=player.transform.position.y; Assert.IsTrue(player.TryJump()); Assert.IsFalse(player.TryJump());
        for(int i=0;i<8;i++) yield return new WaitForFixedUpdate();
        Assert.Greater(player.transform.position.y,start+.4f);
    }
    [UnityTest] public IEnumerator LandsBackOnSolidGround() {
        Assert.IsTrue(player.TryJump());
        for(int i=0;i<100;i++) yield return new WaitForFixedUpdate();
        Assert.IsTrue(player.Grounded); Assert.Less(Mathf.Abs(player.Body.linearVelocity.y),.5f);
    }
    [UnityTest] public IEnumerator CoinContactCollectsAndIncreasesScore() {
        var coin=UnityEngine.Object.FindObjectsByType<CoinPickup>(FindObjectsSortMode.None).OrderBy(c=>c.transform.position.x).First();
        Assert.AreEqual(0,director.Coins); var before=director.Score;
        player.transform.position=coin.transform.position; player.Body.linearVelocity=Vector2.zero; Physics2D.SyncTransforms();
        for(int i=0;i<5;i++) yield return new WaitForFixedUpdate();
        Assert.AreEqual(1,director.Coins); Assert.Greater(director.Score,before); Assert.IsTrue(coin==null || coin.Collected);
    }
    [UnityTest] public IEnumerator ReferencesSpritesAndTouchControlsArePresent() {
        Assert.AreSame(director,player.director);
        foreach(var coin in UnityEngine.Object.FindObjectsByType<CoinPickup>(FindObjectsSortMode.None)) Assert.AreSame(director,coin.director);
        foreach(var sr in UnityEngine.Object.FindObjectsByType<SpriteRenderer>(FindObjectsSortMode.None)) Assert.NotNull(sr.sprite);
        Assert.AreEqual(3,UnityEngine.Object.FindObjectsByType<TouchPad>(FindObjectsSortMode.None).Length);
        Assert.NotNull(UnityEngine.Object.FindFirstObjectByType<UnityEngine.Canvas>()); yield return null;
    }
    [UnityTest] public IEnumerator TouchButtonDrivesActualPlayerMovement() {
        player.TestInput=false; float start=player.transform.position.x;
        var right=UnityEngine.Object.FindObjectsByType<TouchPad>(FindObjectsSortMode.None).Single(t=>t.action=="right");
        right.OnPointerDown(new PointerEventData(EventSystem.current));
        for(int i=0;i<12;i++) yield return new WaitForFixedUpdate();
        right.OnPointerUp(new PointerEventData(EventSystem.current));
        Assert.Greater(player.transform.position.x,start+.5f);
        Assert.IsTrue(Physics2D.GetIgnoreLayerCollision(9,9));
    }
    [UnityTest] public IEnumerator FinishBeaconLoadsResultsScene() {
        var bundle=JsonUtility.FromJson<Bundle>(Resources.Load<TextAsset>("studio_scenes").text);
        string result=bundle.scenes.First(s=>s.kind=="result").name;
        var exit=UnityEngine.Object.FindFirstObjectByType<LevelExit>(); Assert.NotNull(exit);
        player.transform.position=exit.transform.position; player.Body.linearVelocity=Vector2.zero; Physics2D.SyncTransforms();
        for(int i=0;i<30;i++) yield return null;
        Assert.AreEqual(result,SceneManager.GetActiveScene().name);
    }
    [UnityTest] public IEnumerator MainMenuPlayButtonLoadsGeneratedLevel() {
        var bundle=JsonUtility.FromJson<Bundle>(Resources.Load<TextAsset>("studio_scenes").text);
        yield return SceneManager.LoadSceneAsync(bundle.scenes.First(s=>s.kind=="menu").name);
        yield return null;
        var play=UnityEngine.Object.FindObjectsByType<Button>(FindObjectsSortMode.None).Single(b=>b.name=="Play");
        play.onClick.Invoke();
        for(int i=0;i<30;i++) yield return null;
        Assert.AreEqual(bundle.scenes.First(s=>s.kind=="level").name,SceneManager.GetActiveScene().name);
        Assert.NotNull(UnityEngine.Object.FindFirstObjectByType<PlayerMotor>());
    }
    [UnityTest] public IEnumerator CaptureActualGamePreview() {
        yield return null;
        var camera=Camera.main; Assert.NotNull(camera);
        var canvas=UnityEngine.Object.FindFirstObjectByType<Canvas>();
        if(canvas!=null) { canvas.renderMode=RenderMode.ScreenSpaceCamera; canvas.worldCamera=camera; canvas.planeDistance=1; Canvas.ForceUpdateCanvases(); }
        var target=new RenderTexture(1280,720,24); camera.targetTexture=target; camera.Render();
        RenderTexture.active=target; var texture=new Texture2D(1280,720,TextureFormat.RGB24,false);
        texture.ReadPixels(new Rect(0,0,1280,720),0,0); texture.Apply();
        string folder=Path.GetFullPath("Artifacts"); Directory.CreateDirectory(folder);
        File.WriteAllBytes(Path.Combine(folder,"preview.png"),texture.EncodeToPNG());
        camera.targetTexture=null; RenderTexture.active=null; UnityEngine.Object.Destroy(target); UnityEngine.Object.Destroy(texture);
        if(canvas!=null) canvas.renderMode=RenderMode.ScreenSpaceOverlay;
        Assert.IsTrue(File.Exists(Path.Combine(folder,"preview.png")));
    }
    [UnityTearDown] public IEnumerator NoUnexpectedRuntimeErrors() {
        Time.timeScale=1; TouchPad.ResetState(); LogAssert.NoUnexpectedReceived(); yield return null;
    }
}
