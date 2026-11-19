import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from studio.api import ModelService, ResponseInvalid, ServiceRejected, decode_json_response
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
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.job = Job.create("test", .1, root=Path(self.temp.name))
        registry = Registry()
        registry.roles = {"designer": ["one", "two"]}
        registry.models = {m: {"pricing": {"prompt": "0", "completion": "0"},
                              "supported_parameters": ["response_format", "reasoning"]} for m in ("one", "two")}
        self.service = ModelService(registry, self.job)
        self.requests = []

    async def run_replies(self, replies):
        original = httpx.AsyncClient
        def handle(request):
            self.requests.append(json.loads(request.content))
            return replies.pop(0)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret"}), patch(
                "studio.api.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handle))), patch(
                "studio.api.asyncio.sleep", new_callable=AsyncMock):
            return await self.service.json("designer", "JSON", {}, 100)

    async def test_429_retries_with_new_reservation_and_preserves_price_limits(self):
        result = await self.run_replies([httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "1"}),
                                         httpx.Response(200, json=completion())])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(self.job.data["costs"]), 2)
        self.assertEqual(self.job.data["costs"][0]["status"], "failed_or_unknown_charge")
        self.assertEqual(self.requests[0]["model"], self.requests[1]["model"])
        self.assertEqual(self.requests[0]["provider"]["max_price"], {"prompt": 0, "completion": 0})
        self.assertEqual(self.requests[0]["reasoning"], {"enabled": False})

    async def test_null_answer_and_non_json_envelope_fall_back(self):
        for response in [httpx.Response(200, json=completion(None)), httpx.Response(200, text="not JSON")]:
            self.requests.clear()
            result = await self.run_replies([response, httpx.Response(200, json=completion())])
            self.assertEqual(result, {"ok": True})
            self.assertEqual([r["model"] for r in self.requests], ["one", "two"])

    async def test_auth_error_inside_http_200_stops_and_redacts_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "403") as failure:
            await self.run_replies([httpx.Response(200, json={"error": {"code": 403, "message": "test-secret denied"}})])
        self.assertNotIn("test-secret", str(failure.exception))
        self.assertEqual(len(self.requests), 1)

    async def test_budget_prevents_retry_network_call(self):
        self.job.data["cap_usd"] = .0001
        self.job.save()
        with self.assertRaises(BudgetExceeded):
            await self.run_replies([httpx.Response(429, json={"error": {"message": "busy"}})])
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_mcp_configuration_does_not_block_agent_request(self):
        (self.job.directory / "UnityProject").mkdir()
        with patch.dict(os.environ, {"STUDIO_UNITY_MCP_ENABLED": "1", "STUDIO_UNITY_MCP_ARGS_JSON": "bad"}):
            self.assertEqual(await self.run_replies([httpx.Response(200, json=completion())]), {"ok": True})
        self.assertTrue(self.service._mcp_unavailable)

    async def test_no_eligible_model_has_actionable_error_and_no_network(self):
        self.service.registry.models = {}
        with self.assertRaisesRegex(RuntimeError, "No configured designer model"):
            await self.run_replies([])
        self.assertFalse(self.requests)


class AffordableOutputRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_openrouter_affordability_402_retries_once_with_lower_output_limit(self):
        class FakeJob:
            data = {"costs": [], "cap_usd": 2}
            check_cancelled = lambda self: None
            event = lambda self, *args, **kwargs: None

        class FakeRegistry:
            models = {"qwen/qwen3-coder-flash": {"supported_parameters": []}}
            def candidates(self, role): return ["qwen/qwen3-coder-flash"]
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
            def candidates(self, role): return ["model"]
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
            def candidates(self, role): return ["model"]
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
