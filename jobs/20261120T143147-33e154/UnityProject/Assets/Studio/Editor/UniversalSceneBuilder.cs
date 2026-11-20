using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEditor;
using UnityEditor.SceneManagement;
using Studio.Runtime;

// Explicit scene instructions; nothing assumes a player, ground, camera or genre.
public static class UniversalSceneBuilder {
    [Serializable] public class Bundle {
        public int version; public string title, dimension, orientation;
        public SceneData[] scenes; public MaterialData[] materials;
    }
    [Serializable] public class SceneData { public string name, kind, background_color; public ObjectData[] objects; }
    [Serializable] public class ObjectData {
        public string id, name, role, parent, tag, sprite, color, primitive, model, material;
        public int layer, sorting_order; public bool prefab, active = true;
        public float[] position, scale, rotation; public MeshData mesh; public ComponentData[] components;
    }
    [Serializable] public class MeshData { public Vector3[] vertices; public int[] triangles; }
    [Serializable] public class ComponentData { public string type; public PropertyData[] properties; }
    [Serializable] public class PropertyData { public string name, kind, value; }
    [Serializable] public class MaterialData { public string id, color, texture; public float metallic, smoothness; public bool unlit; }
    [Serializable] public class ArtInfo { public ArtEntry[] assets; }
    [Serializable] public class ArtEntry { public string path, kind, status; public int pixels_per_unit; }
    [Serializable] public class Evidence {
        public bool success; public string stage = "assembly", error;
        public int scenes, objects, references, prefabs; public string[] scene_paths;
    }

