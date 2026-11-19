"""Only explicitly configured, freshly priced models can receive paid work."""
import json
import os
from datetime import datetime, timezone

import httpx

from studio.config import CACHE, ROOT
from studio.state import write_json

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
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            self.models = {m["id"]: m for m in response.json()["data"]}
            for model_id in self.roles["art"]:
                response = await client.get("https://openrouter.ai/api/v1/images/models/" + model_id + "/endpoints")
                if response.is_success:
                    self.images[model_id] = response.json()
        self.verified_at = datetime.now(timezone.utc).isoformat()
        write_json(CACHE / "models.json", dict(verified_at=self.verified_at, models=self.models, images=self.images))

    def candidates(self, role):
        for mid in self.roles[role]:
            if role == "art":
                if mid in self.images:
                    yield mid
            else:
                model = self.models.get(mid)
                if model and "response_format" in model.get("supported_parameters", []):
                    # Reject unexpectedly expensive configuration changes.
                    rates = self.rates(mid)
                    if rates[0] <= 0.000002 and rates[1] <= 0.000005:
                        yield mid

    def rates(self, mid):
        pricing = self.models[mid]["pricing"]
        tiers = [pricing] + pricing.get("overrides", [])
        return tuple(max(float(t.get(key, pricing[key])) for t in tiers) for key in ("prompt", "completion"))

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
