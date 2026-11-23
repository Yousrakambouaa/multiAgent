import os
import unittest
from unittest.mock import patch

import httpx

from studio.registry import Registry


def model(mid, prompt="0", completion="0", **extra):
    return {"id": mid, "pricing": {"prompt": prompt, "completion": completion},
            "supported_parameters": ["response_format"], "context_length": 65536, **extra}


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = patch.dict(os.environ, {"AI_PROVIDER": "openrouter", "STUDIO_FREE_ONLY": "0", "STUDIO_MODEL_SELECTION": "configured"})
        settings.start(); self.addCleanup(settings.stop)

    async def test_rejected_key_stops_discovery_before_any_model_requests(self):
        calls=[]
        original=httpx.AsyncClient
        def handle(request):
            calls.append(request)
            return httpx.Response(401,json={"error":{"message":"User not found"}})
        registry=Registry();registry.models={"stale":model("stale")};registry.verified_at="old"
        with patch.dict(os.environ,{"OPENROUTER_API_KEY":"test-secret"}), patch(
                "studio.registry.httpx.AsyncClient",side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))):
            with self.assertRaisesRegex(RuntimeError,"authentication failed.*401.*restart") as error:
                await registry.refresh()
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].url.path,"/api/v1/key")
        self.assertNotIn("test-secret",str(error.exception))
        self.assertEqual(registry.models,{})
        self.assertIsNone(registry.verified_at)

    async def test_discovery_uses_active_key_catalog_and_does_not_cache_key_metadata(self):
        calls=[];original=httpx.AsyncClient
        def handle(request):
            calls.append(request)
            if request.url.path=="/api/v1/key":
                return httpx.Response(200,json={"data":{"label":"private-label","limit":50}})
            self.assertEqual(request.url.path,"/api/v1/models/user")
            return httpx.Response(200,json={"data":[model("allowed")]})
        registry=Registry();registry.roles={"designer":["forbidden","allowed"],"art":[]}
        with patch.dict(os.environ,{"OPENROUTER_API_KEY":"  test-secret  "}), patch(
                "studio.registry.httpx.AsyncClient",side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))), patch(
                "studio.registry.write_json") as save:
            await registry.refresh()
        self.assertEqual(list(registry.candidates("designer")),["allowed"])
        self.assertTrue(all(r.headers["Authorization"]=="Bearer test-secret" for r in calls))
        self.assertNotIn("private-label",str(save.call_args))
        self.assertNotIn("test-secret",str(save.call_args))

    async def test_missing_key_does_not_make_network_requests(self):
        with patch.dict(os.environ,{"OPENROUTER_API_KEY":"  "}),patch("studio.registry.httpx.AsyncClient") as client:
            with self.assertRaisesRegex(RuntimeError,"OPENROUTER_API_KEY"):
                await Registry().refresh()
        client.assert_not_called()

    async def test_management_and_legacy_provisioning_keys_stop_before_model_discovery(self):
        original=httpx.AsyncClient
        for metadata in ({"is_management_key":True}, {"is_provisioning_key":True},
                         {"is_management_key":False,"is_provisioning_key":True}):
            with self.subTest(metadata=metadata):
                calls=[]
                def handle(request):
                    calls.append(request)
                    return httpx.Response(200,json={"data":metadata})
                registry=Registry()
                with patch.dict(os.environ,{"OPENROUTER_API_KEY":"test-secret"}),patch(
                        "studio.registry.httpx.AsyncClient",side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))),patch(
                        "studio.registry.write_json") as save:
                    with self.assertRaisesRegex(RuntimeError,"management/provisioning key.*cannot call any model") as error:
                        await registry.refresh()
                self.assertEqual([r.url.path for r in calls],["/api/v1/key"])
                self.assertEqual(registry.models,{})
                self.assertIsNone(registry.verified_at)
                self.assertNotIn("test-secret",str(error.exception))
                save.assert_not_called()

    async def test_regular_paid_and_free_keys_both_allow_model_discovery(self):
        original=httpx.AsyncClient
        for free_tier in (True,False):
            with self.subTest(free_tier=free_tier):
                calls=[]
                def handle(request):
                    calls.append(request)
                    if request.url.path=="/api/v1/key":
                        return httpx.Response(200,json={"data":{"is_management_key":False,
                            "is_provisioning_key":False,"is_free_tier":free_tier}})
                    return httpx.Response(200,json={"data":[model("paid-model","0.000001","0.000002")]})
                registry=Registry();registry.roles={"designer":["paid-model"],"art":[]}
                with patch.dict(os.environ,{"OPENROUTER_API_KEY":"test-secret"}),patch(
                        "studio.registry.httpx.AsyncClient",side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))),patch(
                        "studio.registry.write_json"):
                    await registry.refresh()
                self.assertEqual(list(registry.candidates("designer")),["paid-model"])
                self.assertEqual(len(calls),2)

    def test_configured_defaults_first_then_bounded_compatible_fallbacks(self):
        registry=Registry();registry.roles={"developer":["missing","preferred","preferred"]}
        registry.models={"preferred":model("preferred","0.000001","0.000002"),
                         **{f"free{i}:free":model(f"free{i}:free") for i in range(5)},
                         "expensive":model("expensive","1","1"),
                         "invalid":model("invalid","NaN"),
                         "not-json":model("not-json",supported_parameters=[]),
                         "image":model("image",architecture={"output_modalities":["image"]}),
                         "tiny":model("tiny",context_length=1024)}
        with patch.dict(os.environ,{"STUDIO_AUTO_MODEL_FALLBACK":"1"}):
            self.assertEqual(list(registry.candidates("developer")),["preferred","free0:free","free1:free","free2:free"])
        with patch.dict(os.environ,{"STUDIO_AUTO_MODEL_FALLBACK":"0"}):
            self.assertEqual(list(registry.candidates("developer")),["preferred"])
