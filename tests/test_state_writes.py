import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.state import write_json


class AtomicStateTests(unittest.TestCase):
    def test_transient_windows_sharing_error_retries_without_losing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "job.json"
            write_json(path, {"stage": "sprites"})
            import os
            replace = os.replace
            locked = PermissionError("sharing violation"); locked.winerror = 32
            calls = []
            def attempt(source, target):
                calls.append(source)
                if len(calls) == 1:
                    self.assertEqual(json.loads(path.read_text()), {"stage": "sprites"})
                    raise locked
                replace(source, target)
            with patch("studio.state.os.replace", side_effect=attempt), patch("studio.state.time.sleep"):
                write_json(path, {"stage": "assembly"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "assembly"})
            self.assertEqual(len(calls), 2)
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_persistent_sharing_failure_is_bounded_and_preserves_old_job(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "job.json"; write_json(path, {"saved": True})
            locked = PermissionError("sharing violation"); locked.winerror = 5
            with patch("studio.state.os.replace", side_effect=locked) as replace, patch("studio.state.time.sleep"):
                with self.assertRaises(PermissionError): write_json(path, {"saved": False})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(json.loads(path.read_text()), {"saved": True})
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])
