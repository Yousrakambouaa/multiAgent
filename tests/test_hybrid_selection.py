"""Deterministic policy tests. Every HTTP response and persistence write is mocked."""
import copy
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from studio.api import ModelAttemptsFailed, ModelService
from studio.costs import Ledger, BudgetExceeded, task_cost_report
from studio.model_selection import benchmark_signal, compact_context, complex_request, value_ranking
from studio.registry import Registry


def model(mid, rate):
    return {"id": mid, "pricing": {"prompt": "0", "completion": str(rate)}, "context_length": 128000,
            "supported_parameters": ["response_format", "temperature", "max_tokens"],
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}


class MemoryJob:
    def __init__(self):
        self.directory = Path(".studio_cache/mock-policy-job")
        self.data = {"id": "mock-job", "cap_usd": 10, "costs": [], "tasks": [], "model_policy": "hybrid"}
        self.events = []
    def save(self): pass
    def check_cancelled(self): pass
    def event(self, role, stage, **details):
        self.events.append(dict(role=role, stage=stage, **details))


class ValueRankingTests(unittest.TestCase):
    def test_reliable_affordable_model_beats_unmeasured_free_and_premium(self):
        options = [{"model": m, "quoted_usd": p} for m, p in (("free", 0), ("qwen", .002), ("premium", .02))]
        evidence = {"qwen": {"developer": [{"kind": "unity", "passed": True}] * 8}}
        ranked = value_ranking(options, "developer", evidence, {}, ["premium"])
        self.assertEqual(ranked[0]["model"], "qwen")
        self.assertNotIn("premium", [o["model"] for o in ranked])

    def test_expensive_pool_opens_only_for_validation_or_complexity(self):
        options = [{"model": "cheap", "quoted_usd": .001}, {"model": "premium", "quoted_usd": .1}]
        for reason in ({"escalate": True}, {"complex_task": True}):
            self.assertIn("premium", [o["model"] for o in value_ranking(options, "qa", {}, {}, [], **reason)])
        self.assertNotIn("premium", [o["model"] for o in value_ranking(options, "qa", {}, {}, [])])

    def test_proven_failures_in_economical_pool_justify_escalation_without_repaying_for_them(self):
        options = [{"model": "cheap", "quoted_usd": .001}, {"model": "premium", "quoted_usd": .1}]
        evidence = {"cheap": {"developer": [{"kind": "unity", "passed": False}] * 3}}
        ranked = value_ranking(options, "developer", evidence, {}, ["cheap"])
        self.assertEqual(ranked[0]["model"], "premium")

    def test_benchmarks_are_advisory_and_local_failures_dominate(self):
        rows = [{"source": "artificial-analysis", "engineering_index": 90, "coding_index": None}]
        self.assertEqual(benchmark_signal(rows, "developer")[0], .9)
        self.assertEqual(benchmark_signal([], "developer")[0], .5)
        options = [{"model": m, "quoted_usd": .001} for m in ("great-benchmark", "validated")]
        evidence = {"great-benchmark": {"developer": [{"kind": "unity", "passed": False}] * 4},
                    "validated": {"developer": [{"kind": "unity", "passed": True}] * 4}}
        ranked = value_ranking(options, "developer", evidence, {"great-benchmark": rows}, [])
        self.assertEqual(ranked[0]["model"], "validated")

    def test_context_deduplication_keeps_all_requirements_and_does_not_mutate_inputs(self):
        requirements = [{"id": "R1", "description": "keep this exact instruction"}]
        shared = {"requirements": requirements, "dimension": "3D", "scenes": ["A"], "ownership": {"scene": "developer"}}
        value = {"game_spec": {"requirements": requirements, "dimension": "3D", "scenes": ["A"]}}
        result = compact_context(value, shared)
        self.assertNotIn("requirements", result)
        self.assertEqual(shared["requirements"], requirements)
        self.assertEqual(value["game_spec"]["requirements"], requirements)
        self.assertIn("requirements", compact_context({"previous_response": value}, shared))

    def test_complexity_uses_structural_workload_and_ignores_schema_size(self):
        self.assertFalse(complex_request({"schema": "x" * 100000, "game_spec": {"scenes": ["A"]}}))
        self.assertTrue(complex_request({"game_spec": {"scenes": ["A", "B", "C"]}}))

    def test_costs_include_failed_attempts_without_calling_json_a_success(self):
        job = MemoryJob()
        job.data["costs"] = [dict(role="ui", charged_usd=.01, basis="reported API cost"),
                             dict(role="ui", charged_usd=.02, basis="conservative reservation")]
        events = [dict(role="ui", stage="model_response_validated", task_id="one")]
        report = task_cost_report(job, events)
        self.assertIsNone(report["by_task_profile"]["ui"]["reported_cost_per_successful_task_usd"])
        events.append(dict(role="ui", stage="model_outcome", task_id="one", kind="contract", passed=True))
        report = task_cost_report(job, events)
        self.assertEqual(report["by_agent"]["designer"]["reported_usd"], .01)
        self.assertEqual(report["total_estimated_or_reserved_usd"], .02)
        self.assertFalse(report["reported_cost_is_complete"])
        self.assertIsNone(report["reported_cost_per_completed_game_usd"])

    def test_pending_reservations_across_jobs_reduce_affordable_selection(self):
        job, other = MemoryJob(), MemoryJob()
        store = {}
        def save(_path, value): store.clear(); store.update(copy.deepcopy(value))
        snapshot = dict(provider="openrouter", identity="mock", scope="account", observed_at=time.time(),
                        remaining_usd=.01, key_remaining_usd=.01, account_remaining_usd=.01, status="reported_bound")
        with patch("studio.costs.ProcessLock"), patch("studio.costs.Ledger._holds", side_effect=lambda: copy.deepcopy(store)), \
             patch("studio.costs.write_json", side_effect=save):
            a, b = Ledger(job), Ledger(other)
            a.bind_allowance(snapshot); b.bind_allowance(snapshot)
            a.reserve("developer", "one", .008)
            with self.assertRaises(BudgetExceeded): b.reserve("developer", "two", .003)
            self.assertAlmostEqual(float(b.remaining), .002)

    def test_planning_cannot_spend_the_completion_and_repair_reserve(self):
        job = MemoryJob(); job.data["tasks"] = [{"name": "scripts"}]
        snapshot = dict(provider="openrouter", identity="mock", scope="account", observed_at=time.time(),
                        remaining_usd=1, key_remaining_usd=1, account_remaining_usd=1, status="reported_bound")
        with patch("studio.costs.Ledger._holds", return_value={}), patch("studio.costs.ProcessLock"), patch("studio.costs.write_json"):
            ledger = Ledger(job); ledger.bind_allowance(snapshot)
            self.assertAlmostEqual(float(ledger.request_budget("designer")), .4)
            with self.assertRaises(BudgetExceeded): ledger.reserve("designer", "one", .41)
            self.assertEqual(job.data["costs"], [])
            self.assertAlmostEqual(float(ledger.request_budget("developer")), .8)


class HybridServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "mock-only-key", "STUDIO_FREE_ONLY": "0"})
        settings.start(); self.addCleanup(settings.stop)
        self.registry = Registry()
        self.registry.roles = {"designer": ["minimax/cheap"], "developer": ["minimax/cheap"], "qa": ["minimax/cheap"], "art": []}
        self.registry.models = {m: model(m, p) for m, p in (("minimax/cheap", .0000001),
            ("deepseek/cheap", .0000002), ("qwen/free:free", 0), ("google/premium", .00001))}
        self.registry.verified_at = "mock-catalog"
        self.registry.evidence = {"minimax/cheap": {r: [{"kind": "unity", "passed": True}] * 8 for r in ("developer", "designer", "qa")}}
        self.job = MemoryJob()
        self.service = ModelService(self.registry, self.job)
        self.service.unity_context = AsyncMock(return_value={})
        self.requests = []
        self.replies = []
        original = httpx.AsyncClient
        def handle(request):
            self.assertEqual(request.method, "POST", "No real metadata request is allowed in this fixture")
            self.requests.append(json.loads(request.content))
            if self.replies:
                result = self.replies.pop(0)
                if isinstance(result, Exception): raise result
                return result
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}], "usage": {"cost": 0}})
        clients = patch("studio.api.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handle)))
        clients.start(); self.addCleanup(clients.stop)
        sleep = patch("studio.api.asyncio.sleep", new_callable=AsyncMock); sleep.start(); self.addCleanup(sleep.stop)

    async def test_actual_hybrid_request_prefers_reliable_low_cost_and_records_reason(self):
        await self.service.json("developer", "JSON", {}, 1000)
        self.assertEqual(self.requests[0]["model"], "minimax/cheap")
        event = next(e for e in self.job.events if e["stage"] == "model_policy")
        self.assertEqual(event["reason"], "economical_first")
        self.assertEqual(self.job.data["costs"][0]["task_id"], self.service.selected_tasks["developer"])

    async def test_preferences_are_not_a_whitelist_and_budget_filters_before_selection(self):
        self.registry.roles["developer"] = ["missing/model"]
        self.job.data["cap_usd"] = .0001
        await self.service.json("developer", "JSON", {}, 1000)
        self.assertEqual(self.requests[0]["model"], "qwen/free:free")

    async def test_provider_429_switches_once_and_stays_blocked_for_next_task(self):
        self.replies = [httpx.Response(429, json={"error": {"message": "provider busy"}})]
        await self.service.json("developer", "JSON", {}, 1000)
        await self.service.json("designer", "JSON", {}, 1000)
        self.assertEqual(self.requests[0]["model"], "minimax/cheap")
        self.assertTrue(all(r["model"] != "minimax/cheap" for r in self.requests[1:]))
        self.assertEqual(len(self.requests), 3)

    async def test_account_429_stops_other_models_and_later_requests_until_retry_after(self):
        self.replies = [httpx.Response(429, json={"error": {"message": "account-wide limit", "metadata": {"limit_source": "openrouter_account"}}}, headers={"Retry-After": "30"})]
        for _ in range(2):
            with self.assertRaises(ModelAttemptsFailed): await self.service.json("developer", "JSON", {}, 1000)
        self.assertEqual(len(self.requests), 1)

    async def test_unsupported_optional_parameter_is_removed_and_remembered(self):
        self.replies = [httpx.Response(400, json={"error": {"message": "temperature is unsupported"}})]
        await self.service.json("developer", "JSON", {}, 1000)
        await self.service.json("designer", "JSON", {}, 1000)
        self.assertIn("temperature", self.requests[0])
        self.assertNotIn("temperature", self.requests[1])
        self.assertNotIn("temperature", self.requests[2])

    async def test_invalid_json_has_bounded_fallback_and_a_saved_escalation_reason(self):
        self.replies = [httpx.Response(200, json={"choices": [{"message": {"content": "bad JSON"}}], "usage": {"cost": 0}})]
        await self.service.json("developer", "JSON", {}, 1000)
        self.assertLessEqual(len(self.requests), 4)
        self.assertNotEqual(self.requests[0]["model"], self.requests[1]["model"])
        self.assertTrue(any(e["stage"] == "model_escalation" for e in self.job.events))

    async def test_timeout_keeps_unknown_charge_and_does_not_repeat_same_model(self):
        self.replies = [httpx.ReadTimeout("mock timeout")]
        await self.service.json("developer", "JSON", {}, 1000)
        self.assertEqual(self.job.data["costs"][0]["status"], "failed_or_unknown_charge")
        self.assertNotEqual(self.requests[0]["model"], self.requests[1]["model"])
        self.assertEqual(len(self.requests), 2)

    async def test_credit_rejection_is_not_treated_as_model_quality_failure(self):
        self.replies = [httpx.Response(403, json={"error": {"message": "API key limit exceeded (weekly limit)"}})]
        await self.service.json("developer", "JSON", {}, 1000)
        self.assertEqual(self.requests[-1]["model"], "qwen/free:free")
        self.assertNotIn("minimax/cheap", self.registry.unavailable)

    async def test_validation_failure_is_persisted_for_resume_and_scene_is_developer_work(self):
        await self.service.json("developer", "JSON", {}, 1000)
        self.service.record_validation("scene", False)
        restored = MemoryJob(); restored.data = json.loads(json.dumps(self.job.data))
        resumed = ModelService(self.registry, restored); resumed.unity_context = AsyncMock(return_value={})
        await resumed.json("qa", "JSON", {}, 1000)
        self.assertEqual(next(e for e in restored.events if e["stage"] == "model_policy")["reason"], "failed_validation")
        self.assertTrue(any(e["role"] == "scene" and e["model"] == "minimax/cheap" for e in self.job.events if e["stage"] == "model_outcome"))


class BenchmarkCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_daily_cache_survives_a_new_registry_without_metadata_calls(self):
        saved = {"checked_at": time.time() - 3600, "models": {"qwen/one": []}, "meta": {"citation": "Artificial Analysis"}}
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter"}):
            registry = Registry(); registry._identity = "mock"
        with patch.object(Path, "read_text", return_value=json.dumps(saved)), patch("studio.registry.httpx.AsyncClient") as client:
            await registry.refresh_benchmarks(); await registry.refresh_benchmarks()
        self.assertIn("qwen/one", registry.benchmarks)
        client.assert_not_called()

    async def test_repeated_validation_of_one_task_is_not_multiple_quality_samples(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter"}), patch("studio.registry.write_json"):
            registry = Registry(); registry._identity = "mock"
            registry.record_outcome("qwen/model", "developer", True, kind="unity", task_id="same-task")
            registry.record_outcome("qwen/model", "developer", False, kind="unity", task_id="same-task")
        records = registry.evidence["qwen/model"]["developer"]
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["passed"])

    async def test_optional_metadata_is_cached_and_failure_does_not_block(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "mock-key"}):
            registry = Registry(); registry._identity = "test-fingerprint"
            original = httpx.AsyncClient; calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(200, json={"data": [{"model_permaslug": "qwen/one", "source": "artificial-analysis", "engineering_index": 80}], "meta": {"citation": "Artificial Analysis"}})
            with patch("studio.registry.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handle))), \
                 patch.object(Path, "read_text", side_effect=FileNotFoundError), patch("studio.registry.write_json") as save:
                await registry.refresh_benchmarks(); await registry.refresh_benchmarks()
            self.assertEqual(len(calls), 1)
            self.assertIn("qwen/one", registry.benchmarks)
            self.assertEqual(save.call_args.args[1]["meta"]["citation"], "Artificial Analysis")
            registry._benchmarks_next_check = 0
            with patch("studio.registry.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(403)))):
                await registry.refresh_benchmarks()
            self.assertIn("qwen/one", registry.benchmarks)

    async def test_dahl_never_fetches_openrouter_benchmarks(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "dahl"}), patch("studio.registry.httpx.AsyncClient") as client:
            await Registry().refresh_benchmarks()
        client.assert_not_called()


class ImageValueTests(unittest.TestCase):
    def test_working_transparency_and_style_reference_can_outweigh_cheapest_endpoint(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "STUDIO_FREE_ONLY": "0"}):
            registry = Registry()
        def endpoint(cost, reference=False):
            params = {"background": {"values": ["transparent"]}, "output_format": {"values": ["png"]}}
            if reference: params["input_references"] = {}
            return {"endpoints": [{"supported_parameters": params, "pricing": [
                {"billable": "output_image", "unit": "image", "cost_usd": cost}]}]}
        registry.images = {"free": endpoint(0), "usable": endpoint(.002), "consistent": endpoint(.004, True)}
        registry.evidence = {"free": {"art": [{"kind": "usable_image", "passed": False,
            "transparency_required": True}] * 4}}
        choices = list(registry.candidates("art", transparent=True, hybrid=True))
        self.assertNotEqual(choices[0], "free")
        referenced = list(registry.candidates("art", transparent=True, hybrid=True, reference=True))
        self.assertEqual(referenced[0], "consistent")
        self.assertEqual(registry.image_endpoint("free", transparent=True)[1], 0)


if __name__ == "__main__": unittest.main()
