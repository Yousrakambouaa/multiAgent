"""Prefer configured models, then discover compatible, freshly priced fallbacks."""
import json
import os
import math
import hashlib
import time
from datetime import datetime, timezone

import httpx

from studio.config import CACHE, ROOT
from studio.state import write_json
from studio.openrouter import api_key, authentication_error, validate_inference_key, key_allowance
from studio.providers import AIProvider, provider_response_message, redact_provider_message
from studio.model_selection import capabilities, rank_key, value_ranking


def minimum_output(role, requested):
    return min(requested, 4096 if role in ("developer", "scene") else 2048 if role == "qa" else 1024)


DEFAULTS = {
    "supervisor": [
        "deepseek/deepseek-v4.1-flash-1017",
        "minimax/minimax-m3.1-flash",
        "z-ai/glm-5.3-flash",
    ],
    "designer": [
        "minimax/minimax-m3.1-flash",
        "qwen/qwen3.8-flash",
        "deepseek/deepseek-v4.1-flash-1017",
    ],
    "developer": [
        "minimax/minimax-m3.1-flash",
        "deepseek/deepseek-v4.1-flash-1017",
        "qwen/qwen3-coder-next",
        "z-ai/glm-5.3-flash",
        "anthropic/claude-sonnet-5.5",
    ],
    "scene": [
        "qwen/qwen3-coder-next",
        "minimax/minimax-m3.1-flash",
        "deepseek/deepseek-v4.1-flash-1017",
    ],
    "qa": [
        "deepseek/deepseek-v4.1-flash-1017",
        "z-ai/glm-5.3-flash",
        "minimax/minimax-m3.1-flash",
        "anthropic/claude-sonnet-5.5",
    ],
    "ui": [
        "minimax/minimax-m3.1-flash",
        "qwen/qwen3.8-flash",
        "google/gemini-3.8-flash",
    ],
    "art": [
        "bytedance-seed/seedream-5-5",
        "recraft/recraft-v4.1-flash-v2",
        "black-forest-labs/flux.2-klein-4b",
    ],
}


class ProviderCatalogError(RuntimeError):
    def __init__(self, provider, operation, status, message):
        self.provider = provider
        self.operation = operation
        self.status = status
        self.diagnostic = redact_provider_message(message)
        status_text = f"HTTP {status}" if status is not None else "connection failure"
        super().__init__(f"{provider} {operation} failed ({status_text}): {self.diagnostic}")


def _require_catalog_success(response, provider, operation):
    if not response.is_success:
        raise ProviderCatalogError(provider, operation, response.status_code,
                                   provider_response_message(response))


def _catalog_json(response, provider, operation):
    try:
        data = response.json()
    except ValueError as error:
        raise ProviderCatalogError(provider, operation, response.status_code,
                                   "response was not valid JSON") from error
    if not isinstance(data, dict):
        raise ProviderCatalogError(provider, operation, response.status_code,
                                   "response was not a JSON object")
    return data


