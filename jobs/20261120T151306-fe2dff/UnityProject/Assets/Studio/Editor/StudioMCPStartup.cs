using System;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

// Trusted, project-local bootstrap. No compile-time dependency on the optional MCP package.
[InitializeOnLoad]
public static class StudioMCPStartup
{
    [Serializable] class Config { public bool enabled; public string python; public string transport; }
    static readonly Config config;
    static readonly double deadline;
    static double nextAttempt;

    static StudioMCPStartup()
    {
        string root = Directory.GetParent(Application.dataPath).FullName;
        string path = Path.Combine(root, "ProjectSettings/StudioMCP.json");
        if (!File.Exists(path)) return;
        try { config = JsonUtility.FromJson<Config>(File.ReadAllText(path)); }
        catch (Exception error) { Debug.LogWarning("Studio MCP configuration: " + error.Message); return; }
        if (config == null || !config.enabled) return;
        if (!File.Exists(config.python))
        {
            Debug.LogWarning("Studio MCP: configured Python is missing; rerun Studio project setup.");
            return;
        }
        // uvx launched from the Unity HTTP window inherits this interpreter choice too.
        Environment.SetEnvironmentVariable("UV_PYTHON", config.python);
        if (Application.isBatchMode) return;
        deadline = EditorApplication.timeSinceStartup + 120;
        EditorApplication.update += StartWhenReady;
    }

    static void StartWhenReady()
    {
        if (EditorApplication.timeSinceStartup < nextAttempt) return;
        nextAttempt = EditorApplication.timeSinceStartup + 1;
        if (EditorApplication.timeSinceStartup > deadline)
        {
            EditorApplication.update -= StartWhenReady;
            Debug.LogWarning("Studio MCP: bridge startup timed out; see the MCP package/Editor logs.");
            return;
        }
        if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
        var type = AppDomain.CurrentDomain.GetAssemblies()
            .Select(a => a.GetType("MCPForUnity.Editor.McpCiBoot", false)).FirstOrDefault(t => t != null);
        var start = type?.GetMethod("StartStdioForCi", BindingFlags.Public | BindingFlags.Static);
        if (start == null) return;
        try
        {
            // Session-scoped pin: do not rewrite the user's global Unity EditorPrefs.
            start.Invoke(null, null);
            EditorApplication.update -= StartWhenReady;
            Debug.Log("Studio MCP: automatic stdio bridge started; child processes use Python " + config.python);
        }
        catch (Exception error) { Debug.LogWarning("Studio MCP startup retry: " + error.GetBaseException().Message); }
    }
}