    public static Type Resolve(string name) {
        var names = new[] { name, "UnityEngine." + name, "UnityEngine.UI." + name,
            "UnityEngine.EventSystems." + name, "Studio.Runtime." + name };
        var type = names.SelectMany(n => AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType(n, false)))
            .FirstOrDefault(t => t != null);
        if (type == null || !typeof(Component).IsAssignableFrom(type) || type.Namespace?.StartsWith("UnityEditor") == true)
            throw new Exception("Unknown runtime component: " + name);
        return type;
    }
    static Vector3 Vec(float[] v, Vector3 fallback) { return v != null && v.Length == 3 ? new Vector3(v[0],v[1],v[2]) : fallback; }
    public static Color ColorValue(string value) {
        if (!ColorUtility.TryParseHtmlString(value, out var c)) throw new Exception("Invalid color " + value);
        return c;
    }
    static float[] Numbers(string value) { return value.Trim('[',']').Split(',').Select(v=>float.Parse(v,CultureInfo.InvariantCulture)).ToArray(); }
    static object ConvertValue(string value, Type type) {
        if (type.IsEnum) return Enum.Parse(type,value);
        if (type == typeof(Color)) return ColorValue(value);
        if (type == typeof(Vector3)) { var v=Numbers(value); return new Vector3(v[0],v[1],v[2]); }
        if (type == typeof(Vector2)) { var v=Numbers(value); return new Vector2(v[0],v[1]); }
        return Convert.ChangeType(value,type,CultureInfo.InvariantCulture);
    }
    static UnityEngine.Object Reference(PropertyData data, Dictionary<string,GameObject> map) {
        if (data.kind == "asset") {
            if (!data.value.StartsWith("Assets/") || data.value.Contains("..")) throw new Exception("Unsafe asset path");
            var asset = AssetDatabase.LoadMainAssetAtPath(data.value);
            if (asset == null) throw new Exception("Missing asset " + data.value);
            return asset;
        }
        var parts = data.value.Split(':');
        if (!map.TryGetValue(parts[0],out var go)) throw new Exception("Missing reference " + data.value);
        if (parts.Length == 1) return go;
        var component=go.GetComponent(Resolve(parts[1]));
        if(component==null) throw new Exception("Missing reference component " + data.value);
        return component;
    }
    static void SetProperty(Component target, PropertyData data, Dictionary<string,GameObject> map, Evidence evidence) {
        bool reference = data.kind == "reference" || data.kind == "asset";
        var field = target.GetType().GetField(data.name,BindingFlags.Public|BindingFlags.Instance);
        var property = target.GetType().GetProperty(data.name,BindingFlags.Public|BindingFlags.Instance);
        if (field != null || (property != null && property.CanWrite)) {
            var type = field != null ? field.FieldType : property.PropertyType;
            object value = reference ? Reference(data,map) : ConvertValue(data.value,type);
            if (field != null) field.SetValue(target,value); else property.SetValue(target,value);
        } else {
            var serialized = new SerializedObject(target);
            var prop = serialized.FindProperty(data.name);
            if (prop == null) throw new Exception("Missing property " + target.GetType().Name + "." + data.name);
            switch(data.kind) {
                case "float": prop.floatValue=float.Parse(data.value,CultureInfo.InvariantCulture); break;
                case "int": prop.intValue=int.Parse(data.value,CultureInfo.InvariantCulture); break;
                case "bool": prop.boolValue=bool.Parse(data.value); break;
                case "string": prop.stringValue=data.value; break;
                case "color": prop.colorValue=ColorValue(data.value); break;
                case "vector2": prop.vector2Value=(Vector2)ConvertValue(data.value,typeof(Vector2)); break;
                case "vector3": prop.vector3Value=(Vector3)ConvertValue(data.value,typeof(Vector3)); break;
                case "enum":
                    int index=Array.IndexOf(prop.enumNames,data.value);
                    if(index<0) throw new Exception("Unknown enum " + data.value); prop.enumValueIndex=index; break;
                case "reference": case "asset": prop.objectReferenceValue=Reference(data,map); break;
                default: throw new Exception("Unknown property kind " + data.kind);
            }
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }
        if (reference) evidence.references++;
        EditorUtility.SetDirty(target);
    }
    static void ImportArt() {
        var art=JsonUtility.FromJson<ArtInfo>(File.ReadAllText("Assets/Resources/studio_art.json"));
        foreach(var item in art.assets ?? Array.Empty<ArtEntry>()) {
            if(item.status!="ready") continue;
            var importer=AssetImporter.GetAtPath(item.path) as TextureImporter;
            if(importer==null) continue;
            importer.textureType=item.kind=="sprite" ? TextureImporterType.Sprite : TextureImporterType.Default;
            if(item.kind=="sprite") { importer.spriteImportMode=SpriteImportMode.Single; importer.spritePixelsPerUnit=item.pixels_per_unit; }
            importer.alphaIsTransparency=true; importer.textureCompression=TextureImporterCompression.Uncompressed;
            var spec=File.ReadAllText("Assets/Resources/game_spec.json");
            importer.filterMode=spec.IndexOf("pixel",StringComparison.OrdinalIgnoreCase)>=0 ? FilterMode.Point : FilterMode.Bilinear;
            importer.SaveAndReimport();
        }
    }
    static void Tags(Bundle bundle) {
        var manager=new SerializedObject(AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset")[0]);
        var tags=manager.FindProperty("tags");
        foreach(var tag in bundle.scenes.SelectMany(s=>s.objects).Select(o=>o.tag).Distinct()) {
            if(new[]{"Untagged","Player","MainCamera","Respawn","Finish","EditorOnly","GameController"}.Contains(tag)) continue;
            bool found=false;
            for(int i=0;i<tags.arraySize;i++) if(tags.GetArrayElementAtIndex(i).stringValue==tag) found=true;
            if(!found) { tags.InsertArrayElementAtIndex(tags.arraySize); tags.GetArrayElementAtIndex(tags.arraySize-1).stringValue=tag; }
        }
        manager.ApplyModifiedPropertiesWithoutUndo();
    }
    static Dictionary<string,Material> Materials(Bundle bundle) {
        var map=new Dictionary<string,Material>();
        foreach(var data in bundle.materials ?? Array.Empty<MaterialData>()) {
            var shader=Shader.Find(data.unlit ? (string.IsNullOrEmpty(data.texture)?"Unlit/Color":"Unlit/Texture") : "Standard");
            if(shader==null) throw new Exception("Required material shader unavailable");
            string path="Assets/Studio/Art/"+data.id+".mat";
            var mat=AssetDatabase.LoadAssetAtPath<Material>(path);
            if(mat==null) { mat=new Material(shader); AssetDatabase.CreateAsset(mat,path); }
            mat.shader=shader; mat.color=ColorValue(data.color);
            if(!string.IsNullOrEmpty(data.texture)) {
                mat.mainTexture=AssetDatabase.LoadAssetAtPath<Texture2D>(data.texture);
                if(mat.mainTexture==null) throw new Exception("Missing material texture " + data.texture);
            }
            if(mat.HasProperty("_Metallic")) mat.SetFloat("_Metallic",data.metallic);
            if(mat.HasProperty("_Glossiness")) mat.SetFloat("_Glossiness",data.smoothness);
            EditorUtility.SetDirty(mat); map.Add(data.id,mat);
        }
        return map;
    }
    [MenuItem("Studio/Assemble prompt-driven scenes")]
    public static void Assemble() {
        var evidence=new Evidence();
        try {
            var bundle=JsonUtility.FromJson<Bundle>(File.ReadAllText("Assets/Resources/studio_scenes.json"));
            if(bundle==null || bundle.version!=2 || bundle.scenes==null || bundle.scenes.Length==0) throw new Exception("Invalid v2 scene specification");
            // The base project uses a 2D-only URP renderer. New v2 projects use
            // built-in rendering for both 2D sprites and 3D lit materials.
            GraphicsSettings.defaultRenderPipeline=null;
            int previous=QualitySettings.GetQualityLevel();
            for(int i=0;i<QualitySettings.names.Length;i++) { QualitySettings.SetQualityLevel(i,false); QualitySettings.renderPipeline=null; }
            QualitySettings.SetQualityLevel(previous,false);
            Directory.CreateDirectory("Assets/Studio/Art"); Directory.CreateDirectory("Assets/Studio/Scenes"); Directory.CreateDirectory("Assets/Studio/Prefabs");
            Tags(bundle); ImportArt(); var materials=Materials(bundle); var paths=new List<string>();
            foreach(var scene in bundle.scenes) {
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
                var map=new Dictionary<string,GameObject>();
                foreach(var obj in scene.objects) {
                    GameObject go;
                    if(!string.IsNullOrEmpty(obj.model)) {
                        var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(obj.model);
                        if(prefab==null) throw new Exception("Missing model/prefab " + obj.model);
                        go=(GameObject)PrefabUtility.InstantiatePrefab(prefab);
                    } else if(!string.IsNullOrEmpty(obj.primitive)) go=GameObject.CreatePrimitive((PrimitiveType)Enum.Parse(typeof(PrimitiveType),obj.primitive));
                    else if(obj.components.Any(c=>c.type=="UnityEngine.RectTransform")) go=new GameObject(obj.name,typeof(RectTransform));
                    else go=new GameObject(obj.name);
                    go.name=obj.name; go.layer=obj.layer; go.tag=obj.tag;
                    go.transform.position=Vec(obj.position,Vector3.zero); go.transform.localScale=Vec(obj.scale,Vector3.one);
                    go.transform.eulerAngles=Vec(obj.rotation,Vector3.zero);
                    go.AddComponent<StudioObjectId>().id=obj.id;
                    map.Add(obj.id,go);
                    if(obj.mesh!=null && obj.mesh.vertices!=null && obj.mesh.vertices.Length>0) {
                        var mesh=new Mesh { name=scene.name+"_"+obj.id, vertices=obj.mesh.vertices, triangles=obj.mesh.triangles };
                        mesh.RecalculateNormals(); mesh.RecalculateBounds();
                        string meshPath="Assets/Studio/Art/"+mesh.name+".asset";
                        var old=AssetDatabase.LoadAssetAtPath<Mesh>(meshPath);
                        if(old!=null) { EditorUtility.CopySerialized(mesh,old); mesh=old; } else AssetDatabase.CreateAsset(mesh,meshPath);
                        go.AddComponent<MeshFilter>().sharedMesh=mesh; go.AddComponent<MeshRenderer>();
                    }
                    if(!string.IsNullOrEmpty(obj.sprite)) {
                        var sprite=AssetDatabase.LoadAssetAtPath<Sprite>(obj.sprite);
                        if(sprite==null) throw new Exception("Missing sprite " + obj.sprite);
                        var sr=go.AddComponent<SpriteRenderer>(); sr.sprite=sprite; sr.sortingOrder=obj.sorting_order; sr.color=ColorValue(obj.color);
                        go.transform.localScale=new Vector3(obj.scale[0]/sprite.bounds.size.x,obj.scale[1]/sprite.bounds.size.y,obj.scale[2]);
                    }
                    foreach(var comp in obj.components) { var type=Resolve(comp.type); if(go.GetComponent(type)==null) go.AddComponent(type); }
                    if(go.TryGetComponent<MeshCollider>(out var collider) && go.TryGetComponent<MeshFilter>(out var filter)) collider.sharedMesh=filter.sharedMesh;
                    if(!string.IsNullOrEmpty(obj.material)) {
                        if(!materials.TryGetValue(obj.material,out var material)) throw new Exception("Missing material " + obj.material);
                        foreach(var renderer in go.GetComponentsInChildren<Renderer>()) renderer.sharedMaterial=material;
                    }
                    evidence.objects++;
                }
                foreach(var obj in scene.objects) {
                    var go=map[obj.id];
                    if(!string.IsNullOrEmpty(obj.parent)) go.transform.SetParent(map[obj.parent].transform,true);
                    foreach(var comp in obj.components) foreach(var prop in comp.properties) SetProperty(go.GetComponent(Resolve(comp.type)),prop,map,evidence);
                    if(go.TryGetComponent<Camera>(out var camera)) camera.backgroundColor=ColorValue(scene.background_color);
                    go.SetActive(obj.active);
                }
                foreach(var obj in scene.objects.Where(o=>o.prefab)) {
                    PrefabUtility.SaveAsPrefabAsset(map[obj.id],"Assets/Studio/Prefabs/"+scene.name+"_"+obj.id+".prefab"); evidence.prefabs++;
                }
                string path="Assets/Studio/Scenes/"+scene.name+".unity";
                if(!EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene(),path)) throw new Exception("Scene save failed");
                paths.Add(path); evidence.scenes++;
            }
            EditorBuildSettings.scenes=paths.Select(p=>new EditorBuildSettingsScene(p,true)).ToArray();
            PlayerSettings.productName=bundle.title;
            if(bundle.orientation=="landscape") PlayerSettings.defaultInterfaceOrientation=UIOrientation.LandscapeLeft;
            else if(bundle.orientation=="portrait") PlayerSettings.defaultInterfaceOrientation=UIOrientation.Portrait;
            else if(bundle.orientation=="auto") PlayerSettings.defaultInterfaceOrientation=UIOrientation.AutoRotation;
            AssetDatabase.SaveAssets(); AssetDatabase.Refresh(); evidence.scene_paths=paths.ToArray(); evidence.success=true;
        } catch(Exception error) { evidence.error=error.ToString(); Debug.LogError(error); }
        Directory.CreateDirectory("Artifacts"); File.WriteAllText(Path.GetFullPath("Artifacts/assembly.json"),JsonUtility.ToJson(evidence,true));
        if(!evidence.success && Application.isBatchMode) EditorApplication.Exit(1);
    }
}
