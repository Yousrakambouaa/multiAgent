using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

// Trusted harness evidence: only a fresh completed compiler callback can pass live compilation.
[InitializeOnLoad]
public static class StudioCompilationEvidence
{
    [Serializable] class Evidence
    {
        public string project;
        public long started_unix_ms;
        public bool completed;
        public bool success;
        public string[] errors;
    }
    static readonly List<string> errors = new();
    static long started;
    static StudioCompilationEvidence()
    {
        CompilationPipeline.compilationStarted += _ =>
        {
            errors.Clear(); started = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(); Save(false);
        };
        CompilationPipeline.assemblyCompilationFinished += (assembly, messages) =>
        {
            foreach (var message in messages)
                if (message.type == CompilerMessageType.Error)
                    errors.Add($"{message.file}({message.line},{message.column}): {message.message}");
            Save(false);
        };
        CompilationPipeline.compilationFinished += _ => Save(true);
    }
    static void Save(bool completed)
    {
        string project = Directory.GetParent(Application.dataPath).FullName;
        var evidence = new Evidence { project = project, started_unix_ms = started,
            completed = completed, success = completed && errors.Count == 0, errors = errors.ToArray() };
        Directory.CreateDirectory(Path.Combine(project, "Artifacts"));
        File.WriteAllText(Path.Combine(project, "Artifacts/compilation.json"), JsonUtility.ToJson(evidence, true));
    }
}
