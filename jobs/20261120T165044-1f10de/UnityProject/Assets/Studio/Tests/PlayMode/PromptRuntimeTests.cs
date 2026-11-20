using System;
using System.IO;
using System.Linq;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.UI;
using UnityEngine.EventSystems;
using Studio.Runtime;

public class PromptRuntimeTests {
    [Serializable] public class Plan { public Probe[] probes; }
    [Serializable] public class Probe { public string id, scene; public string[] requirement_ids; public Step[] steps; public Check[] assertions; }
    [Serializable] public class Step { public string action, object_id, component, member, value; public int frames; }
    [Serializable] public class Check { public string kind, object_id, component, member, comparison, value; }
    [Serializable] public class Record { public string id, error; public string[] requirement_ids; public bool passed; }
    [Serializable] public class Evidence { public Record[] checks; }
    Keyboard keyboard;
    HashSet<Key> held=new HashSet<Key>();
    static GameObject Find(string id) {
        return UnityEngine.Object.FindObjectsByType<StudioObjectId>(FindObjectsInactive.Include,FindObjectsSortMode.None)
            .Where(i=>i.id==id && i.gameObject.scene==SceneManager.GetActiveScene()).Select(i=>i.gameObject).SingleOrDefault();
    }
    static Component Target(string id,string type) {
        var go=Find(id); Assert.NotNull(go,"Missing object "+id);
        var component=go.GetComponents<Component>().FirstOrDefault(c=>c!=null &&
            (c.GetType().FullName==type || c.GetType().Name==type));
        Assert.NotNull(component,"Missing component "+id+":"+type); return component;
    }
    static Key ParseKey(string member) {
        try { return (Key)Enum.Parse(typeof(Key),member,true); }
        catch (ArgumentException) {
            if (member.EndsWith("Key",StringComparison.OrdinalIgnoreCase))
                return (Key)Enum.Parse(typeof(Key),member.Substring(0,member.Length-3),true);
            throw;
        }
    }
    static object Read(Component component,string member) {
        var field=component.GetType().GetField(member,BindingFlags.Public|BindingFlags.Instance);
        if(field!=null) return field.GetValue(component);
        var property=component.GetType().GetProperty(member,BindingFlags.Public|BindingFlags.Instance);
        if(property!=null) return property.GetValue(component);
        var getter=component.GetType().GetMethod(member,BindingFlags.Public|BindingFlags.Instance,null,Type.EmptyTypes,null);
        Assert.NotNull(getter,"Missing public field, property or zero-argument state getter "+member);
        return getter.Invoke(component,null);
    }
    static void Set(Component component,string member,string text) {
        var field=component.GetType().GetField(member,BindingFlags.Public|BindingFlags.Instance);
        var property=component.GetType().GetProperty(member,BindingFlags.Public|BindingFlags.Instance);
        var type=field!=null ? field.FieldType : property?.PropertyType;
        Assert.NotNull(type,"Missing member "+member);
        object value;
        if(type==typeof(Vector3)) { var n=text.Trim('[',']').Split(',').Select(s=>float.Parse(s,CultureInfo.InvariantCulture)).ToArray(); value=new Vector3(n[0],n[1],n[2]); }
        else value=type.IsEnum ? Enum.Parse(type,text) : Convert.ChangeType(text,type,CultureInfo.InvariantCulture);
        if(field!=null) field.SetValue(component,value); else property.SetValue(component,value);
    }
    IEnumerator Run(Probe probe) {
        held.Clear(); InputSystem.QueueStateEvent(keyboard,new KeyboardState());
        Time.timeScale=1;
        yield return SceneManager.LoadSceneAsync(probe.scene);
        for(int i=0;i<5;i++) yield return null;
        var positions=UnityEngine.Object.FindObjectsByType<StudioObjectId>(FindObjectsInactive.Include,FindObjectsSortMode.None)
            .ToDictionary(i=>i.id,i=>i.transform.position);
        foreach(var step in probe.steps) {
            if(step.action=="key") {
                var key=ParseKey(step.member);
                if(bool.Parse(step.value)) held.Add(key); else held.Remove(key);
                InputSystem.QueueStateEvent(keyboard,new KeyboardState(held.ToArray()));
            } else if(step.action=="set") Set(Target(step.object_id,step.component),step.member,step.value);
            else if(step.action=="invoke") {
                var target=Target(step.object_id,step.component);
                var method=target.GetType().GetMethod(step.member,BindingFlags.Public|BindingFlags.Instance,null,Type.EmptyTypes,null);
                Assert.NotNull(method,"Missing gameplay method "+step.member); method.Invoke(target,null);
            } else if(step.action=="click") {
                var go=Find(step.object_id); Assert.NotNull(go); var button=go.GetComponent<Button>();
                Assert.NotNull(button); Assert.IsTrue(button.interactable); button.onClick.Invoke();
            } else if(step.action=="pointer") {
                var go=Find(step.object_id); Assert.NotNull(go);
                var pointer=new PointerEventData(EventSystem.current) { pointerId=-1 };
                if(step.value=="down") Assert.IsTrue(ExecuteEvents.Execute<IPointerDownHandler>(go,pointer,ExecuteEvents.pointerDownHandler));
                else if(step.value=="up") ExecuteEvents.Execute<IPointerUpHandler>(go,pointer,ExecuteEvents.pointerUpHandler);
                else Assert.Fail("Pointer steps require value down or up");
            } else Assert.AreEqual("wait",step.action);
            for(int i=0;i<step.frames;i++) yield return null;
        }
        foreach(var check in probe.assertions) {
            if(check.kind=="scene") { Assert.AreEqual(check.value,SceneManager.GetActiveScene().name); continue; }
            var go=Find(check.object_id);
            if(check.kind=="exists") { Assert.AreEqual(bool.Parse(check.value),go!=null); continue; }
            Assert.NotNull(go,check.object_id);
            if(check.kind=="active") Assert.AreEqual(bool.Parse(check.value),go.activeInHierarchy);
            else if(check.kind=="position_changed") Assert.Greater(Vector3.Distance(positions[check.object_id],go.transform.position),float.Parse(check.value,CultureInfo.InvariantCulture));
            else {
                Assert.AreEqual("property",check.kind);
                var value=Read(Target(check.object_id,check.component),check.member);
                if(check.comparison=="greater") Assert.Greater(Convert.ToDouble(value,CultureInfo.InvariantCulture),double.Parse(check.value,CultureInfo.InvariantCulture));
                else if(check.comparison=="less") Assert.Less(Convert.ToDouble(value,CultureInfo.InvariantCulture),double.Parse(check.value,CultureInfo.InvariantCulture));
                else if(value is float || value is double) Assert.AreEqual(double.Parse(check.value,CultureInfo.InvariantCulture),Convert.ToDouble(value,CultureInfo.InvariantCulture),.001);
                else Assert.AreEqual(check.value.ToLowerInvariant(),Convert.ToString(value,CultureInfo.InvariantCulture).ToLowerInvariant());
            }
        }
        LogAssert.NoUnexpectedReceived();
    }
    [UnityTest] public IEnumerator ExecutesEveryRequirementLinkedGameplayProbe() {
        var plan=JsonUtility.FromJson<Plan>(Resources.Load<TextAsset>("studio_probes").text);
        Assert.IsNotEmpty(plan.probes);
        keyboard=InputSystem.AddDevice<Keyboard>(); keyboard.MakeCurrent();
        var records=new List<Record>();
        foreach(var probe in plan.probes) {
            var record=new Record { id=probe.id,requirement_ids=probe.requirement_ids,passed=true };
            var routine=Run(probe);
            while(true) {
                bool more; object current=null;
                try { more=routine.MoveNext(); if(more) current=routine.Current; }
                catch(Exception error) { record.passed=false; record.error=error.ToString(); break; }
                if(!more) break;
                yield return current;
            }
            records.Add(record);
            Directory.CreateDirectory("Artifacts");
            File.WriteAllText(Path.GetFullPath("Artifacts/runtime_checks.json"),JsonUtility.ToJson(new Evidence { checks=records.ToArray() },true));
        }
        var camera=Camera.main;
        if(camera!=null) {
            var target=new RenderTexture(960,540,24); camera.targetTexture=target; camera.Render(); RenderTexture.active=target;
            var texture=new Texture2D(960,540,TextureFormat.RGB24,false); texture.ReadPixels(new Rect(0,0,960,540),0,0); texture.Apply();
            File.WriteAllBytes(Path.GetFullPath("Artifacts/preview.png"),texture.EncodeToPNG());
            camera.targetTexture=null; RenderTexture.active=null; UnityEngine.Object.Destroy(target); UnityEngine.Object.Destroy(texture);
        }
        Assert.IsTrue(records.All(r=>r.passed),string.Join("\n",records.Where(r=>!r.passed).Select(r=>r.id+": "+r.error)));
    }
    [UnityTearDown] public IEnumerator Cleanup() {
        if(keyboard!=null) InputSystem.RemoveDevice(keyboard);
        held.Clear(); Time.timeScale=1; LogAssert.NoUnexpectedReceived(); yield return null;
    }
}
