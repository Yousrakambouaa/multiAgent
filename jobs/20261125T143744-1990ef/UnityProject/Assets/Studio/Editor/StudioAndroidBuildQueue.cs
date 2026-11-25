using System;
using System.IO;
using UnityEditor;
using UnityEngine;

// Trusted build orchestration. The MCP request returns before platform import
// or BuildPlayer blocks the Editor, and SessionState survives the target reload.
[InitializeOnLoad]
public static class StudioAndroidBuildQueue
{
    const string Pending = "Studio.AndroidBuild.Pending";
    const string Switching = "Studio.AndroidBuild.Switching";
    static readonly bool ReloadedAfterSwitch;
    [Serializable] class Result { public bool success; public string stage = "android_build"; public string error; }
    [Serializable] class Progress { public string phase; }

    static StudioAndroidBuildQueue()
    {
        ReloadedAfterSwitch = SessionState.GetBool(Switching, false) &&
                              EditorUserBuildSettings.activeBuildTarget == BuildTarget.Android;
        if (!Application.isBatchMode && SessionState.GetBool(Pending, false))
            EditorApplication.update += Tick;
    }

    [MenuItem("Studio/Build Android APK")]
    public static void Enqueue()
    {
        if (SessionState.GetBool(Pending, false) || BuildPipeline.isBuildingPlayer) return;
        SessionState.SetBool(Pending, true);
        SessionState.SetBool(Switching, false);
        WriteProgress("queued");
        EditorApplication.update -= Tick;
        EditorApplication.update += Tick;
    }

    static void WriteProgress(string phase)
    {
        Directory.CreateDirectory("Artifacts");
        File.WriteAllText(Path.GetFullPath("Artifacts/android_progress.json"), JsonUtility.ToJson(new Progress { phase = phase }, true));
    }

    static void Fail(string error)
    {
        SessionState.SetBool(Pending, false);
        SessionState.SetBool(Switching, false);
        EditorApplication.update -= Tick;
        Directory.CreateDirectory("Artifacts");
        File.WriteAllText(Path.GetFullPath("Artifacts/android.json"), JsonUtility.ToJson(new Result { error = error }, true));
        Debug.LogError(error);
    }

    static void Tick()
    {
        if (!SessionState.GetBool(Pending, false)) { EditorApplication.update -= Tick; return; }
        if (EditorApplication.isCompiling || EditorApplication.isUpdating || BuildPipeline.isBuildingPlayer) return;
        if (EditorApplication.isPlayingOrWillChangePlaymode) { Fail("Stop Play mode before building Android."); return; }
        if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
        {
            if (SessionState.GetBool(Switching, false)) return;
            SessionState.SetBool(Switching, true);
            WriteProgress("switching_to_android");
            if (!EditorUserBuildSettings.SwitchActiveBuildTargetAsync(BuildTargetGroup.Android, BuildTarget.Android))
                Fail("Unity could not switch to Android; check Android Build Support and the Editor log.");
            return;
        }
        if (SessionState.GetBool(Switching, false) && !ReloadedAfterSwitch) return;
        SessionState.SetBool(Pending, false);
        SessionState.SetBool(Switching, false);
        EditorApplication.update -= Tick;
        WriteProgress("building_apk");
        StudioSceneBuilder.BuildAndroid();
        WriteProgress("finished");
    }
}
