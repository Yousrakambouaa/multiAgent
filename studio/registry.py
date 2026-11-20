"""Prefer configured models, then discover compatible, freshly priced fallbacks."""
import json
import os
import math
from datetime import datetime, timezone

import httpx

from studio.config import CACHE, ROOT
from studio.state import write_json
from studio.openrouter import api_key, authentication_error, validate_inference_key

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
        config = ROOT / "studio_models.json"
        self.roles = json.loads(config.read_text(encoding="utf-8")) if config.exists() else DEFAULTS
        self.models = {}
        self.images = {}
        self.verified_at = None

    async def refresh(self):
        self.models, self.images = {}, {}
        self.verified_at = None
        headers = {"Authorization": "Bearer " + api_key()}
        async with httpx.AsyncClient(timeout=40) as client:
            # Validate the key without a completion or a spending reservation.
            response = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
            if response.status_code == 401:
                raise RuntimeError(authentication_error())
            response.raise_for_status()
            validate_inference_key(response.json().get("data"))
            # This catalog applies the active key's provider/privacy/guardrail settings.
            response = await client.get("https://openrouter.ai/api/v1/models/user", headers=headers)
            if response.status_code == 401:
                raise RuntimeError(authentication_error())
            response.raise_for_status()
            self.models = {m["id"]: m for m in response.json()["data"]}
            for model_id in self.roles["art"]:
                try:
                    response = await client.get("https://openrouter.ai/api/v1/images/models/" + model_id + "/endpoints")
                    if response.is_success:
                        data = response.json()
                        if isinstance(data, dict) and isinstance(data.get("endpoints"), list):
                            self.images[model_id] = data
                except (httpx.HTTPError, ValueError):
                    # Optional art discovery cannot prevent text-only planning.
                    continue
        self.verified_at = datetime.now(timezone.utc).isoformat()
        write_json(CACHE / "models.json", dict(verified_at=self.verified_at, models=self.models, images=self.images))

    def candidates(self, role):
        configured = self.roles.get(role, [])
        if role == "art":
            for mid in configured:
                if mid in self.images:
                    yield mid
            return
        seen = set()
        for mid in configured:
            if mid not in seen and self.text_eligible(mid):
                seen.add(mid)
                yield mid
        if os.environ.get("STUDIO_AUTO_MODEL_FALLBACK", "1") == "1":
            discovered = [mid for mid, model in self.models.items() if mid not in seen
                          and model.get("context_length", 0) >= 32768 and self.text_eligible(mid)]
            # Keep the attempt count bounded. Free/cheaper models allow restricted
            # accounts to keep working; code profiles prefer coding models on ties.
            discovered.sort(key=lambda mid: (sum(self.rates(mid)),
                0 if role in ("developer", "scene", "qa") and "coder" in mid else 1, mid))
            yield from discovered[:3]

    def text_eligible(self, mid):
        model = self.models.get(mid)
        if not model or "response_format" not in model.get("supported_parameters", []):
            return False
        if "text" not in model.get("architecture", {}).get("output_modalities", ["text"]):
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
                if ceiling <= 0.08:
                    return endpoint, ceiling + 0.0001
        raise RuntimeError("No image endpoint with a bounded low-cost price")
