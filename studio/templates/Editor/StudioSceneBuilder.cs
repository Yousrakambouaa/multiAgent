using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;
using UnityEditor;
using UnityEditor.SceneManagement;
using Studio.Runtime;
using UnityEditor.Build.Reporting;

public static class StudioSceneBuilder {
    [Serializable] public class Bundle { public int version; public string title, orientation; public SceneData[] scenes; public Collision[] collision_rules; }
    [Serializable] public class Collision { public int layer_a, layer_b; public bool ignore; }
    [Serializable] public class SceneData { public string name, kind, background_color; public float camera_size; public ObjectData[] objects; }
    [Serializable] public class ObjectData {
        public string id, name, role, parent, tag, sprite, color; public int layer, sorting_order;
        public bool prefab; public float[] position, scale; public ComponentData[] components;
    }
    [Serializable] public class ComponentData { public string type; public PropertyData[] properties; }
    [Serializable] public class PropertyData { public string name, kind, value; }
    [Serializable] public class Evidence { public bool success; public string stage, error; public int scenes, objects, references, prefabs; public string[] scene_paths; }
    [Serializable] public class ArtInfo { public ArtEntry[] assets; }
    [Serializable] public class ArtEntry { public string path; public int pixels_per_unit; }

    static string Output(string name) { Directory.CreateDirectory("Artifacts"); return Path.GetFullPath("Artifacts/" + name); }
    static Type Resolve(string name) {
        var type = AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType(name, false)).FirstOrDefault(t => t != null);
        if (type == null || !typeof(Component).IsAssignableFrom(type)) throw new Exception("Unknown component: " + name);
        return type;
    }
    static void SetProperty(Component target, PropertyData data, Dictionary<string,GameObject> map, Evidence evidence) {
        var serialized = new SerializedObject(target);
        var prop = serialized.FindProperty(data.name);
        if (prop == null) {
            var property=target.GetType().GetProperty(data.name,System.Reflection.BindingFlags.Public|System.Reflection.BindingFlags.Instance);
            if(property==null || !property.CanWrite || data.kind=="reference") throw new Exception($"Missing field/property {target.GetType().Name}.{data.name}");
            object converted=Convert.ChangeType(data.value,property.PropertyType,CultureInfo.InvariantCulture);
            property.SetValue(target,converted); EditorUtility.SetDirty(target); return;
        }
        switch(data.kind) {
            case "float": prop.floatValue = float.Parse(data.value, CultureInfo.InvariantCulture); break;
            case "int": prop.intValue = int.Parse(data.value); break;
            case "bool": prop.boolValue = bool.Parse(data.value); break;
            case "string": prop.stringValue = data.value; break;
            case "color": if(!ColorUtility.TryParseHtmlString(data.value, out var color)) throw new Exception("Invalid color"); prop.colorValue = color; break;
            case "reference":
                var parts = data.value.Split(':');
                if (!map.ContainsKey(parts[0])) throw new Exception("Missing reference: " + data.value);
                UnityEngine.Object value = parts.Length == 1 ? map[parts[0]] : map[parts[0]].GetComponent(Resolve(parts[1]));
                if (value == null) throw new Exception("Missing component reference " + data.value);
                prop.objectReferenceValue = value; evidence.references++; break;
            default: throw new Exception("Unknown property kind");
        }
        serialized.ApplyModifiedPropertiesWithoutUndo();
    }
    static void ConfigureTags(Bundle bundle) {
        var manager = new SerializedObject(AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset")[0]);
        var tags = manager.FindProperty("tags");
        foreach (var tag in bundle.scenes.SelectMany(s => s.objects).Select(o => o.tag).Distinct()) {
            if (tag == "Untagged" || tag == "Player" || tag == "MainCamera") continue;
            bool found = false; for (int i=0;i<tags.arraySize;i++) if(tags.GetArrayElementAtIndex(i).stringValue==tag) found=true;
            if(!found) { tags.InsertArrayElementAtIndex(tags.arraySize); tags.GetArrayElementAtIndex(tags.arraySize-1).stringValue=tag; }
        }
        var layers = manager.FindProperty("layers"); layers.GetArrayElementAtIndex(8).stringValue="Ground"; layers.GetArrayElementAtIndex(9).stringValue="Player";
        manager.ApplyModifiedPropertiesWithoutUndo();
    }
    static void ImportSprites() {
        var art = JsonUtility.FromJson<ArtInfo>(File.ReadAllText("Assets/Resources/studio_art.json"));
        foreach(var item in art.assets) {
            var importer = AssetImporter.GetAtPath(item.path) as TextureImporter;
            if (importer == null) throw new Exception("Missing texture importer: "+item.path);
            importer.textureType = TextureImporterType.Sprite; importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = item.pixels_per_unit; importer.alphaIsTransparency = true;
            importer.textureCompression = TextureImporterCompression.Uncompressed; importer.mipmapEnabled=false;
            importer.filterMode = FilterMode.Bilinear; importer.SaveAndReimport();
        }
    }
    [MenuItem("Studio/Assemble validated scenes")]
    public static void Assemble() {
        var evidence = new Evidence { stage="assembly" };
        try {
            var bundle = JsonUtility.FromJson<Bundle>(File.ReadAllText("Assets/Resources/studio_scenes.json"));
            if(bundle == null || bundle.scenes == null || bundle.scenes.Length < 1) throw new Exception("No valid scenes");
            ConfigureTags(bundle); ImportSprites();
            Directory.CreateDirectory("Assets/Studio/Scenes"); Directory.CreateDirectory("Assets/Studio/Prefabs");
            var paths = new List<string>();
            foreach (var scene in bundle.scenes) {
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var map = new Dictionary<string,GameObject>();
                foreach(var obj in scene.objects) {
                    if(map.ContainsKey(obj.id)) throw new Exception("Duplicate object ID");
                    var go = new GameObject(obj.name); map.Add(obj.id,go); go.layer=obj.layer; go.tag=obj.tag;
                    go.transform.position=new Vector3(obj.position[0],obj.position[1],obj.position[2]);
                    go.transform.localScale=new Vector3(obj.scale[0],obj.scale[1],obj.scale[2]);
                    if(!string.IsNullOrEmpty(obj.sprite)) {
                        var sprite=AssetDatabase.LoadAssetAtPath<Sprite>(obj.sprite);
                        if(sprite==null) throw new Exception("Missing sprite "+obj.sprite);
                        var sr=go.AddComponent<SpriteRenderer>(); sr.sprite=sprite; sr.sortingOrder=obj.sorting_order;
                        var shader=Shader.Find("Universal Render Pipeline/2D/Sprite-Unlit-Default");
                        if(shader==null) shader=Shader.Find("Sprites/Default");
                        if(shader==null) throw new Exception("No supported sprite shader");
                        // Persist per-texture materials. Manual render targets must not
                        // reuse a stale SpriteRenderer texture binding from another sprite.
                        string materialPath="Assets/Studio/Art/"+Path.GetFileNameWithoutExtension(obj.sprite)+".mat";
                        var material=AssetDatabase.LoadAssetAtPath<Material>(materialPath);
                        if(material==null) { material=new Material(shader); AssetDatabase.CreateAsset(material,materialPath); }
                        material.mainTexture=sprite.texture; sr.sharedMaterial=material;
                        if(ColorUtility.TryParseHtmlString(obj.color,out var c)) sr.color=c;
                        go.transform.localScale=new Vector3(obj.scale[0]/sprite.bounds.size.x,obj.scale[1]/sprite.bounds.size.y,obj.scale[2]);
                    }
                    foreach(var comp in obj.components) {
                        var type=Resolve(comp.type);
                        if(go.GetComponent(type)==null) go.AddComponent(type);
                    }
                    var renderer=go.GetComponent<SpriteRenderer>();
                    if(renderer!=null) {
                        if(go.TryGetComponent<BoxCollider2D>(out var box)) box.size=renderer.sprite.bounds.size;
                        if(go.TryGetComponent<CircleCollider2D>(out var circle)) circle.radius=renderer.sprite.bounds.size.x/2;
                    }
                    evidence.objects++;
                }
                foreach(var obj in scene.objects) {
                    var go=map[obj.id];
                    if(!string.IsNullOrEmpty(obj.parent)) go.transform.SetParent(map[obj.parent].transform,true);
                    foreach(var comp in obj.components) foreach(var prop in comp.properties) SetProperty(go.GetComponent(Resolve(comp.type)),prop,map,evidence);
                }
                var cameraGO=new GameObject("Scene Camera"); cameraGO.tag="MainCamera";
                var camera=cameraGO.AddComponent<Camera>(); camera.orthographic=true; camera.orthographicSize=scene.camera_size;
                cameraGO.transform.position=new Vector3(6,2,-10); camera.clearFlags=CameraClearFlags.SolidColor;
                if(ColorUtility.TryParseHtmlString(scene.background_color,out var bg)) camera.backgroundColor=bg;
                if(scene.kind=="level") cameraGO.AddComponent<CameraFollow>().target=map[scene.objects.First(o=>o.role=="player").id].transform;
                if(bundle.collision_rules!=null) foreach(var rule in bundle.collision_rules) Physics2D.IgnoreLayerCollision(rule.layer_a,rule.layer_b,rule.ignore);
                foreach(var obj in scene.objects.Where(o=>o.prefab)) {
                    PrefabUtility.SaveAsPrefabAsset(map[obj.id],$"Assets/Studio/Prefabs/{scene.name}_{obj.id}.prefab"); evidence.prefabs++;
                }
                foreach(var go in map.Values) {
                    if(GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go)>0) throw new Exception("Missing script on "+go.name);
                    foreach(var behaviour in go.GetComponents<MonoBehaviour>()) {
                        var so=new SerializedObject(behaviour); var it=so.GetIterator();
                        while(it.NextVisible(true)) if(it.propertyType==SerializedPropertyType.ObjectReference && it.objectReferenceValue==null && it.objectReferenceInstanceIDValue!=0) throw new Exception("Broken reference on "+go.name);
                    }
                }
                string path=$"Assets/Studio/Scenes/{scene.name}.unity";
                if(!EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene(),path)) throw new Exception("Scene save failed");
                paths.Add(path); evidence.scenes++;
            }
            EditorBuildSettings.scenes=paths.Select(p=>new EditorBuildSettingsScene(p,true)).ToArray();
            PlayerSettings.productName=bundle.title; PlayerSettings.defaultInterfaceOrientation=bundle.orientation=="portrait" ? UIOrientation.Portrait : UIOrientation.LandscapeLeft;
            AssetDatabase.SaveAssets(); AssetDatabase.Refresh(); evidence.scene_paths=paths.ToArray(); evidence.success=true;
        } catch(Exception error) { evidence.error=error.ToString(); Debug.LogError(error); }
        File.WriteAllText(Output("assembly.json"),JsonUtility.ToJson(evidence,true));
        if(!evidence.success && Application.isBatchMode) EditorApplication.Exit(1);
    }
    public static void BuildAndroid() {
        var evidence=new Evidence { stage="android_build" };
        try {
            if(!BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Android,BuildTarget.Android)) throw new Exception("Android module not installed");
            if(EditorUserBuildSettings.activeBuildTarget!=BuildTarget.Android)
                throw new Exception("Select Android before building. Live builds use Studio/Build Android APK; batch builds require -buildTarget Android.");
            PlayerSettings.SetScriptingBackend(UnityEditor.Build.NamedBuildTarget.Android,ScriptingImplementation.IL2CPP);
            PlayerSettings.Android.targetArchitectures=AndroidArchitecture.ARM64;
            PlayerSettings.SetApplicationIdentifier(UnityEditor.Build.NamedBuildTarget.Android,"com.unitystudio.prototype");
            EditorUserBuildSettings.buildAppBundle=false; EditorUserBuildSettings.development=true;
            var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions {
                scenes=EditorBuildSettings.scenes.Where(s=>s.enabled).Select(s=>s.path).ToArray(),
                locationPathName=Output("prototype.apk"), target=BuildTarget.Android, options=BuildOptions.Development });
            evidence.success=report.summary.result==BuildResult.Succeeded && File.Exists(Output("prototype.apk"));
            if(!evidence.success) evidence.error=report.summary.result.ToString();
        } catch(Exception error) { evidence.error=error.ToString(); Debug.LogError(error); }
        File.WriteAllText(Output("android.json"),JsonUtility.ToJson(evidence,true));
        if(!evidence.success && Application.isBatchMode) EditorApplication.Exit(1);
    }
}
