"""Project-local MCP installation and startup configuration for generated games."""
import json
import os
import sys
from pathlib import Path

from studio.state import write_json

MCP_PACKAGE = "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#v10.3.0"


def prepare_mcp_project(project):
    project = Path(project)
    enabled = os.environ.get("STUDIO_UNITY_MCP_ENABLED", "1").casefold() in ("1", "true", "yes")
    config_path = project / "ProjectSettings/StudioMCP.json"
    if not enabled:
        # Respect a user's existing project-local setup when Studio MCP is disabled.
        return
    manifest_path = project / "Packages/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dependencies = manifest.setdefault("dependencies", {})
    if "com.coplaydev.unity-mcp" not in dependencies:
        dependencies["com.coplaydev.unity-mcp"] = MCP_PACKAGE
        write_json(manifest_path, manifest)
    write_json(config_path, {"enabled": True, "python": sys.executable, "transport": "stdio"})
