import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.costs import validate_budget
from studio.registry import Registry
from studio.state import Job
from studio.supervisor import Supervisor


class BudgetPolicyTests(unittest.TestCase):
    def test_budget_is_persistent_and_cannot_erase_existing_commitments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            studio = Supervisor(root)
            studio.job = Job.create("recover a game", 2, root=root)
            studio.job.data["status"] = "budget_stopped"
            studio.job.data["costs"] = [{"charged_usd": 1.5}]
            studio.set_budget(10)
            self.assertEqual(studio.job.data["cap_usd"], 10)
            self.assertEqual(studio.job.data["status"], "failed")
            self.assertEqual(Supervisor(root).default_budget(), 10)
            with self.assertRaisesRegex(ValueError, "existing charges"):
                studio.set_budget(1)
            self.assertEqual(studio.default_budget(), 10)
            studio.set_budget(1, update_current=False)
            self.assertEqual(studio.default_budget(), 1)
            self.assertEqual(studio.job.data["cap_usd"], 10)

    def test_nonfinite_negative_and_excessive_budgets_are_rejected(self):
        with patch("studio.config.MAX_JOB_CAP", 50):
            for value in ("NaN", "Infinity", -1, 0, 50.01, "bad"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    validate_budget(value)
            self.assertEqual(validate_budget("50"), 50)

    def test_paid_configured_policy_excludes_free_batch_and_unconfigured_models(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "STUDIO_ALLOW_PAID_MODELS": "1",
                "STUDIO_FREE_ONLY": "0", "STUDIO_PAID_ONLY": "1", "STUDIO_MODEL_SELECTION": "configured",
                "STUDIO_AUTO_MODEL_FALLBACK": "1", "STUDIO_MODEL_FALLBACK_SCOPE": "configured"}):
            registry = Registry()
            registry.roles = {"developer": ["paid", "paid:batch", "free:free"]}
            registry.models = {mid: {"id": mid, "context_length": 128000,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format"],
                "pricing": {"prompt": str(price), "completion": str(price)}}
                for mid, price in (("paid", .00001), ("paid:batch", .000005), ("free:free", 0), ("other", .000001))}
            self.assertEqual(list(registry.candidates("developer")), ["paid"])


if __name__ == "__main__":
    unittest.main()
