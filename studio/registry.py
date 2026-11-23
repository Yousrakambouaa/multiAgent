"""Prefer configured models, then discover compatible, freshly priced fallbacks."""
import json
import os
import math
import hashlib
from datetime import datetime, timezone

import httpx

from studio.config import CACHE, ROOT
from studio.state import write_json
from studio.openrouter import api_key, authentication_error, validate_inference_key
from studio.providers import AIProvider
from studio.model_selection import capabilities, rank_key

DEFAULTS = {
    "supervisor": ["~deepseek/deepseek-v4-flash-latest", "deepseek/deepseek-v4.1-flash"],
    "designer": ["~deepseek/deepseek-v4-flash-latest", "deepseek/deepseek-v4.1-flash"],
    "developer": ["qwen/qwen3-coder-flash", "qwen/qwen3-coder-30b-a3b-instruct"],
    "scene": ["qwen/qwen3-coder-flash", "qwen/qwen3-coder-30b-a3b-instruct"],
    "qa": ["qwen/qwen3-coder-flash", "qwen/qwen3-coder-30b-a3b-instruct"],
    "ui": ["~deepseek/deepseek-v4-flash-latest", "deepseek/deepseek-v4.1-flash"],
    "art": ["recraft/recraft-v4.1-flash", "bytedance-seed/seedream-5-0-flash", "black-forest-labs/flux.2-klein-4b"],
}


