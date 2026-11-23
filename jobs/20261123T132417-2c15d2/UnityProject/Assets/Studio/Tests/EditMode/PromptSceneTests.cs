using System.IO;
using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;
using Studio.Runtime;

public class PromptSceneTests {
    static UniversalSceneBuilder.Bundle MinimalBundle(string dimension) {
        return new UniversalSceneBuilder.Bundle {
            version=2, dimension=dimension, scenes=new[] {
                new UniversalSceneBuilder.SceneData {
                    name="Validation", objects=new[] {
                        new UniversalSceneBuilder.ObjectData {
                            id="camera", name="Camera", position=new[]{0f,0f,-10f}, scale=new[]{1f,1f,1f},
                            rotation=new[]{0f,0f,0f}, components=new[] {
                                new UniversalSceneBuilder.ComponentData { type="UnityEngine.Camera",
                                    properties=new[] { new UniversalSceneBuilder.PropertyData {
                                        name="orthographic", kind="bool", value="true" } } }
                            }
                        },
                        new UniversalSceneBuilder.ObjectData {
                            id="floor", name="Floor", position=new[]{0f,-1f,0f}, scale=new[]{1f,1f,1f},
                            rotation=new[]{0f,0f,0f}, components=new[] {
                                new UniversalSceneBuilder.ComponentData { type="UnityEngine.BoxCollider2D",
                                    properties=new[] { new UniversalSceneBuilder.PropertyData {
                                        name="size", kind="vector2", value="2,1" } } }
                            }
                        }
                    }
                }
            }
        };
    }
    [Test] public void PreflightValidatesUnityPropertiesAndColliderBoundsBeforeAssembly() {
        var bundle=MinimalBundle("2D");
        Assert.DoesNotThrow(()=>UniversalSceneBuilder.ValidateBundle(bundle));
        bundle.scenes[0].objects[1].components[0].properties[0].value="0,1";
        Assert.Throws<System.Exception>(()=>UniversalSceneBuilder.ValidateBundle(bundle));
        bundle=MinimalBundle("2D");
        bundle.scenes[0].objects[0].components[0].properties[0].kind="string";
        Assert.Throws<System.Exception>(()=>UniversalSceneBuilder.ValidateBundle(bundle));
    }
    [Test] public void MixedScenesRequireAnExplicitCameraProjection() {
        var bundle=MinimalBundle("mixed");
        bundle.scenes[0].objects[0].components[0].properties=System.Array.Empty<UniversalSceneBuilder.PropertyData>();
        Assert.Throws<System.Exception>(()=>UniversalSceneBuilder.ValidateBundle(bundle));
        bundle.scenes[0].objects[0].components[0].properties=new[] {
            new UniversalSceneBuilder.PropertyData { name="orthographic", kind="bool", value="true" }
        };
        Assert.DoesNotThrow(()=>UniversalSceneBuilder.ValidateBundle(bundle));
    }
    [Test] public void EverySpecifiedSceneAndComponentExists() {
        var bundle=JsonUtility.FromJson<UniversalSceneBuilder.Bundle>(File.ReadAllText("Assets/Resources/studio_scenes.json"));
        Assert.AreEqual(2,bundle.version);
        Assert.DoesNotThrow(()=>UniversalSceneBuilder.ValidateBundle(bundle));
        CollectionAssert.AreEqual(bundle.scenes.Select(s=>"Assets/Studio/Scenes/"+s.name+".unity").ToArray(),EditorBuildSettings.scenes.Select(s=>s.path).ToArray());
        foreach(var spec in bundle.scenes) {
            var scene=EditorSceneManager.OpenScene("Assets/Studio/Scenes/"+spec.name+".unity",OpenSceneMode.Single);
            Assert.IsNotEmpty(scene.GetRootGameObjects().SelectMany(r=>r.GetComponentsInChildren<Camera>(true)),spec.name+" has no camera");
            var ids=scene.GetRootGameObjects().SelectMany(r=>r.GetComponentsInChildren<StudioObjectId>(true)).ToDictionary(i=>i.id,i=>i.gameObject);
            foreach(var obj in spec.objects) {
                Assert.IsTrue(ids.ContainsKey(obj.id),obj.id);
                var go=ids[obj.id];
                Assert.AreEqual(obj.active,go.activeSelf,obj.id);
                var rect=go.GetComponent<RectTransform>();
                if(rect==null) {
                    var expectedPosition=new Vector3(obj.position[0],obj.position[1],obj.position[2]);
                    Assert.Less(Vector3.Distance(expectedPosition,go.transform.position),.001f,obj.id);
                } else if(go.GetComponent<Canvas>()==null) {
                    var expectedPosition=new Vector3(obj.position[0],obj.position[1],obj.position[2]);
                    Assert.Less(Vector3.Distance(expectedPosition,rect.anchoredPosition3D),.001f,obj.id);
                }
                foreach(var comp in obj.components) Assert.NotNull(go.GetComponent(UniversalSceneBuilder.Resolve(comp.type)),obj.id+":"+comp.type);
                var image=go.GetComponent<Image>();
                if(image!=null) Assert.NotNull(image.sprite,obj.id+" has an Image with no visible sprite");
                var button=go.GetComponent<Button>();
                if(button!=null) Assert.NotNull(button.targetGraphic,obj.id+" has no Button target Graphic");
                var text=go.GetComponent<Text>();
                if(text!=null) Assert.NotNull(text.font,obj.id+" has no UI font");
                if(go.GetComponent<Rigidbody2D>()!=null || go.GetComponents<Collider2D>().Length>0) {
                    Assert.IsNull(go.GetComponent<Rigidbody>(),obj.id+" mixes 2D and 3D rigidbodies");
                    Assert.IsEmpty(go.GetComponents<Collider>(),obj.id+" retains an implicit 3D collider");
                }
                if(!string.IsNullOrEmpty(obj.sprite)) Assert.NotNull(go.GetComponent<SpriteRenderer>().sprite,obj.id);
                if(!string.IsNullOrEmpty(obj.model)) Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(go),obj.id);
                if(obj.mesh!=null && obj.mesh.vertices!=null && obj.mesh.vertices.Length>0) Assert.NotNull(go.GetComponent<MeshFilter>().sharedMesh);
            }
            if(spec.objects.Any(o=>o.components.Any(c=>c.type.EndsWith("Button")))) {
                Assert.IsTrue(ids.Values.Any(go=>go.GetComponent<EventSystem>()!=null),"Missing UI EventSystem");
                var inputModule=UniversalSceneBuilder.Resolve("UnityEngine.InputSystem.UI.InputSystemUIInputModule");
                Assert.IsTrue(ids.Values.Any(go=>go.GetComponent(inputModule)!=null),"Missing Input System UI module");
            }
        }
    }
    [Test] public void GeneratedScenesHaveNoMissingScriptsOrBrokenSerializedReferences() {
        foreach(var entry in EditorBuildSettings.scenes) {
            var scene=EditorSceneManager.OpenScene(entry.path,OpenSceneMode.Single);
            foreach(var transform in scene.GetRootGameObjects().SelectMany(r=>r.GetComponentsInChildren<Transform>(true))) {
                Assert.AreEqual(0,GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(transform.gameObject),transform.name);
                foreach(var component in transform.GetComponents<Component>()) {
                    Assert.NotNull(component,transform.name);
                    var iterator=new SerializedObject(component).GetIterator();
                    while(iterator.NextVisible(true)) if(iterator.propertyType==SerializedPropertyType.ObjectReference)
                        Assert.IsFalse(iterator.objectReferenceValue==null && iterator.objectReferenceInstanceIDValue!=0,transform.name+":"+iterator.name);
                }
            }
        }
    }
}
