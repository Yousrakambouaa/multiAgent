using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Studio.Runtime;
public class AssemblyTests {
    [Test] public void EveryBuildSceneExistsAndHasNoMissingScripts() {
        Assert.GreaterOrEqual(EditorBuildSettings.scenes.Length,3);
        foreach(var entry in EditorBuildSettings.scenes) {
            Assert.IsTrue(System.IO.File.Exists(entry.path));
            var scene=EditorSceneManager.OpenScene(entry.path,OpenSceneMode.Single);
            var roots=scene.GetRootGameObjects();
            Assert.AreEqual(1,roots.SelectMany(r=>r.GetComponentsInChildren<GameDirector>()).Count());
            foreach(var go in roots.SelectMany(r=>r.GetComponentsInChildren<Transform>()).Select(t=>t.gameObject))
                Assert.AreEqual(0,GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go));
        }
    }
    [Test] public void SpriteImportsAndPrefabsExist() {
        Assert.GreaterOrEqual(AssetDatabase.FindAssets("t:Sprite",new[]{"Assets/Studio/Art"}).Length,4);
        Assert.GreaterOrEqual(AssetDatabase.FindAssets("t:Prefab",new[]{"Assets/Studio/Prefabs"}).Length,2);
        foreach(var guid in AssetDatabase.FindAssets("t:Texture2D",new[]{"Assets/Studio/Art"})) {
            var importer=AssetImporter.GetAtPath(AssetDatabase.GUIDToAssetPath(guid)) as TextureImporter;
            Assert.AreEqual(TextureImporterType.Sprite,importer.textureType); Assert.Greater(importer.spritePixelsPerUnit,0);
        }
    }
}