class Registry:
    def __init__(self):
        self.provider = AIProvider()
        config = ROOT / "studio_models.json"
        if self.provider.name == "dahl":
            self.roles = {role: [mid.strip() for mid in os.environ.get(
                f"DAHL_MODEL_{role.upper()}", "MiniMaxAI/MiniMax-M2.7").split(",") if mid.strip()]
                for role in ("supervisor", "designer", "developer", "art", "qa")}
            self.roles["ui"] = self.roles["designer"]
            self.roles["scene"] = self.roles["developer"]
        else:
            self.roles = json.loads(config.read_text(encoding="utf-8")) if config.exists() else DEFAULTS
        self.models = {}
        self.images = {}
        self.verified_at = None
        self.free_only = os.environ.get("STUDIO_FREE_ONLY", "0") == "1"
        self.evidence = {}
        self._identity = None

    def dynamic(self):
        return (os.environ.get("STUDIO_MODEL_SELECTION", "dynamic") == "dynamic"
                and os.environ.get("STUDIO_AUTO_MODEL_FALLBACK", "1") == "1")

    def capability_summary(self):
        return {"provider": self.provider.name, "image_generation": bool(list(self.candidates("art"))),
                "image_limitation": ("" if self.images else
                    f"{self.provider.label} has no usable image-generation endpoint; do not send image tasks to text models."),
                "text_models": {mid: capabilities(self.models[mid]) for mid in list(self.candidates("designer"))[:4]}}

    def _cache_path(self):
        return CACHE / ("models_dahl.json" if self.provider.name == "dahl" else "models.json")

    def _cached_catalog(self):
        try:
            data = json.loads(self._cache_path().read_text(encoding="utf-8"))
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["verified_at"])).total_seconds()
            if data.get("cache_version") == 2 and data.get("identity") == self._identity and 0 <= age < 300:
                self.models, self.images = data["models"], data["images"]
                self.verified_at = data["verified_at"]
                return True
        except (OSError, KeyError, TypeError, ValueError):
            pass
        return False

    def _save_catalog(self):
        self.verified_at = datetime.now(timezone.utc).isoformat()
        write_json(self._cache_path(), dict(cache_version=2, verified_at=self.verified_at, identity=self._identity,
                                          models=self.models, images=self.images))

    def record_outcome(self, model, role, passed, kind="contract"):
        if self._identity is None:
            return
        records = self.evidence.setdefault(model, {}).setdefault(role, [])
        records.append({"kind": kind, "passed": bool(passed)})
        del records[:-100]
        write_json(CACHE / ("model_evidence_" + self._identity + ".json"), self.evidence)

    async def refresh(self):
        self.models, self.images = {}, {}
        self.verified_at = None
        self.free_only = os.environ.get("STUDIO_FREE_ONLY", "0") == "1"
        # Both catalog and performance evidence are scoped to provider URL/key.
        key = self.provider.api_key()
        self._identity = hashlib.sha256((self.provider.base_url + "\0" + key).encode()).hexdigest()
        try:
            self.evidence = json.loads((CACHE / ("model_evidence_" + self._identity + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.evidence = {}
        if self.provider.name == "dahl":
            if self._cached_catalog():
                return
            await self._refresh_dahl()
            return
        headers = {"Authorization": "Bearer " + api_key()}
        async with httpx.AsyncClient(timeout=40) as client:
            # Validate the key without a completion or a spending reservation.
            response = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
            if response.status_code == 401:
                raise RuntimeError(authentication_error())
            response.raise_for_status()
            validate_inference_key(response.json().get("data"))
            # Always check current credentials; cached model data never bypasses it.
            if self._cached_catalog():
                return
            # This catalog applies the active key's provider/privacy/guardrail settings.
            response = await client.get("https://openrouter.ai/api/v1/models/user", headers=headers)
            if response.status_code == 401:
                raise RuntimeError(authentication_error())
            response.raise_for_status()
            self.models = {m["id"]: m for m in response.json()["data"]}
            image_ids = list(self.roles.get("art", []))
            if self.dynamic() and image_ids:
                try:
                    response = await client.get(self.provider.base_url + "/images/models", headers=headers)
                    if response.is_success:
                        catalog = response.json().get("data", [])
                        image_ids = list(dict.fromkeys(image_ids + [m["id"] for m in catalog if capabilities(m)["image_generation"]]))
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
            for model_id in image_ids[:24]:
                try:
                    response = await client.get("https://openrouter.ai/api/v1/images/models/" + model_id + "/endpoints", headers=headers)
                    if response.is_success:
                        data = response.json()
                        if isinstance(data, dict) and isinstance(data.get("endpoints"), list):
                            self.images[model_id] = data
                except (httpx.HTTPError, ValueError):
                    # Optional art discovery cannot prevent text-only planning.
                    continue
        self._save_catalog()

    async def _refresh_dahl(self):
        headers = {"Authorization": "Bearer " + self.provider.api_key()}
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.get(self.provider.base_url + "/models", headers=headers)
            if response.status_code == 401:
                raise RuntimeError(self.provider.authentication_error())
            response.raise_for_status()
            # Dahl's OpenAI catalog lacks OpenRouter pricing/capability metadata.
            # Standard rate: $0.02/M tokens (ignore temporary prepaid discounts).
            # https://inference.dahl.global/pricing
            self.models = {m["id"]: {**m,
                "pricing": {"prompt": "0.00000002", "completion": "0.00000002"},
                "context_length": m.get("context_length"),
                "capability_source": "Dahl OpenAI-compatible text API; unspecified capabilities are unknown"}
                for m in response.json()["data"]}
        self._save_catalog()

    def candidates(self, role):
        configured = self.roles.get(role, [])
        if role == "art":
            # Dahl serves text only; keep existing unavailable/procedural art paths.
            if self.provider.name == "dahl":
                return
            available = list(self.images) if self.dynamic() else configured
            eligible = []
            for mid in available:
                if mid not in self.images:
                    continue
                try:
                    _, ceiling = self.image_endpoint(mid)
                    eligible.append((ceiling, mid))
                except (RuntimeError, KeyError, TypeError, ValueError):
                    continue
            for _, mid in sorted(eligible)[:4]:
                yield mid
            return
        if self.dynamic() and self.verified_at:
            eligible = [mid for mid in self.models if self.text_eligible(mid)
                        and self._context_eligible(self.models[mid])
                        and self._permitted(mid, configured)]
            eligible.sort(key=lambda mid: rank_key(self.models[mid], role, self.evidence.get(mid, {}),
                sum(self.rates(mid)), configured.index(mid) if mid in configured else len(configured)))
            yield from eligible[:4]
            return
        seen = set()
        for mid in configured:
            if mid not in seen and self.text_eligible(mid) and self._permitted(mid, configured):
                seen.add(mid)
                yield mid
        if os.environ.get("STUDIO_AUTO_MODEL_FALLBACK", "1") == "1":
            discovered = [mid for mid, model in self.models.items() if mid not in seen
                          and self._context_eligible(model) and self.text_eligible(mid)
                          and self._permitted(mid, configured)]
            # Keep the attempt count bounded. Free/cheaper models allow restricted
            # accounts to keep working; code profiles prefer coding models on ties.
            discovered.sort(key=lambda mid: (sum(self.rates(mid)),
                mid))
            yield from discovered[:3]

    def _permitted(self, mid, configured):
        if self.provider.name != "openrouter":
            return True
        prompt, completion = self.rates(mid)
        free = prompt == completion == float(self.models[mid]["pricing"].get("request", 0)) == 0
        if self.free_only:
            return free
        # Dynamic paid OpenRouter selection requires deliberate opt-in. The
        # configured compatibility mode retains its explicit model allowlist.
        return (free or os.environ.get("STUDIO_ALLOW_PAID_MODELS", "0") == "1"
                or (not self.dynamic() and mid in configured))

    def _context_eligible(self, model):
        context = model.get("context_length")
        # Dahl's minimal /models response omits context windows. Never invent
        # a 32K limit or a quality score; backend limits remain authoritative.
        return context >= 32768 if context else self.provider.name == "dahl"

    def request_fits(self, mid, messages, max_tokens):
        caps = capabilities(self.models[mid])
        output = caps["max_completion_tokens"]
        if output and max_tokens > output:
            return False
        # Bytes conservatively bound input as in existing cost reservations.
        required = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 1024 + max_tokens
        return not caps["context_length"] or required <= caps["context_length"]

    def text_eligible(self, mid):
        model = self.models.get(mid)
        if not model:
            return False
        supported = model.get("supported_parameters")
        if supported is not None and "response_format" not in supported:
            return False
        if supported is None and self.provider.name != "dahl":
            return False
        if not capabilities(model)["text"]:
            return False
        try:
            prompt, completion = self.rates(mid)
            request = float(model["pricing"].get("request", 0))
            return prompt <= .000002 and completion <= .000005 and math.isfinite(request) and 0 <= request <= .01
        except (KeyError, TypeError, ValueError):
            return False

    def rates(self, mid):
        pricing = self.models[mid]["pricing"]
        tiers = [pricing] + pricing.get("overrides", [])
        rates = [tuple(float(t.get(key, pricing[key])) for t in tiers) for key in ("prompt", "completion")]
        if any(not math.isfinite(rate) or rate < 0 for group in rates for rate in group):
            raise ValueError("Invalid model pricing")
        return tuple(max(group) for group in rates)

    def text_ceiling(self, mid, messages, max_tokens):
        # UTF-8 bytes safely overcount normal text tokens; reserve all output.
        prompt, completion = self.rates(mid)
        request_price=float(self.models[mid]["pricing"].get("request",0))
        if request_price<0 or request_price>0.01:
            raise ValueError("Unexpected per-request fee")
        return (len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 1024) * prompt + max_tokens * completion + request_price + 0.0001

    def image_endpoint(self, mid):
        for endpoint in self.images[mid]["endpoints"]:
            prices = endpoint.get("pricing", [])
            outputs = [p for p in prices if p["billable"] == "output_image"]
            if outputs and all(p["unit"] in ("image", "megapixel") for p in prices):
                # Up to 4 megapixels at default 1K output; deliberately overreserve.
                ceiling = sum(float(p["cost_usd"]) * (4 if p["unit"] == "megapixel" else 1) for p in prices)
                if (math.isfinite(ceiling) and 0 <= ceiling <= 0.08
                        and all(math.isfinite(float(p["cost_usd"])) and float(p["cost_usd"]) >= 0 for p in prices)
                        and (not self.free_only or ceiling == 0)
                        and (ceiling == 0 or os.environ.get("STUDIO_ALLOW_PAID_MODELS", "0") == "1"
                             or (not self.dynamic() and mid in self.roles.get("art", [])))):
                    return endpoint, ceiling + 0.0001
        raise RuntimeError("No image endpoint with a bounded low-cost price")
