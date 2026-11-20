import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from studio.mcp_setup import MCP_PACKAGE, prepare_mcp_project


class MCPProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(__file__).resolve().parent / (".mcp-setup-test-" + uuid4().hex)
        self.project.mkdir()
        self.addCleanup(shutil.rmtree, self.project)
        (self.project / "Packages").mkdir()
        (self.project / "ProjectSettings").mkdir()
        self.manifest = self.project / "Packages/manifest.json"
        self.config = self.project / "ProjectSettings/StudioMCP.json"

    def test_enabled_by_default_adds_package_and_uses_studio_python(self):
        self.manifest.write_text('{"dependencies":{"com.unity.test-framework":"1.0.0"}}', encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            prepare_mcp_project(self.project)

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["dependencies"]["com.coplaydev.unity-mcp"], MCP_PACKAGE)
        self.assertEqual(manifest["dependencies"]["com.unity.test-framework"], "1.0.0")
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), {
            "enabled": True, "python": sys.executable, "transport": "stdio"
        })

    def test_opt_out_preserves_existing_project_configuration(self):
        self.manifest.write_text('{"dependencies":{}}', encoding="utf-8")
        original = {"enabled": True, "python": "C:/Unity/python.exe", "transport": "stdio"}
        self.config.write_text(json.dumps(original), encoding="utf-8")
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "0"}, clear=True):
            prepare_mcp_project(self.project)

        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), original)
        self.assertNotIn("com.coplaydev.unity-mcp", json.loads(self.manifest.read_text())["dependencies"])

    def test_existing_package_source_is_preserved(self):
        original_source = "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"
        self.manifest.write_text(json.dumps({"dependencies": {"com.coplaydev.unity-mcp": original_source}}), encoding="utf-8")
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "true"}, clear=True):
            prepare_mcp_project(self.project)

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["dependencies"]["com.coplaydev.unity-mcp"], original_source)


if __name__ == "__main__":
    unittest.main()