async def _catalog_get(client, url, headers, provider, operation):
    try:
        return await client.get(url, headers=headers)
    except httpx.HTTPError as error:
        raise ProviderCatalogError(provider, operation, None,
                                   f"{type(error).__name__}: {redact_provider_message(error)}") from error


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
        self.allowance = None
        self.unavailable = set()
        self.unavailable_until = {}
        self.json_disabled = set()
        self.unsupported_parameters = {}
        self.benchmarks = {}
        self.benchmark_meta = {}
        self._benchmarks_identity = None
        self._benchmarks_next_check = 0
        self.value_decisions = {}

    def dynamic(self):
        if self.provider.name == "openrouter":
            return True
        return (os.environ.get("STUDIO_MODEL_SELECTION", "dynamic") == "dynamic"
                and os.environ.get("STUDIO_AUTO_MODEL_FALLBACK", "1") == "1")

    def block_model(self, model, seconds=300):
        self.unavailable.add(model)
        self.unavailable_until[model] = time.time() + seconds

    def is_unavailable(self, model):
        if model in self.unavailable_until and self.unavailable_until[model] <= time.time():
            self.unavailable_until.pop(model)
            self.unavailable.discard(model)
        return model in self.unavailable

    def _set_allowance(self, metadata, credits, observed_at):
        snapshot = key_allowance(metadata, credits)
        # Some inference keys expose a balance but no account identifier. Share
        # that unknown scope conservatively rather than overspend across keys.
        scope = metadata.get("workspace_id") or metadata.get("organization_id") or self.provider.base_url
        snapshot.update(provider="openrouter", identity=self._identity,
                        scope=hashlib.sha256(str(scope).encode()).hexdigest(), observed_at=observed_at)
        self.allowance = snapshot

    async def refresh_allowance(self):
        if self.provider.name != "openrouter":
            return
        started = time.time()
        headers = {"Authorization": "Bearer " + self.provider.api_key()}
        identity = hashlib.sha256((self.provider.base_url + "\0" + self.provider.api_key()).encode()).hexdigest()
        if identity != self._identity:
            await self.refresh()
            return
        async with httpx.AsyncClient(timeout=20) as client:
            response = await _catalog_get(client, self.provider.base_url + "/key", headers,
                                          self.provider.label, "key allowance lookup")
            _require_catalog_success(response, self.provider.label, "key allowance lookup")
            metadata = _catalog_json(response, self.provider.label, "key allowance lookup").get("data")
            validate_inference_key(metadata)
            credits = None
            try:
                response = await client.get(self.provider.base_url + "/credits", headers=headers)
                if response.is_success:
                    credits = response.json().get("data")
            except (httpx.HTTPError, ValueError):
                pass  # Some inference keys cannot read account credits.
            self._set_allowance(metadata, credits, started)

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

    def record_outcome(self, model, role, passed, kind="contract", **details):
        if self._identity is None:
            return
        records = self.evidence.setdefault(model, {}).setdefault(role, [])
        if details.get("task_id"):
            records[:] = [r for r in records if not (r.get("task_id") == details["task_id"] and r.get("kind") == kind)]
        records.append({**details, "kind": kind, "passed": bool(passed)})
        del records[:-100]
        write_json(CACHE / ("model_evidence_" + self._identity + ".json"), self.evidence)

    async def refresh_benchmarks(self):
        """Optional metadata only, cached daily; failure must not block a game."""
        if self.provider.name != "openrouter" or not self._identity:
            return
        now = time.time()
        path = CACHE / ("benchmarks_" + self._identity + ".json")
        if self._benchmarks_identity != self._identity:
            self.benchmarks, self.benchmark_meta = {}, {}
            self._benchmarks_next_check = 0
            self._benchmarks_identity = self._identity
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if (0 <= now - saved["checked_at"] < 86400 and isinstance(saved.get("models"), dict)
                        and isinstance(saved.get("meta"), dict)):
                    self.benchmarks, self.benchmark_meta = saved["models"], saved["meta"]
                    self._benchmarks_next_check = saved["checked_at"] + 86400
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if now < self._benchmarks_next_check:
            return
        self._benchmarks_next_check = now + 300
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(self.provider.base_url + "/benchmarks",
                    headers={"Authorization": "Bearer " + self.provider.api_key()})
            if not response.is_success:
                return
            document = response.json()
            if not isinstance(document, dict):
                return
            rows = document.get("data")
            if not isinstance(rows, list):
                return
            grouped = {}
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("model_permaslug"), str):
                    grouped.setdefault(row["model_permaslug"], []).append(row)
            self.benchmarks, self.benchmark_meta = grouped, document.get("meta", {})
            write_json(path, {"checked_at": now, "models": grouped, "meta": self.benchmark_meta})
            self._benchmarks_next_check = now + 86400
        except (httpx.HTTPError, ValueError, OSError):
            pass

    def hybrid_candidates(self, role, messages, max_tokens, budget, *, escalate=False, complex_task=False):
        options = []
        benchmarks = {}
        for mid, model in self.models.items():
            try:
                if not self.text_eligible(mid) or not self._permitted(mid, self.roles.get(role, [])):
                    continue
                output = self.output_limit(mid, messages, max_tokens, budget, minimum_output(role, max_tokens))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if output is None:
                continue
            options.append({"model": mid, "quoted_usd": self.text_ceiling(mid, messages, output),
                            "requested_tokens": max_tokens, "output_tokens": output})
            benchmarks[mid] = self.benchmarks.get(model.get("canonical_slug", mid), self.benchmarks.get(mid, []))
        ranked = value_ranking(options, role, self.evidence, benchmarks, self.roles.get(role, []),
                               escalate=escalate, complex_task=complex_task)
        self.value_decisions[role] = {"policy": "hybrid", "reason": "failed_validation" if escalate else
            "task_complexity" if complex_task else "measured_validation_failures" if ranked and not ranked[0]["economical"]
            else "economical_first", "candidates": ranked[:4],
            "eligible_count": len(options), "external_benchmarks_are_unity_results": False}
        return [o["model"] for o in ranked[:4]]

    async def refresh(self):
        self.models, self.images = {}, {}
        self.verified_at = None
        self.free_only = os.environ.get("STUDIO_FREE_ONLY", "0") == "1"
        # Both catalog and performance evidence are scoped to provider URL/key.
        key = self.provider.api_key()
        identity = hashlib.sha256((self.provider.base_url + "\0" + key).encode()).hexdigest()
        if identity != self._identity:
            self.unavailable.clear()
            self.unavailable_until.clear()
            self.json_disabled.clear()
            self.unsupported_parameters.clear()
        self._identity = identity
        self.allowance = None
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
            observed_at = time.time()
            # Validate the key without a completion or a spending reservation.
            response = await _catalog_get(client, "https://openrouter.ai/api/v1/key", headers,
                                          self.provider.label, "model key verification")
            if response.status_code == 401:
                raise RuntimeError(authentication_error() + " Provider response: "
                                   + provider_response_message(response))
            _require_catalog_success(response, self.provider.label, "model catalog lookup")
            metadata = _catalog_json(response, self.provider.label, "model key verification").get("data")
            validate_inference_key(metadata)
            credits = None
            try:
                credit_response = await client.get(self.provider.base_url + "/credits", headers=headers)
                if credit_response.is_success:
                    credits = credit_response.json().get("data")
            except (httpx.HTTPError, ValueError):
                pass
            self._set_allowance(metadata, credits, observed_at)
            # Always check current credentials; cached model data never bypasses it.
            if self._cached_catalog():
                return
            # This catalog applies the active key's provider/privacy/guardrail settings.
            response = await _catalog_get(client, "https://openrouter.ai/api/v1/models/user", headers,
                                          self.provider.label, "model catalog lookup")
            if response.status_code == 401:
                raise RuntimeError(authentication_error() + " Provider response: "
                                   + provider_response_message(response))
            _require_catalog_success(response, self.provider.label, "model catalog lookup")
            catalog = _catalog_json(response, self.provider.label, "model catalog lookup").get("data")
            if not isinstance(catalog, list):
                raise ProviderCatalogError(self.provider.label, "model catalog lookup", response.status_code,
                                           "response did not contain a model list")
            self.models = {m["id"]: m for m in catalog if isinstance(m, dict) and isinstance(m.get("id"), str)}
            image_ids = list(self.roles.get("art", []))
            if self.dynamic():
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
            response = await _catalog_get(client, self.provider.base_url + "/models", headers,
                                          self.provider.label, "model catalog lookup")
            if response.status_code == 401:
                raise RuntimeError(self.provider.authentication_error() + " Provider response: "
                                   + provider_response_message(response))
            _require_catalog_success(response, self.provider.label, "model catalog lookup")
            catalog = _catalog_json(response, self.provider.label, "model catalog lookup").get("data")
            if not isinstance(catalog, list):
                raise ProviderCatalogError(self.provider.label, "model catalog lookup", response.status_code,
                                           "response did not contain a model list")
            # Dahl's OpenAI catalog lacks OpenRouter pricing/capability metadata.
            # Standard rate: $0.02/M tokens (ignore temporary prepaid discounts).
            # https://inference.dahl.global/pricing
            self.models = {m["id"]: {**m,
                "pricing": {"prompt": "0.00000002", "completion": "0.00000002"},
                "context_length": m.get("context_length"),
                "capability_source": "Dahl OpenAI-compatible text API; unspecified capabilities are unknown"}
                for m in catalog if isinstance(m, dict) and isinstance(m.get("id"), str)}
        self._save_catalog()

    def candidates(self, role, messages=None, max_tokens=None, max_cost_usd=None, *, transparent=False,
                   hybrid=False, escalate=False, complex_task=False, reference=False):
        configured = self.roles.get(role, [])
        if hybrid and self.provider.name == "openrouter" and role != "art" and messages is not None:
            yield from self.hybrid_candidates(role, messages, max_tokens, max_cost_usd,
                                              escalate=escalate, complex_task=complex_task)
            return

        def fits_request(mid):
            if messages is None or max_tokens is None:
                return True
            try:
                if self.provider.name == "openrouter" and max_cost_usd is not None:
                    return self.output_limit(mid, messages, max_tokens, max_cost_usd,
                                             min_tokens=minimum_output(role, max_tokens)) is not None
                if not self.request_fits(mid, messages, max_tokens):
                    return False
                return (max_cost_usd is None or
                        self.text_ceiling(mid, messages, max_tokens) <= max_cost_usd)
            except (KeyError, TypeError, ValueError):
                return False

        if role == "art":
            # Dahl serves text only; keep existing unavailable/procedural art paths.
            if self.provider.name == "dahl":
                return
            available = list(self.images) if self.dynamic() else configured
            eligible = []
            for mid in available:
                if mid not in self.images or self.is_unavailable(mid):
                    continue
                try:
                    _, ceiling = self.image_endpoint(mid, transparent=transparent)
                    eligible.append((ceiling, mid))
                except (RuntimeError, KeyError, TypeError, ValueError):
                    continue
            order = (lambda item: (configured.index(item[1]) if item[1] in configured else len(configured), item[0]))
            if hybrid and eligible:
                image_evidence = {mid: {"art": [r for r in self.evidence.get(mid, {}).get("art", [])
                    if r.get("kind") != "usable_image" or r.get("transparency_required") == transparent]}
                    for _, mid in eligible}
                ranked = value_ranking([{"model": mid, "quoted_usd": cost} for cost, mid in eligible],
                                      "art", image_evidence, {}, configured, escalate=True)
                def reference_fit(item):
                    endpoint, _ = self.image_endpoint(item["model"], transparent=transparent)
                    return reference and "input_references" not in endpoint.get("supported_parameters", {})
                yield from (o["model"] for o in sorted(ranked, key=reference_fit)[:4])
                return
            for _, mid in sorted(eligible, key=order if not self.dynamic() else None)[:4]:
                yield mid
            return
        if self.dynamic() and self.verified_at:
            eligible = [mid for mid in self.models if self.text_eligible(mid)
                        and self._context_eligible(self.models[mid])
                        and fits_request(mid)
                        and self._permitted(mid, configured)]
            eligible.sort(key=lambda mid: rank_key(self.models[mid], role, self.evidence.get(mid, {}),
                sum(self.rates(mid)), configured.index(mid) if mid in configured else len(configured)))
            if self.provider.name == "openrouter" and not self.free_only:
                paid = [mid for mid in eligible if not self._is_free(mid)]
                free = [mid for mid in eligible if self._is_free(mid)]
                prefer_free = self.allowance and (self.allowance.get("remaining_usd") == 0 or self.allowance.get("free_tier"))
                selected = (free[:3] + paid[:1]) if prefer_free else (paid[:3] + free[:1])
                if len(selected) < 4:
                    selected.extend(mid for mid in eligible if mid not in selected)
                # Actual Unity/contract outcomes can promote a free model over
                # paid preferences; one successful API envelope is not proof of
                # stronger game-building quality.
                yield from sorted(selected[:4], key=lambda mid: rank_key(
                    self.models[mid], role, self.evidence.get(mid, {}), sum(self.rates(mid)),
                    configured.index(mid) if mid in configured else len(configured))[:2])
            else:
                yield from eligible[:4]
            return
        seen = set()
        for mid in configured:
            if (mid not in seen and self.text_eligible(mid) and fits_request(mid)
                    and self._permitted(mid, configured)):
                seen.add(mid)
                yield mid
        if (self.provider.name == "openrouter" or (os.environ.get("STUDIO_AUTO_MODEL_FALLBACK", "1") == "1"
                and os.environ.get("STUDIO_MODEL_FALLBACK_SCOPE", "catalog") != "configured")):
            discovered = [mid for mid, model in self.models.items() if mid not in seen
                          and self._context_eligible(model) and self.text_eligible(mid)
                          and fits_request(mid)
                          and self._permitted(mid, configured)]
            # Keep the attempt count bounded. Free/cheaper models allow restricted
            # accounts to keep working; code profiles prefer coding models on ties.
            discovered.sort(key=lambda mid: (sum(self.rates(mid)),
                mid))
            yield from discovered[:3]

    def _permitted(self, mid, configured):
        if self.provider.name != "openrouter":
            return True
        free = self._is_free(mid)
        if self.free_only:
            return free
        return True  # Account allowance and request reservations govern affordability.

    def _is_free(self, mid):
        prompt, completion = self.rates(mid)
        free = prompt == completion == float(self.models[mid]["pricing"].get("request", 0)) == 0
        return free

    def _context_eligible(self, model):
        context = model.get("context_length")
        # Dahl's minimal /models response omits context windows. Never invent
        # a 32K limit or a quality score; backend limits remain authoritative.
        if self.provider.name == "openrouter":
            return bool(context and context > 0)
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
        # Batch adapters are asynchronous jobs, not chat/completions routes.
        if mid.endswith(":batch") or self.is_unavailable(mid):
            return False
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
        if self.provider.name != "dahl":
            architecture = model.get("architecture") or {}
            outputs = architecture.get("output_modalities", model.get("output_modalities"))
            modality = architecture.get("modality")
            if outputs is None and isinstance(modality, str) and "->" in modality:
                outputs = modality.split("->", 1)[1].split("+")
            inputs = architecture.get("input_modalities", model.get("input_modalities"))
            if inputs is None and isinstance(modality, str) and "->" in modality:
                inputs = modality.split("->", 1)[0].split("+")
            if not isinstance(outputs, list) or set(outputs) != {"text"}:
                return False
            if not isinstance(inputs, list) or "text" not in inputs:
                return False
        try:
            self.rates(mid)
            request = float(model["pricing"].get("request", 0))
            return math.isfinite(request) and request >= 0
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
        if not math.isfinite(request_price) or request_price < 0:
            raise ValueError("Invalid per-request fee")
        margin = 0.0001 if self.provider.name != "openrouter" or prompt or completion or request_price else 0
        return (len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 1024) * prompt + max_tokens * completion + request_price + margin

    def output_limit(self, mid, messages, requested, budget, min_tokens=1024):
        """Adapt output size to real context, published pricing and spendable funds."""
        caps = capabilities(self.models[mid])
        limit = min(requested, caps["max_completion_tokens"] or requested)
        if caps["context_length"]:
            limit = min(limit, caps["context_length"] - len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) - 1024)
        base = self.text_ceiling(mid, messages, 0)
        completion = self.rates(mid)[1]
        if base > budget:
            return None
        if completion:
            limit = min(limit, int(max(0, budget - base) / completion))
        return int(limit) if limit >= min(requested, min_tokens) else None

    def image_endpoint(self, mid, *, transparent=False):
        for endpoint in self.images[mid]["endpoints"]:
            supported = endpoint.get("supported_parameters", {})
            if transparent:
                background = supported.get("background")
                if background is None or (background.get("values") and "transparent" not in background["values"]):
                    continue
                formats = supported.get("output_format", {}).get("values", [])
                if formats and "png" not in formats:
                    continue
            prices = endpoint.get("pricing", [])
            outputs = [p for p in prices if p["billable"] == "output_image"]
            if outputs and all(p["unit"] in ("image", "megapixel") for p in prices):
                # Up to 4 megapixels at default 1K output; deliberately overreserve.
                ceiling = sum(float(p["cost_usd"]) * (4 if p["unit"] == "megapixel" else 1) for p in prices)
                if (math.isfinite(ceiling) and 0 <= ceiling <= 0.08
                        and all(math.isfinite(float(p["cost_usd"])) and float(p["cost_usd"]) >= 0 for p in prices)
                        and (not self.free_only or ceiling == 0)):
                    return endpoint, ceiling + (0.0001 if ceiling or self.provider.name != "openrouter" else 0)
        raise RuntimeError("No image endpoint with a bounded low-cost price")
