import json
import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from studio.api import (ModelAttemptsFailed, ModelService, RequestDeadlineExceeded, ResponseInvalid,
                        ServiceRejected, decode_json_response)
from studio.costs import BudgetExceeded
from studio.registry import Registry
from studio.state import Job


def completion(content='{"ok":true}', finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}], "usage": {"cost": 0}}


class ResponseTests(unittest.TestCase):
    def test_null_reasoning_only_truncated_and_invalid_json_are_explicit_errors(self):
        for content, finish in [(None, "stop"), (None, "length"), ('{"ok":', 'stop'), ('[]', 'stop')]:
            with self.subTest(content=content, finish=finish), self.assertRaises(ResponseInvalid):
                decode_json_response(completion(content, finish))

    def test_text_blocks_and_markdown_fences_are_supported(self):
        for content in ['```json\n{"ok":true}\n```', [{"type": "text", "text": '{"ok":true}'}]]:
            self.assertEqual(decode_json_response(completion(content)), {"ok": True})


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "STUDIO_FREE_ONLY": "0"})
        settings.start(); self.addCleanup(settings.stop)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.job = Job.create("test", .1, root=Path(self.temp.name))
        registry = Registry()
        registry.roles = {"designer": ["one", "two"]}
        registry.models = {m: {"pricing": {"prompt": "0", "completion": "0"},
                              "supported_parameters": ["response_format", "reasoning"],
                              "architecture": {"modality":"text->text","input_modalities":["text"],
                                               "output_modalities":["text"]}} for m in ("one", "two")}
        self.service = ModelService(registry, self.job)
        self.requests = []

    async def run_replies(self, replies, role="designer"):
        original = httpx.AsyncClient
        def handle(request):
            self.requests.append(json.loads(request.content))
            return replies.pop(0)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret"}), patch(
                "studio.api.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handle))), patch(
                "studio.api.asyncio.sleep", new_callable=AsyncMock):
            return await self.service.json(role, "JSON", {}, 100)

    async def test_catalog_parameters_control_sampling_and_output_token_field(self):
        self.service.registry.models["one"]["supported_parameters"] = ["response_format", "max_completion_tokens"]
        result = await self.run_replies([httpx.Response(200, json=completion())])
        self.assertEqual(result, {"ok": True})
        self.assertNotIn("temperature", self.requests[0])
        self.assertNotIn("max_tokens", self.requests[0])
        self.assertEqual(self.requests[0]["max_completion_tokens"], 100)

    async def test_coding_roles_can_use_catalog_advertised_low_reasoning(self):
        self.service.registry.roles = {"developer": ["one"]}
        self.service.registry.models["one"]["reasoning"] = {"supported_efforts": ["none", "low", "high"]}
        result = await self.run_replies([httpx.Response(200, json=completion())], role="developer")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.requests[0]["reasoning"], {"effort": "low"})

    async def test_429_retries_with_new_reservation_and_preserves_price_limits(self):
        result = await self.run_replies([httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "1"}),
                                         httpx.Response(200, json=completion())])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(self.job.data["costs"]), 2)
        self.assertEqual(self.job.data["costs"][0]["status"], "failed_or_unknown_charge")
        self.assertEqual(self.requests[0]["model"], self.requests[1]["model"])
        self.assertEqual(self.requests[0]["provider"]["max_price"], {"prompt": 0, "completion": 0})
        self.assertEqual(self.requests[0]["reasoning"], {"enabled": False})

    async def test_mandatory_reasoning_retries_same_openrouter_model_enabled(self):
        result = await self.run_replies([
            httpx.Response(400, json={"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}}),
            httpx.Response(200, json=completion())])
        self.assertEqual(result, {"ok": True})
        self.assertEqual([request["model"] for request in self.requests], ["one", "one"])
        self.assertEqual([request["reasoning"] for request in self.requests],
                         [{"enabled": False}, {"enabled": True}])
        self.assertEqual(len(self.job.data["costs"]), 2)

    async def test_null_answer_and_non_json_envelope_fall_back(self):
        for response in [httpx.Response(200, json=completion(None)), httpx.Response(200, text="not JSON")]:
            self.requests.clear()
            result = await self.run_replies([response, httpx.Response(200, json=completion())])
            self.assertEqual(result, {"ok": True})
            self.assertEqual([r["model"] for r in self.requests], ["one", "two"])

    async def test_openrouter_truncated_output_retries_same_model_with_larger_reservation(self):
        result = await self.run_replies([
            httpx.Response(200, json=completion('{"incomplete":', finish="length")),
            httpx.Response(200, json=completion())])
        self.assertEqual(result, {"ok": True})
        self.assertEqual([r["model"] for r in self.requests], ["one", "one"])
        self.assertEqual([r["max_tokens"] for r in self.requests], [100, 200])
        self.assertEqual(len(self.job.data["costs"]), 2)

    async def test_total_request_deadline_keeps_the_unknown_charge_reserved(self):
        original = httpx.AsyncClient
        async def delayed_response(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json=completion())
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret"}), patch(
                "studio.api.REQUEST_TIMEOUT_SECONDS", .01), patch(
                "studio.api.httpx.AsyncClient", side_effect=lambda **kw:
                    original(transport=httpx.MockTransport(delayed_response))):
            with self.assertRaisesRegex(RequestDeadlineExceeded, "total deadline"):
                await self.service.post("chat/completions", {}, "designer", "one", .01)
        self.assertEqual(self.job.data["costs"][0]["status"], "failed_or_unknown_charge")
        self.assertEqual(self.job.data["costs"][0]["charged_usd"], .01)

    async def test_paid_only_policy_never_uses_free_credit_fallback(self):
        registry = self.service.registry
        registry.models["one"]["pricing"] = {"prompt": ".000001", "completion": ".000001"}
        registry.models["two:free"] = registry.models.pop("two")
        registry.roles = {"designer": ["one", "two:free"]}
        with patch.dict(os.environ, {"STUDIO_PAID_ONLY": "1", "STUDIO_ALLOW_PAID_MODELS": "1"}):
            with self.assertRaises(ModelAttemptsFailed):
                await self.run_replies([httpx.Response(402, json={"error": {"message": "Insufficient credits"}})])
        self.assertEqual([request["model"] for request in self.requests], ["one"])

    async def test_auth_error_inside_http_200_stops_and_redacts_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "403") as failure:
            await self.run_replies([httpx.Response(200, json={"error": {"code": 403, "message": "test-secret denied"}})])
        self.assertNotIn("test-secret", str(failure.exception))
        self.assertEqual(len(self.requests), 1)

    async def test_401_explains_key_problem_without_trying_other_models(self):
        with self.assertRaisesRegex(RuntimeError,"authentication failed.*401.*Changing models") as error:
            await self.run_replies([httpx.Response(401,json={"error":{"message":"User not found. test-secret"}})])
        self.assertNotIn("test-secret",str(error.exception))
        self.assertEqual(len(self.requests),1)

    async def test_model_access_error_switches_to_next_eligible_model(self):
        result = await self.run_replies([
            httpx.Response(403, json={"error": {"message": "model access denied by policy"}}),
            httpx.Response(200, json=completion())])
        self.assertEqual(result, {"ok": True})
        self.assertEqual([request["model"] for request in self.requests], ["one", "two"])
        events = [json.loads(line) for line in (self.job.directory / "events.jsonl").read_text().splitlines()]
        fallback = next(event for event in events if event["stage"] == "model_fallback")
        self.assertEqual((fallback["from_model"], fallback["to_model"]), ("one", "two"))
        self.assertIn("model access denied", fallback["reason"])

    async def test_exhausted_model_errors_are_saved_and_user_readable(self):
        from studio.failures import failure_message, failure_report
        with self.assertRaises(ModelAttemptsFailed) as failure:
            await self.run_replies([
                httpx.Response(404, json={"error": {"message": "unknown model"}}),
                httpx.Response(403, json={"error": {"message": "not permitted"}})])
        failure_report(failure.exception, self.job)
        report = self.job.data["failure_report"]
        message = failure_message(report)
        self.assertEqual([attempt["status"] for attempt in report["provider_attempts"]], [404, 403])
        self.assertIn("unknown model", message)
        self.assertIn("not permitted", message)
        self.assertIn("before Unity", report["needed"])

    async def test_credit_rejection_falls_back_only_to_available_free_model(self):
        registry=self.service.registry
        registry.models["free:free"]=registry.models["one"]
        registry.roles["designer"].append("free:free")
        result=await self.run_replies([httpx.Response(402,json={"error":{"message":"Insufficient credits"}}),
                                      httpx.Response(200,json=completion())])
        self.assertEqual(result,{"ok":True})
        self.assertEqual([r["model"] for r in self.requests],["one","free:free"])

    async def test_budget_prevents_retry_network_call(self):
        self.job.data["cap_usd"] = .0001
        self.job.save()
        with self.assertRaises(BudgetExceeded):
            await self.run_replies([httpx.Response(429, json={"error": {"message": "busy"}})])
        self.assertEqual(len(self.requests), 1)

    async def test_credit_restriction_persists_for_later_agent_calls(self):
        registry=self.service.registry
        registry.models["free:free"]=registry.models["one"]
        registry.roles["designer"].append("free:free")
        await self.run_replies([httpx.Response(402,json={"error":{"message":"Insufficient credits"}}),
                                httpx.Response(200,json=completion())])
        self.assertTrue(self.job.data["openrouter_free_only"])
        self.service=ModelService(registry,self.job)
        await self.run_replies([httpx.Response(200,json=completion())])
        self.assertEqual([r["model"] for r in self.requests],["one","free:free","free:free"])

    async def test_invalid_mcp_configuration_does_not_block_agent_request(self):
        (self.job.directory / "UnityProject").mkdir()
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "1", "STUDIO_UNITY_MCP_ARGS_JSON": "bad"}):
            self.assertEqual(await self.run_replies([httpx.Response(200, json=completion())]), {"ok": True})
        self.assertTrue(self.service._mcp_unavailable)

    async def test_no_eligible_model_has_actionable_error_and_no_network(self):
        self.service.registry.models = {}
        with self.assertRaisesRegex(RuntimeError, "No text-only JSON model"):
            await self.run_replies([])
        self.assertFalse(self.requests)


class AffordableOutputRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = patch.dict(os.environ, {"AI_PROVIDER": "openrouter"})
        settings.start(); self.addCleanup(settings.stop)

    async def test_openrouter_affordability_402_retries_once_with_lower_output_limit(self):
        class FakeJob:
            data = {"costs": [], "cap_usd": 2}
            check_cancelled = lambda self: None
            event = lambda self, *args, **kwargs: None

        class FakeRegistry:
            models = {"qwen/qwen3-coder-flash": {"supported_parameters": []}}
            def candidates(self, role, *args): return ["qwen/qwen3-coder-flash"]
            def rates(self, model): return (0.000001, 0.000005)
            def text_ceiling(self, model, messages, max_tokens): return max_tokens * 0.000005

        service = ModelService(FakeRegistry(), FakeJob())
        service.unity_context = AsyncMock(return_value={})
        requests = []

        async def post(route, payload, role, model, ceiling):
            requests.append((payload, ceiling))
            if len(requests) == 1:
                raise ServiceRejected(402,
                    "This request requires more credits, or fewer max_tokens. "
                    "You requested up to 16000 tokens, but can only afford 15237.")
            return completion()

        service.post = AsyncMock(side_effect=post)
        result = await service.json("developer", "JSON", {}, 16000)

        self.assertEqual(result, {"ok": True})
        self.assertEqual([request[0]["max_tokens"] for request in requests], [16000, 15085])
        self.assertLess(requests[1][1], requests[0][1])
        self.assertEqual(service.post.await_count, 2)

    async def test_other_402_errors_do_not_trigger_token_retry(self):
        class FakeJob:
            data = {"costs": [], "cap_usd": 2}
            check_cancelled = lambda self: None
            event = lambda self, *args, **kwargs: None

        class FakeRegistry:
            models = {"model": {"supported_parameters": []}}
            def candidates(self, role, *args): return ["model"]
            def rates(self, model): return (0.000001, 0.000005)
            def text_ceiling(self, model, messages, max_tokens): return max_tokens * 0.000005

        service = ModelService(FakeRegistry(), FakeJob())
        service.unity_context = AsyncMock(return_value={})
        service.post = AsyncMock(side_effect=ServiceRejected(402, "Insufficient credits"))
        with self.assertRaisesRegex(RuntimeError, "Insufficient credits"):
            await service.json("developer", "JSON", {}, 16000)
        service.post.assert_awaited_once()

    async def test_transient_429_retry_keeps_the_original_output_limit(self):
        class FakeJob:
            data = {"costs": [], "cap_usd": 2}
            check_cancelled = lambda self: None
            event = lambda self, *args, **kwargs: None

        class FakeRegistry:
            models = {"model": {"supported_parameters": []}}
            def candidates(self, role, *args): return ["model"]
            def rates(self, model): return (0.000001, 0.000005)
            def text_ceiling(self, model, messages, max_tokens): return max_tokens * 0.000005

        service = ModelService(FakeRegistry(), FakeJob())
        service.unity_context = AsyncMock(return_value={})
        requests = []

        async def post(route, payload, role, model, ceiling):
            requests.append(payload)
            if len(requests) == 1:
                raise ServiceRejected(429, "slow down", retry_after=0)
            return completion()

        service.post = AsyncMock(side_effect=post)
        with patch("studio.api.asyncio.sleep", new_callable=AsyncMock):
            self.assertEqual(await service.json("developer", "JSON", {}, 16000), {"ok": True})
        self.assertEqual([request["max_tokens"] for request in requests], [16000, 16000])
