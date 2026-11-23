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
    [Serializable] public class ProjectDimension { public string dimension; }
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
    static bool UsesPhysics2D(ObjectData obj) {
        return obj.components.Any(comp => {
            var type = Resolve(comp.type);
            return typeof(Rigidbody2D).IsAssignableFrom(type) || typeof(Collider2D).IsAssignableFrom(type);
        });
    }
    static bool DeclaresPhysics3D(ObjectData obj) {
        return obj.components.Any(comp => {
            var type = Resolve(comp.type);
            return typeof(Rigidbody).IsAssignableFrom(type) || typeof(Collider).IsAssignableFrom(type);
        });
    }
    static void RemovePrimitive3DColliderFor2D(GameObject go, ObjectData obj) {
        if (string.IsNullOrEmpty(obj.primitive) || !UsesPhysics2D(obj)) return;
        if (DeclaresPhysics3D(obj))
            throw new Exception("Object '"+obj.name+"' declares both 2D and 3D physics components; use one physics dimension per object.");
        // CreatePrimitive always adds a 3D collider. Keeping it on a 2D player or
        // ground makes Unity/MCP reject Rigidbody2D and can leave a broken body.
        foreach (var collider in go.GetComponents<Collider>()) UnityEngine.Object.DestroyImmediate(collider);
    }
    static void ApplyObjectColor(GameObject go, ObjectData obj, string sceneName) {
        if (!ColorUtility.TryParseHtmlString(obj.color, out var color)) throw new Exception("Invalid object color " + obj.color);
        foreach (var renderer in go.GetComponents<Renderer>()) {
            if (renderer is SpriteRenderer) continue; // SpriteRenderer.color is applied during sprite setup.
            if (string.IsNullOrEmpty(obj.material) && obj.color != "#FFFFFF") {
                var shader=Shader.Find("Unlit/Color") ?? Shader.Find("Sprites/Default") ?? Shader.Find("Standard");
                if(shader==null) throw new Exception("No supported shader for solid object color");
                var path="Assets/Studio/Art/"+sceneName+"_"+obj.id+"_Color.mat";
                var material=AssetDatabase.LoadAssetAtPath<Material>(path);
                if(material==null) { material=new Material(shader); AssetDatabase.CreateAsset(material,path); }
                material.shader=shader; material.color=color; EditorUtility.SetDirty(material);
                renderer.sharedMaterial=material;
                continue;
            }
            var block = new MaterialPropertyBlock(); renderer.GetPropertyBlock(block);
            block.SetColor("_Color",color); block.SetColor("_BaseColor",color);
            renderer.SetPropertyBlock(block);
        }
    }
    static Component RequireComponent(GameObject go, string name, string objectId) {
        var type = Resolve(name);
        var component = go.GetComponent(type);
        if (component == null) component = go.AddComponent(type);
        if (component == null || go.GetComponent(type) == null)
            throw new Exception("Could not attach required component "+type.Name+" to object '"+go.name+"' (id '"+objectId+"').");
        return component;
    }
    static object ConvertValue(string value, Type type) {
        if (type.IsEnum) {
            if (type == typeof(TextAnchor)) {
                switch (value.Trim().ToLowerInvariant()) {
                    case "center": value = "MiddleCenter"; break;
                    case "top": value = "UpperCenter"; break;
                    case "bottom": value = "LowerCenter"; break;
                    case "left": value = "MiddleLeft"; break;
                    case "right": value = "MiddleRight"; break;
                }
            }
            return Enum.Parse(type,value,true);
        }
        if (type == typeof(Color)) return ColorValue(value);
        if (type == typeof(Vector3)) { var v=Numbers(value); return new Vector3(v[0],v[1],v[2]); }
        if (type == typeof(Vector2)) { var v=Numbers(value); return new Vector2(v[0],v[1]); }
        return Convert.ChangeType(value,type,CultureInfo.InvariantCulture);
    }
    static UnityEngine.Object Reference(PropertyData data, Dictionary<string,GameObject> map, Type expectedType) {
        if (data.kind == "asset") {
            if (!data.value.StartsWith("Assets/") || data.value.Contains("..")) throw new Exception("Unsafe asset path");
            var asset = AssetDatabase.LoadMainAssetAtPath(data.value);
            if (asset == null) throw new Exception("Missing asset " + data.value);
            return asset;
        }
        var parts = data.value.Split(':');
        if (!map.TryGetValue(parts[0],out var go)) throw new Exception("Missing reference " + data.value);
        if (parts.Length == 1 && (expectedType == null || expectedType == typeof(UnityEngine.Object)
                || expectedType == typeof(GameObject) || expectedType.IsInstanceOfType(go))) return go;
        if (parts.Length == 1 && expectedType != null && typeof(Component).IsAssignableFrom(expectedType)) {
            var inferred = go.GetComponent(expectedType);
            if (inferred == null) throw new Exception("Missing reference component " + go.name + ":" + expectedType.Name);
            return inferred;
        }
        if (parts.Length == 1) throw new Exception("Reference " + data.value + " points to a GameObject but " + expectedType.Name + " is required");
        var component=go.GetComponent(Resolve(parts[1]));
        if(component==null) throw new Exception("Missing reference component " + data.value);
        if (expectedType != null && !expectedType.IsInstanceOfType(component))
            throw new Exception("Reference " + data.value + " is not assignable to " + expectedType.Name);
        return component;
    }
    static void SetProperty(Component target, PropertyData data, Dictionary<string,GameObject> map, Evidence evidence) {
        bool reference = data.kind == "reference" || data.kind == "asset";
        var field = target.GetType().GetField(data.name,BindingFlags.Public|BindingFlags.Instance);
        var property = target.GetType().GetProperty(data.name,BindingFlags.Public|BindingFlags.Instance);
        if (field != null || (property != null && property.CanWrite)) {
            var type = field != null ? field.FieldType : property.PropertyType;
            object value = reference ? Reference(data,map,type) : ConvertValue(data.value,type);
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
                case "reference": case "asset": prop.objectReferenceValue=Reference(data,map,typeof(UnityEngine.Object)); break;
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
            if(bundle.dimension!="2D" && bundle.dimension!="3D" && bundle.dimension!="mixed")
                throw new Exception("Unsupported scene dimension: "+bundle.dimension);
            string dimensionPath="Assets/Resources/studio_dimension.json";
            if(File.Exists(dimensionPath)) {
                var projectDimension=JsonUtility.FromJson<ProjectDimension>(File.ReadAllText(dimensionPath));
                if(projectDimension==null || projectDimension.dimension!=bundle.dimension)
                    throw new Exception("Scene dimension does not match the Unity project dimension selected before Editor startup.");
            }
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
                    go.SetActive(false);
                    go.name=obj.name; go.layer=obj.layer; go.tag=obj.tag;
                    go.transform.position=Vec(obj.position,Vector3.zero); go.transform.localScale=Vec(obj.scale,Vector3.one);
                    go.transform.eulerAngles=Vec(obj.rotation,Vector3.zero);
                    go.AddComponent<StudioObjectId>().id=obj.id;
                    map.Add(obj.id,go);
                    RemovePrimitive3DColliderFor2D(go,obj);
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
                    foreach(var comp in obj.components) RequireComponent(go,comp.type,obj.id);
                    if (UsesPhysics2D(obj) && (go.GetComponent<Rigidbody>() != null || go.GetComponent<Collider>() != null))
                        throw new Exception("Object '"+obj.name+"' combines 2D and 3D physics components.");
                    if(go.TryGetComponent<MeshCollider>(out var collider) && go.TryGetComponent<MeshFilter>(out var filter)) collider.sharedMesh=filter.sharedMesh;
                    if(!string.IsNullOrEmpty(obj.material)) {
                        if(!materials.TryGetValue(obj.material,out var material)) throw new Exception("Missing material " + obj.material);
                        foreach(var renderer in go.GetComponentsInChildren<Renderer>()) renderer.sharedMaterial=material;
                    }
                    ApplyObjectColor(go,obj,scene.name);
                    evidence.objects++;
                }
                foreach(var obj in scene.objects) {
                    var go=map[obj.id];
                    if(!string.IsNullOrEmpty(obj.parent)) {
                        var parent=map[obj.parent].transform;
                        if(go.transform is RectTransform rect && parent is RectTransform) {
                            // UI transforms store screen-space coordinates relative to their Canvas.
                            rect.SetParent(parent,false); rect.anchoredPosition3D=Vec(obj.position,Vector3.zero);
                            rect.localScale=Vec(obj.scale,Vector3.one); rect.localEulerAngles=Vec(obj.rotation,Vector3.zero);
                        } else go.transform.SetParent(parent,true);
                    }
                    foreach(var comp in obj.components) {
                        var target=RequireComponent(go,comp.type,obj.id);
                        if(target is Camera generatedCamera && !comp.properties.Any(prop=>prop.name=="orthographic"))
                            generatedCamera.orthographic=bundle.dimension=="2D";
                        foreach(var prop in comp.properties) {
                            try { SetProperty(target,prop,map,evidence); }
                            catch(Exception error) { throw new Exception("Failed to set "+Resolve(comp.type).Name+"."+prop.name+" on object '"+obj.name+"' (id '"+obj.id+"'): "+error.GetBaseException().Message,error); }
                        }
                    }
                    EnsureVisibleUI(go,obj);
                    if(go.TryGetComponent<Camera>(out var camera)) {
                        camera.clearFlags=CameraClearFlags.SolidColor;
                        camera.backgroundColor=ColorValue(scene.background_color);
                    }
                }
                // Wire every object before enabling scripts and their lifecycle.
                foreach(var obj in scene.objects) map[obj.id].SetActive(obj.active);
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
    static void EnsureVisibleUI(GameObject go,ObjectData obj) {
        var text=go.GetComponent<UnityEngine.UI.Text>();
        if(text!=null && text.font==null) text.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        var image=go.GetComponent<UnityEngine.UI.Image>();
        if(image!=null && image.sprite==null) {
            // A newly added UGUI Image has no source sprite and draws no mesh.
            // Use Unity's built-in UI sprite so generated touch buttons are visible
            // without importing external art.
            var sprite=AssetDatabase.GetBuiltinExtraResource<Sprite>("UI/Skin/UISprite.psd") ??
                       AssetDatabase.GetBuiltinExtraResource<Sprite>("UI/Skin/Background.psd") ??
                       Resources.GetBuiltinResource<Sprite>("UI/Skin/UISprite.psd");
            if(sprite==null) throw new Exception("Button/Image on '"+obj.name+"' has no sprite and Unity's built-in UI sprite is unavailable.");
            image.sprite=sprite;
            image.type=UnityEngine.UI.Image.Type.Simple;
        }
        var button=go.GetComponent<UnityEngine.UI.Button>();
        if(button!=null && button.targetGraphic==null && image!=null) button.targetGraphic=image;
        if(button!=null && button.targetGraphic==null)
            throw new Exception("Button '"+obj.name+"' has no visible target Graphic.");
    }
}
