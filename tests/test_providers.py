"""Provider isolation through the real registry and shared metered gateway."""
import json
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from studio.api import ModelService
from studio.costs import BudgetExceeded
from studio.model_selection import capabilities
from studio.providers import AIProvider
from studio.registry import DEFAULTS, Registry
from studio.state import Job


def completion():
    return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = {"AI_PROVIDER": "dahl", "DAHL_API_KEY": "  dahl-secret  ",
                    "STUDIO_MODEL_SELECTION": "configured", "STUDIO_FREE_ONLY": "0",
                    "DAHL_BASE_URL": "https://inference.dahl.global/v1/",
                    "OPENROUTER_API_KEY": "openrouter-secret", "STUDIO_AUTO_MODEL_FALLBACK": "1"}
        settings.update({f"DAHL_MODEL_{role.upper()}": f"dahl/{role},dahl/backup"
                         for role in ("supervisor", "designer", "developer", "art", "qa")})
        self.env = patch.dict(os.environ, settings)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.registry = Registry()
        self.job = SimpleNamespace(data={"costs": [], "cap_usd": 2},
                                   save=Mock(), event=Mock(), check_cancelled=Mock())
        self.service = ModelService(self.registry, self.job)
        self.service.unity_context = AsyncMock(return_value={})
        self.requests = []
        self.original_client = httpx.AsyncClient

    def transport(self, handler):
        def handle(request):
            self.requests.append(request)
            return handler(request)
        return patch("studio.api.httpx.AsyncClient", side_effect=lambda **kw:
                     self.original_client(transport=httpx.MockTransport(handle)))

    def populate(self):
        self.registry.models = {mid: {"pricing": {"prompt": "0.00000002", "completion": "0.00000002"},
                                      "supported_parameters": ["response_format", "reasoning"],
                                      "architecture": {"modality":"text->text","input_modalities":["text"],
                                                       "output_modalities":["text"]},
                                      "context_length": 32768}
                                for mids in self.registry.roles.values() for mid in mids}

    def test_default_provider_preserves_openrouter_model_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = Registry()
        self.assertEqual(registry.provider.name, "openrouter")
        self.assertEqual(registry.provider.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(registry.roles, DEFAULTS)

    def test_invalid_provider_is_rejected(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "unknown"}):
            with self.assertRaisesRegex(ValueError, "AI_PROVIDER"):
                Registry()

    def test_five_agent_models_and_owned_task_profiles_are_configurable(self):
        for role in ("supervisor", "designer", "developer", "art", "qa"):
            self.assertEqual(self.registry.roles[role], [f"dahl/{role}", "dahl/backup"])
        self.assertEqual(self.registry.roles["ui"], self.registry.roles["designer"])
        self.assertEqual(self.registry.roles["scene"], self.registry.roles["developer"])
        with patch.dict(os.environ, {}, clear=True):
            os.environ["AI_PROVIDER"] = "dahl"
            self.assertEqual(Registry().roles["developer"], ["MiniMaxAI/MiniMax-M2.7"])
            self.assertEqual(AIProvider().base_url, "https://inference.dahl.global/v1")

    async def test_missing_selected_key_never_uses_other_provider_key(self):
        for provider, key in (("dahl", "DAHL_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")):
            with self.subTest(provider=provider), patch.dict(os.environ, {"AI_PROVIDER": provider, key: " "}), \
                    patch("studio.api.httpx.AsyncClient") as client:
                registry = Registry()
                with self.assertRaisesRegex(RuntimeError, key):
                    await registry.refresh()
                with self.assertRaisesRegex(RuntimeError, key):
                    await ModelService(registry, self.job).post("chat/completions", {}, "designer", "model", .01)
                client.assert_not_called()
        self.assertFalse(self.job.data["costs"])

    async def test_dahl_catalog_uses_only_dahl_url_key_models_and_cache(self):
        def handle(request):
            self.assertEqual(str(request.url), "https://inference.dahl.global/v1/models")
            self.assertEqual(request.headers["Authorization"], "Bearer dahl-secret")
            return httpx.Response(200, json={"data": [{"id": "dahl/designer"}, {"id": "dahl/backup"}]})
        with self.transport(handle), patch("studio.registry.write_json") as save:
            await self.registry.refresh()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(list(self.registry.candidates("designer")), ["dahl/designer", "dahl/backup"])
        self.assertEqual(self.registry.rates("dahl/designer"), (2e-8, 2e-8))
        self.assertIsNone(capabilities(self.registry.models["dahl/designer"])["json"])
        self.assertTrue(self.registry.verified_at)
        self.assertEqual(save.call_args.args[0].name, "models_dahl.json")
        self.assertNotIn("secret", str(save.call_args))
        self.assertEqual(list(self.registry.candidates("art")), [])
        self.assertEqual(self.registry.images, {})

    async def test_dahl_catalog_401_clears_stale_models_and_does_not_save(self):
        self.populate()
        with self.transport(lambda request: httpx.Response(401)), patch("studio.registry.write_json") as save:
            with self.assertRaisesRegex(RuntimeError, "Dahl authentication failed.*DAHL_API_KEY"):
                await self.registry.refresh()
        self.assertEqual(self.registry.models, {})
        self.assertIsNone(self.registry.verified_at)
        save.assert_not_called()

    async def test_openrouter_discovery_and_free_fallback_ignore_dahl_settings(self):
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter"}):
            registry = Registry()
        registry.roles = {"designer": ["router/paid", "router/other", "router/free:free"], "art": []}
        models = [{"id": mid, "pricing": {"prompt": "0", "completion": "0"},
                   "supported_parameters": ["response_format"], "context_length": 32768,
                   "architecture": {"modality":"text->text","input_modalities":["text"],
                                    "output_modalities":["text"]}}
                  for mid in registry.roles["designer"]]
        for item in models[:2]:
            item["pricing"]["completion"] = ".000001"

        def handle(request):
            self.assertEqual(request.url.host, "openrouter.ai")
            self.assertEqual(request.headers["Authorization"], "Bearer openrouter-secret")
            if request.url.path == "/api/v1/key":
                return httpx.Response(200, json={"data": {"is_management_key": False}})
            if request.url.path == "/api/v1/models/user":
                return httpx.Response(200, json={"data": models})
            if request.url.path in ("/api/v1/credits", "/api/v1/images/models"):
                return httpx.Response(403)
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            payload = json.loads(request.content)
            self.assertEqual(payload["provider"], {"require_parameters": True,
                "max_price": {"prompt": 0, "completion": 1 if payload["model"] == "router/paid" else 0}})
            if payload["model"] == "router/paid":
                return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})
            self.assertEqual(payload["model"], "router/free:free")
            return completion()

        with tempfile.TemporaryDirectory() as temp, self.transport(handle), patch("studio.registry.write_json") as save:
            self.job = Job.create("OpenRouter isolation test", 2, root=Path(temp))
            await registry.refresh()
            service = ModelService(registry, self.job)
            service.unity_context = AsyncMock(return_value={})
            self.assertEqual(await service.json("designer", "JSON", {}, 100), {"ok": True})
        self.assertEqual(len([r for r in self.requests if r.method == "POST"]), 2)
        self.assertTrue(all(r.url.host == "openrouter.ai" for r in self.requests))
        self.assertIn("models.json", [call.args[0].name for call in save.call_args_list])

    async def test_all_text_tasks_use_dahl_without_openrouter_request_parameters(self):
        self.populate()
        # Selection stays attached to the registry, even if the environment changes.
        with patch.dict(os.environ, {"AI_PROVIDER": "openrouter"}), self.transport(lambda request: completion()):
            for role in ("supervisor", "designer", "developer", "qa", "ui", "scene"):
                self.assertEqual(await self.service.json(role, "JSON", {}, 100), {"ok": True})
        expected = ["supervisor", "designer", "developer", "qa", "designer", "developer"]
        self.assertEqual([json.loads(r.content)["model"] for r in self.requests], ["dahl/" + r for r in expected])
        for request in self.requests:
            self.assertEqual(str(request.url), "https://inference.dahl.global/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer dahl-secret")
            payload = json.loads(request.content)
            self.assertNotIn("provider", payload)
            self.assertNotIn("reasoning", payload)
            self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertTrue(all(e["basis"].startswith("conservative estimate") for e in self.job.data["costs"]))

    async def test_dahl_retry_and_fallback_never_leave_dahl(self):
        self.populate()
        with patch.dict(os.environ, {"STUDIO_AUTO_MODEL_FALLBACK": "0"}):
            replies = [httpx.Response(503, json={"error": {"message": "busy"}}),
                       httpx.Response(200, json={"choices": []}), completion()]
            with self.transport(lambda request: replies.pop(0)), patch("studio.api.asyncio.sleep", new_callable=AsyncMock):
                self.assertEqual(await self.service.json("designer", "JSON", {}, 100), {"ok": True})
        self.assertEqual([json.loads(r.content)["model"] for r in self.requests],
                         ["dahl/designer", "dahl/designer", "dahl/backup"])
        self.assertTrue(all(r.url.host == "inference.dahl.global" and
                            r.headers["Authorization"] == "Bearer dahl-secret" for r in self.requests))
        self.assertEqual(len(self.job.data["costs"]), 3)

    async def test_dahl_auto_fallback_is_bounded_and_can_be_disabled(self):
        self.populate()
        for n in range(5):
            self.registry.models[f"dahl/discovered{n}"] = self.registry.models["dahl/backup"]
        self.assertEqual(len(list(self.registry.candidates("designer"))), 5)
        with patch.dict(os.environ, {"STUDIO_AUTO_MODEL_FALLBACK": "0"}):
            self.assertEqual(list(self.registry.candidates("designer")), ["dahl/designer", "dahl/backup"])

    async def test_dahl_402_does_not_use_openrouter_free_or_token_retry_logic(self):
        self.populate()
        self.registry.models["dahl/backup:free"] = self.registry.models["dahl/backup"]
        self.registry.roles["designer"].append("dahl/backup:free")
        with self.transport(lambda request: httpx.Response(402, json={"error": {
                "message": "tokens exhausted; can only afford 1000"}})):
            with self.assertRaisesRegex(RuntimeError, "Dahl error 402"):
                await self.service.json("designer", "JSON", {}, 5000)
        self.assertEqual(len(self.requests), 1)

    async def test_dahl_errors_redact_both_keys_and_auth_errors_stop(self):
        self.populate()
        for status in (401, 403):
            with self.subTest(status=status), self.transport(lambda request: httpx.Response(status, json={
                    "error": {"message": "dahl-secret openrouter-secret denied"}})):
                with self.assertRaises(RuntimeError) as failure:
                    await self.service.json("designer", "JSON", {}, 100)
                self.assertNotIn("dahl-secret", str(failure.exception))
                self.assertNotIn("openrouter-secret", str(failure.exception))
                self.assertIn("Dahl", str(failure.exception))
        self.assertEqual(len(self.requests), 2)

    async def test_dahl_custom_base_url_applies_to_discovery_and_inference(self):
        with patch.dict(os.environ, {"DAHL_BASE_URL": "https://custom.example/v1/"}):
            registry = Registry()
        with self.transport(lambda request: httpx.Response(200, json={"data": [{"id": "dahl/designer"}]})), \
                patch("studio.registry.write_json"):
            await registry.refresh()
        service = ModelService(registry, self.job)
        service.unity_context = AsyncMock(return_value={})
        with self.transport(lambda request: completion()):
            await service.json("designer", "JSON", {}, 100)
        self.assertEqual([str(r.url) for r in self.requests],
                         ["https://custom.example/v1/models", "https://custom.example/v1/chat/completions"])

    async def test_dahl_budget_blocks_network(self):
        self.populate()
        self.job.data["cap_usd"] = 0
        with self.transport(lambda request: completion()):
            with self.assertRaises(BudgetExceeded):
                await self.service.json("designer", "JSON", {}, 100)
        self.assertEqual(self.requests, [])

    async def test_dahl_truncated_reasoning_retries_once_with_larger_reserved_output(self):
        self.populate()
        replies=[httpx.Response(200,json={"choices":[{"finish_reason":"length","message":{"content":None}}]}),completion()]
        with self.transport(lambda request:replies.pop(0)):
            self.assertEqual(await self.service.json("developer","JSON",{},5000),{"ok":True})
        self.assertEqual([json.loads(r.content)["max_tokens"] for r in self.requests],[5000,10000])
        self.assertEqual(len(self.job.data["costs"]),2)
        self.assertGreater(self.job.data["costs"][1]["reserved_usd"],self.job.data["costs"][0]["reserved_usd"])

    async def test_cancelled_http_request_retains_an_unknown_charge_reservation(self):
        def cancelled(request):raise asyncio.CancelledError()
        with self.transport(cancelled),self.assertRaises(asyncio.CancelledError):
            await self.service.post("chat/completions",{},"developer","dahl/developer",.001)
        self.assertEqual(self.job.data["costs"][0]["status"],"failed_or_unknown_charge")
        self.assertEqual(self.job.data["costs"][0]["charged_usd"],.001)


if __name__ == "__main__":
    unittest.main()
