
using System;
using System.IO;
using UnityEngine;
using UnityEditor;
using UnityEditor.SceneManagement;

public static class GameSceneBuilder
{
    [Serializable]
    public class SceneConfig
    {
        public SceneObject[] objects;
    }

    [Serializable]
    public class SceneObject
    {
        public string name;
        public string type;
        public float[] position;
        public float[] scale;
    }

    public static void Generate()
    {
        string path = "Assets/Resources/game_scene.json";

        if (!File.Exists(path))
        {
            Debug.LogError("Scene JSON not found!");
            return;
        }

        string json = File.ReadAllText(path);
        SceneConfig config = JsonUtility.FromJson<SceneConfig>(json);

        if (config == null || config.objects == null)
        {
            Debug.LogError("Invalid scene configuration!");
            return;
        }

        var scene = EditorSceneManager.NewScene(
            NewSceneSetup.EmptyScene,
            NewSceneMode.Single
        );

        GameObject cameraObject = new GameObject("Main Camera");
        cameraObject.tag = "MainCamera";

        Camera camera = cameraObject.AddComponent<Camera>();
        camera.orthographic = true;
        camera.orthographicSize = 5;
        cameraObject.transform.position = new Vector3(0, 0, -10);

        foreach (SceneObject obj in config.objects)
        {
            if (obj.position == null || obj.position.Length != 3 ||
                obj.scale == null || obj.scale.Length != 3)
                continue;

            GameObject gameObject = new GameObject(obj.name);

            gameObject.transform.position = new Vector3(
                obj.position[0], obj.position[1], obj.position[2]
            );

            gameObject.transform.localScale = new Vector3(
                obj.scale[0], obj.scale[1], obj.scale[2]
            );

            SpriteRenderer renderer =
                gameObject.AddComponent<SpriteRenderer>();

            renderer.sprite =
                AssetDatabase.GetBuiltinExtraResource<Sprite>(
                    "UI/Skin/UISprite.psd"
                );

        if (obj.type == "player")
        {
            gameObject.tag = "Player";
            gameObject.AddComponent<Rigidbody2D>();
            gameObject.AddComponent<BoxCollider2D>();
            AttachScript(gameObject, "PlayerMovement");
        }
        else if (obj.type == "coin")
        {
            CircleCollider2D collider =
                gameObject.AddComponent<CircleCollider2D>();

            collider.isTrigger = true;
            AttachScript(gameObject, "CoinController");
        }
        else if (obj.type == "obstacle")
        {
            gameObject.AddComponent<BoxCollider2D>();
            AttachScript(gameObject, "ObstacleController");
        }
        else if (obj.type == "ground" || obj.type == "platform")
        {
            gameObject.AddComponent<BoxCollider2D>();
        }

            
        }

        EditorSceneManager.SaveScene(
            scene, "Assets/GameScene.unity"
        );

        Debug.Log("AI-generated scene created!");
    }


    private static void AttachScript(GameObject obj, string className)
    {
        var script = AssetDatabase.LoadAssetAtPath<MonoScript>(
            $"Assets/Scripts/{className}.cs"
        );

        if (script == null)
        {
            Debug.LogError($"Missing script: {className}");
            return;
        }

        var scriptType = script.GetClass();

        if (scriptType == null || !typeof(MonoBehaviour).IsAssignableFrom(scriptType))
        {
            Debug.LogError($"Invalid MonoBehaviour: {className}");
            return;
        }

        obj.AddComponent(scriptType);
    }


    [MenuItem("AI Tools/Generate Game Scene")]
    public static void GenerateFromMenu()
    {
        Generate();
    }

    public static void GenerateBatch()
    {
        Generate();
    }
}
