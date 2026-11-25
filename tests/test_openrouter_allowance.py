"""No network/inference: exercise real discovery, selection and reservation code."""
import json
import os
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from studio.api import ModelAttemptsFailed, ModelService
from studio.costs import BudgetExceeded, Ledger
from studio.openrouter import key_allowance
from studio.registry import Registry
from studio.state import Job


def model(mid, price):
    return {"id": mid, "pricing": {"prompt": "0", "completion": str(price)},
            "context_length": 128000, "supported_parameters": ["response_format", "max_tokens"],
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}


class AllowanceTests(unittest.TestCase):
    def test_minimum_of_key_period_workspace_and_account_balances(self):
        snapshot = key_allowance({"limit": 10, "limit_reset": "weekly", "usage": 500,
            "usage_weekly": 8, "effective_budget": {"scope": "workspace", "remaining_usd": 1.5}},
            {"total_credits": 50, "total_usage": 49})
        self.assertEqual(snapshot["remaining_usd"], 1)
        self.assertEqual(snapshot["key_remaining_usd"], 2)
        self.assertEqual(snapshot["account_remaining_usd"], 1)

    def test_unlimited_missing_or_invalid_limits_are_unknown_not_credits(self):
        for data in ({}, {"limit": None, "usage": 40}, {"limit_remaining": "NaN"},
                     {"limit_remaining": "Infinity"}):
            self.assertIsNone(key_allowance(data)["remaining_usd"])
        self.assertEqual(key_allowance({"limit_remaining": 0})["remaining_usd"], 0)
        self.assertEqual(key_allowance({}, {"total_credits": 1, "total_usage": 2})["remaining_usd"], 0)

    def test_pending_settled_and_unknown_reservations_across_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = dict(provider="openrouter", identity="key", scope="account", observed_at=time.time(),
                            remaining_usd=1, key_remaining_usd=1, account_remaining_usd=1, status="reported_bound")
            a, b = Ledger(Job.create("a", 10, root=root)), Ledger(Job.create("b", 10, root=root))
            a.bind_allowance(snapshot); b.bind_allowance(snapshot)
            pending = a.reserve("developer", "model", .7)
            self.assertEqual(b.remaining, Decimal("0.3"))
            with self.assertRaises(BudgetExceeded):
                b.reserve("developer", "model", .4)
            a.settle(pending, {"cost": .2})
            self.assertEqual(b.remaining, Decimal("0.8"))
            # A fresh provider snapshot already includes the settled usage.
            b.bind_allowance({**snapshot, "observed_at": time.time() + 1,
                              "remaining_usd": .8, "key_remaining_usd": .8, "account_remaining_usd": .8})
            self.assertEqual(b.remaining, Decimal("0.8"))
            unknown = a.reserve("developer", "model", .3)
            unknown["status"] = "failed_or_unknown_charge"; a.job.save()
            self.assertEqual(b.remaining, Decimal("0.5"))

    def test_two_keys_share_account_credit_but_not_individual_key_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = dict(provider="openrouter", identity="a", scope="account", observed_at=time.time(),
                remaining_usd=.8, key_remaining_usd=.8, account_remaining_usd=1, status="reported_bound")
            a, b = [Ledger(Job.create(name, 10, root=root)) for name in ("a", "b")]
            a.bind_allowance(snapshot); b.bind_allowance({**snapshot, "identity": "b"})
            a.reserve("developer", "model", .6)
            self.assertEqual(b.remaining, Decimal("0.4"))

    def test_downstream_budget_is_reserved_but_free_calls_are_possible(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Job.create("game", 10, root=Path(temp)); job.data["tasks"] = [{"name": "sprites"}]
            ledger = Ledger(job)
            ledger.bind_allowance(dict(provider="openrouter", identity="key", scope="account",
                observed_at=time.time(), remaining_usd=1, key_remaining_usd=1, account_remaining_usd=None,
                status="reported_bound"))
            self.assertEqual(job.data["effective_cap_usd"], 1)
            self.assertEqual(ledger.request_budget("art"), Decimal("0.4"))
            with self.assertRaises(BudgetExceeded): ledger.reserve("art", "image", .5)
            ledger.reserve("art", "image", .3)
            self.assertGreater(ledger.request_budget("developer"), ledger.request_budget("art"))

    def test_new_env_maximum_limits_resumed_job_without_erasing_spend(self):
        with tempfile.TemporaryDirectory() as temp, patch("studio.config.MAX_JOB_CAP", .25):
            job = Job.create("saved game", 10, root=Path(temp))
            job.data["costs"] = [{"charged_usd": .1}]
            ledger = Ledger(job)
            ledger.bind_allowance(dict(provider="openrouter", identity="key", scope="account",
                observed_at=time.time(), remaining_usd=5, key_remaining_usd=5, account_remaining_usd=None,
                status="reported_bound"))
            self.assertEqual(ledger.remaining, Decimal("0.15"))
            self.assertEqual(job.data["effective_cap_usd"], .25)
            self.assertEqual(job.data["cap_usd"], 10)
            self.assertFalse(ledger.can_reserve(.2))


class AdaptiveServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "mock-key",
            "STUDIO_FREE_ONLY": "0", "STUDIO_MODEL_SELECTION": "configured",
            "STUDIO_MODEL_FALLBACK_SCOPE": "configured", "STUDIO_ALLOW_PAID_MODELS": "0"})
        settings.start(); self.addCleanup(settings.stop)
        cache = patch("studio.registry.CACHE", self.root / "cache"); cache.start(); self.addCleanup(cache.stop)
        self.balance = 5
        self.credits = {"total_credits": 20, "total_usage": 10}
        self.models = [model("preferred", .00001), model("cheap", .0000001), model("free:free", 0)]
        self.replies, self.posts, self.gets = [], [], []
        original = httpx.AsyncClient
        transport = httpx.MockTransport(self.handle)
        clients = patch("studio.api.httpx.AsyncClient", side_effect=lambda **kw: original(transport=transport))
        clients.start(); self.addCleanup(clients.stop)
        delay = patch("studio.api.asyncio.sleep", new_callable=AsyncMock); delay.start(); self.addCleanup(delay.stop)
        self.registry = Registry(); self.registry.roles = {"designer": ["preferred"], "developer": ["preferred"], "art": []}
        self.job = Job.create("mock game", 10, root=self.root)

    def handle(self, request):
        if request.method == "GET":
            self.gets.append(request.url.path)
            if request.url.path.endswith("/key"):
                return httpx.Response(200, json={"data": {"limit_remaining": self.balance}})
            if request.url.path.endswith("/credits"):
                return httpx.Response(403, json={"error": {"message": "Management key required"}}) if self.credits is None else httpx.Response(200, json={"data": self.credits})
            if request.url.path.endswith("/models/user"):
                return httpx.Response(200, json={"data": self.models})
            return httpx.Response(404)
        self.posts.append(json.loads(request.content))
        if self.replies: return self.replies.pop(0)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}], "usage": {"cost": 0}})

    async def service(self):
        await self.registry.refresh()
        service = ModelService(self.registry, self.job)
        service.unity_context = AsyncMock(return_value={})
        return service

    async def test_zero_credit_key_can_use_free_model_with_zero_reservation(self):
        self.balance = 0
        service = await self.service()
        self.assertEqual(await service.json("developer", "json", {}, 8000), {"ok": True})
        self.assertEqual(self.posts[0]["model"], "free:free")
        self.assertEqual(self.job.data["effective_cap_usd"], 0)

    async def test_low_credit_selects_discovered_affordable_coder_outside_preferences(self):
        self.balance = .002
        service = await self.service()
        await service.json("developer", "json", {}, 16000)
        self.assertEqual(self.posts[0]["model"], "cheap")
        self.assertLessEqual(self.job.data["costs"][0]["reserved_usd"], .002)

    async def test_output_is_adapted_and_funds_remain_for_coding_and_repairs(self):
        self.balance = .04
        self.job.data["tasks"] = [{"name": "scripts", "status": "pending"}]
        service = await self.service()
        await service.json("designer", "json", {}, 5000)
        self.assertEqual(self.posts[0]["model"], "preferred")
        self.assertLess(self.posts[0]["max_tokens"], 5000)
        self.assertLessEqual(self.job.data["costs"][0]["reserved_usd"], .016)

    async def test_premium_key_uses_preferred_model_under_configured_cap(self):
        self.balance = 50
        service = await self.service()
        await service.json("developer", "json", {}, 16000)
        self.assertEqual(self.posts[0]["model"], "preferred")
        self.assertEqual(self.posts[0]["max_tokens"], 16000)
        self.assertEqual(self.job.data["effective_cap_usd"], 10)

    async def test_unavailable_account_credit_endpoint_is_optional_and_unknown_is_explicit(self):
        self.balance = None; self.credits = None
        service = await self.service()
        await service.json("designer", "json", {}, 2000)
        self.assertEqual(self.job.data["provider_budget"]["status"], "unknown")
        self.assertIsNone(self.job.data["provider_budget"]["remaining_usd"])

    async def test_allowance_drop_before_next_call_reselects_without_paid_attempt(self):
        service = await self.service()
        await service.json("developer", "json", {}, 8000)
        self.balance = 0; service._allowance_checked = 0
        await service.json("developer", "json", {}, 8000)
        self.assertEqual([p["model"] for p in self.posts], ["preferred", "free:free"])

    async def test_402_and_key_limit_403_fall_back_once_and_do_not_retry_paid_roles(self):
        self.registry.record_outcome = Mock()
        for status, message in ((402, "Insufficient credits"), (403, "API key limit exceeded (weekly limit)")):
            with self.subTest(status=status):
                self.registry.free_only = False
                self.job.data.pop("openrouter_free_only", None)
                service = await self.service(); self.posts.clear()
                self.replies = [httpx.Response(status, json={"error": {"message": message}})]
                await service.json("developer", "json", {}, 8000)
                await service.json("designer", "json", {}, 2000)
                self.assertEqual([p["model"] for p in self.posts], ["preferred", "free:free", "free:free"])
                self.assertEqual(self.job.data["costs"][-3]["charged_usd"], 0)

    async def test_unsupported_json_retries_plain_json_and_remembers_mode(self):
        service = await self.service()
        self.replies = [httpx.Response(400, json={"error": {"message": "response_format json_object unsupported"}})]
        await service.json("developer", "json", {}, 8000)
        await service.json("designer", "json", {}, 2000)
        self.assertIn("response_format", self.posts[0])
        self.assertNotIn("response_format", self.posts[1])
        self.assertNotIn("response_format", self.posts[2])

    async def test_model_restriction_or_exhausted_rate_limit_is_not_reselected(self):
        self.registry.record_outcome = Mock()
        for status in (403, 429):
            with self.subTest(status=status):
                self.registry.unavailable.clear(); self.job.data.pop("openrouter_model_blocks", None)
                service = await self.service(); self.posts.clear()
                self.replies = [httpx.Response(status, json={"error": {"message": "model access restricted"}}, headers={"Retry-After": "30"})]
                await service.json("developer", "json", {}, 8000)
                await service.json("designer", "json", {}, 2000)
                self.assertEqual(len(self.posts), 3)
                self.assertNotEqual(self.posts[0]["model"], self.posts[1]["model"])
                self.assertEqual(self.posts[1]["model"], self.posts[2]["model"])
                self.assertIn(self.posts[0]["model"], self.registry.unavailable)

    async def test_rate_limit_cooldown_expires_for_a_later_saved_job_retry(self):
        service = await self.service()
        self.registry.block_model("preferred", seconds=-1)
        await service.json("developer", "json", {}, 8000)
        self.assertEqual(self.posts[0]["model"], "preferred")

    async def test_new_key_clears_previous_credit_and_model_restrictions(self):
        service = await self.service()
        self.replies = [httpx.Response(402, json={"error": {"message": "Insufficient credits"}})]
        await service.json("developer", "json", {}, 8000)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "different-mock-key"}):
            service._allowance_checked = 0
            await service.json("developer", "json", {}, 8000)
        self.assertEqual(self.posts[-1]["model"], "preferred")
        self.assertEqual(self.gets.count("/api/v1/models/user"), 2)

    async def test_in_flight_402_is_bounded_without_marking_key_bankrupt(self):
        service = await self.service()
        self.replies = [httpx.Response(402, json={"error": {"message": "Pending requests exceed available in-flight budget",
            "metadata": {"limit_source": "openrouter_in_flight_budget"}}}) for _ in range(2)]
        with self.assertRaises(ModelAttemptsFailed):
            await service.json("developer", "json", {}, 8000)
        self.assertEqual([p["model"] for p in self.posts], ["preferred", "preferred"])
        self.assertFalse(self.registry.free_only)
        self.assertEqual(service.ledger.committed, 0)

    async def test_insufficient_credits_preserves_job_and_never_tries_other_paid_models(self):
        self.models = self.models[:2]
        service = await self.service()
        self.replies = [httpx.Response(402, json={"error": {"message": "Insufficient credits"}})]
        with self.assertRaises(ModelAttemptsFailed):
            await service.json("developer", "json", {}, 8000)
        self.assertEqual(len(self.posts), 1)
        self.assertTrue((self.job.directory / "job.json").is_file())


if __name__ == "__main__":
    unittest.main()
