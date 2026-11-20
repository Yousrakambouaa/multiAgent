using System.IO;
using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Studio.Runtime;

public class PromptSceneTests {
    [Test] public void EverySpecifiedSceneAndComponentExists() {
        var bundle=JsonUtility.FromJson<UniversalSceneBuilder.Bundle>(File.ReadAllText("Assets/Resources/studio_scenes.json"));
        Assert.AreEqual(2,bundle.version);
        CollectionAssert.AreEqual(bundle.scenes.Select(s=>"Assets/Studio/Scenes/"+s.name+".unity").ToArray(),EditorBuildSettings.scenes.Select(s=>s.path).ToArray());
        foreach(var spec in bundle.scenes) {
            var scene=EditorSceneManager.OpenScene("Assets/Studio/Scenes/"+spec.name+".unity",OpenSceneMode.Single);
            var ids=scene.GetRootGameObjects().SelectMany(r=>r.GetComponentsInChildren<StudioObjectId>(true)).ToDictionary(i=>i.id,i=>i.gameObject);
            foreach(var obj in spec.objects) {
                Assert.IsTrue(ids.ContainsKey(obj.id),obj.id);
                var go=ids[obj.id];
                Assert.AreEqual(obj.active,go.activeSelf,obj.id);
                Assert.Less(Vector3.Distance(new Vector3(obj.position[0],obj.position[1],obj.position[2]),go.transform.position),.001f,obj.id);
                foreach(var comp in obj.components) Assert.NotNull(go.GetComponent(UniversalSceneBuilder.Resolve(comp.type)),obj.id+":"+comp.type);
                if(!string.IsNullOrEmpty(obj.sprite)) Assert.NotNull(go.GetComponent<SpriteRenderer>().sprite,obj.id);
                if(!string.IsNullOrEmpty(obj.model)) Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(go),obj.id);
                if(obj.mesh!=null && obj.mesh.vertices!=null && obj.mesh.vertices.Length>0) Assert.NotNull(go.GetComponent<MeshFilter>().sharedMesh);
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
