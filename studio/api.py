import asyncio
import json
import re
import os
import time
from uuid import uuid4

import httpx

from studio.costs import BudgetExceeded, Ledger
from studio.state import JobCancelled
from studio.providers import AIProvider, provider_response_message, redact_provider_message

REQUEST_TIMEOUT_SECONDS = float(os.environ.get("STUDIO_API_TIMEOUT_SECONDS", "300"))
OPENROUTER_TIMEOUT_SECONDS = float(os.environ.get("OPENROUTER_REQUEST_TIMEOUT_SECONDS", "180"))


class RequestDeadlineExceeded(httpx.ReadTimeout):
    """A total deadline, including provider keepalive heartbeats."""


class ServiceRejected(RuntimeError):
    def __init__(self, status, message, retry_after=None, provider_code=None, request_id=None, limit_source=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.provider_code = provider_code
        self.request_id = request_id
        self.limit_source = limit_source


class ResponseInvalid(RuntimeError):
    def __init__(self, message, truncated=False):
        super().__init__(message)
        self.truncated = truncated


class ModelAttemptsFailed(RuntimeError):
    def __init__(self, provider, role, attempts, reason=None, fallback_blocked=False):
        self.provider = provider
        self.role = role
        self.attempts = list(attempts)
        self.reason = reason
        self.fallback_blocked = fallback_blocked
        if self.attempts:
            details = "; ".join(
                f"{item['model']}: {item['reason']}" for item in self.attempts
            )
            message = f"{provider} could not complete the {role} model request after trying eligible models: {details}"
        else:
            message = reason or f"No eligible {provider} model is available for the {role} request"
        super().__init__(message)


def affordable_retry_limit(error, requested_tokens):
    """Return a safely lower output limit only for explicit OpenRouter 402 hints."""
    if not isinstance(error, ServiceRejected) or error.status != 402:
        return None
    match = re.search(r"can only afford\s+([\d,]+)", str(error), re.IGNORECASE)
    if not match:
        return None
    affordable = int(match.group(1).replace(",", ""))
    # Leave a small margin for provider-side pricing/token-count differences.
    reduced = min(requested_tokens - 1, affordable - max(128, affordable // 100))
    return reduced if 256 <= reduced < requested_tokens else None


def credential_rejection(error, allow_credit_fallback=False):
    if not isinstance(error, ServiceRejected):
        return False
    if error.status == 401:
        return True
    if error.status != 403:
        return False
    if allow_credit_fallback and credit_rejection(error):
        return False
    text = str(error).casefold()
    model_specific = ("model" in text and any(marker in text for marker in (
        "model access", "model is not enabled", "model unavailable", "model not found",
        "not authorized to use model", "model denied")))
    return not model_specific and any(marker in text for marker in (
        "api key", "credential", "authentication", "user not found", " denied"))


def credit_rejection(error):
    return isinstance(error, ServiceRejected) and (
        error.status == 402 or error.status == 403 and any(marker in str(error).casefold()
        for marker in ("key limit exceeded", "credit", "budget", "weekly limit", "daily limit", "monthly limit")))


def decode_json_response(data):
    """Accept text blocks/fences, but never turn a partial answer into success."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ResponseInvalid("Provider returned no completion choices")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise ResponseInvalid("Model exhausted its output limit (including reasoning); JSON is incomplete", truncated=True)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ResponseInvalid("Provider returned no completion message")
    if message.get("refusal") or choice.get("finish_reason") == "content_filter":
        raise ResponseInvalid("Provider refused the requested output")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content
                          if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
    if not isinstance(content, str) or not content.strip():
        raise ResponseInvalid("Provider returned empty/non-text content; no JSON answer (possibly reasoning-only)")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(content)
    except ValueError as error:
        raise ResponseInvalid("Model returned malformed JSON") from error
    if not isinstance(value, dict):
        raise ResponseInvalid("Model JSON must be an object")
    return value


class ModelService:
    def __init__(self, registry, job):
        self.registry, self.job = registry, job
        self.provider = getattr(registry, "provider", None) or AIProvider()
        if (self.provider.name == "openrouter" and job.data.get("openrouter_free_only")
                and job.data.get("openrouter_restricted_key") == getattr(registry, "_identity", None)):
            registry.free_only = True
        self.ledger = Ledger(job)
        if self.provider.name == "openrouter" and isinstance(getattr(registry, "allowance", None), dict):
            self.ledger.bind_allowance(registry.allowance)
        self._allowance_checked = 0
        self._mcp_unavailable = False
        self.selected_models = {}
        self.contract_failures = {}
        self._active_tasks = {}
        self.selected_tasks = {}
        self.hybrid = self.provider.name == "openrouter" and job.data.get("model_policy") == "hybrid"
        blocks = job.data.get("openrouter_model_blocks", {})
        if (self.provider.name == "openrouter" and blocks.get("identity") == getattr(registry, "_identity", None)
                and hasattr(registry, "unavailable")):
            for model, until in blocks.get("until", {}).items():
                if until > time.time():
                    registry.unavailable.add(model)
                    registry.unavailable_until[model] = until
        self.clear_stale_restrictions()

    def clear_stale_restrictions(self):
        if self.provider.name != "openrouter":
            return
        identity = getattr(self.registry, "_identity", None)
        for flag, key in (("openrouter_free_only", "openrouter_restricted_key"),
                          ("image_service_blocked", "image_service_blocked_key")):
            if self.job.data.get(flag) and self.job.data.get(key) != identity:
                self.job.data.pop(flag, None)
                self.job.data.pop(key, None)

    async def refresh_budget(self, force=False):
        # Only a verified OpenRouter registry can perform metadata requests.
        # Dahl and offline/injected registries retain their existing behavior.
        if self.provider.name != "openrouter" or not getattr(self.registry, "_identity", None):
            return
        if force or time.monotonic() - self._allowance_checked >= 2:
            from studio.registry import ProviderCatalogError
            try:
                await self.registry.refresh_allowance()
            except (httpx.HTTPError, ProviderCatalogError) as error:
                if getattr(error, "status", None) in (401, 403):
                    raise
                self.job.event("supervisor", "allowance_lookup_unavailable", error=type(error).__name__)
                # Keep the previous bound and all reservations when lookup fails.
            self._allowance_checked = time.monotonic()
        if isinstance(getattr(self.registry, "allowance", None), dict):
            self.clear_stale_restrictions()
            self.ledger.bind_allowance(self.registry.allowance)

    def record_validation(self, role, passed):
        source_role = "developer" if self.hybrid and role == "scene" and role not in self.selected_models else role
        model = self.selected_models.get(source_role)
        if self.hybrid and not passed:
            for profile in {role, "qa"}:
                self.job.data.setdefault("model_escalations", {})[profile] = "unity_validation_failed"
        if model and hasattr(self.registry, "record_outcome"):
            details = {"task_id": self.selected_tasks.get(source_role)} if self.hybrid else {}
            self.registry.record_outcome(model, role, passed, kind="unity", **details)
            self.job.event(role, "model_outcome", provider=self.provider.name, model=model,
                           kind="unity", passed=bool(passed), **details)

    def record_contract_failure(self, role):
        model = self.selected_models.get(role)
        if model:
            self.contract_failures.setdefault(role, set()).add(model)
        if model and hasattr(self.registry, "record_outcome"):
            details = {"task_id": self.selected_tasks.get(role)} if self.hybrid else {}
            if self.hybrid:
                self.job.data.setdefault("model_escalations", {})[role] = "contract_validation_failed"
            self.registry.record_outcome(model, role, False, kind="contract", **details)
            self.job.event(role, "model_outcome", provider=self.provider.name, model=model,
                           kind="contract", passed=False, **details)

    def record_contract_success(self, role):
        model = self.selected_models.get(role)
        if model and hasattr(self.registry, "record_outcome"):
            details = {"task_id": self.selected_tasks.get(role)} if self.hybrid else {}
            self.registry.record_outcome(model, role, True, kind="contract", **details)
            self.job.event(role, "model_outcome", provider=self.provider.name, model=model,
                           kind="contract", passed=True, **details)

    async def unity_context(self, role):
        """Supply real role-scoped Unity observations to the existing agents."""
        if self._mcp_unavailable:
            return {}
        from studio.mcp_client import UnityMCPClient, _content_value
        project = self.job.directory / "UnityProject"
        if not project.is_dir():
            return {}
        client = None
        try:
            client = UnityMCPClient.from_environment()
            if client is None:
                return {}
            await client.connect()
            facade = client.for_agent(role, project)
            name = "manage_asset" if role == "art" else "read_console" if role == "qa" else "manage_scene"
            args = ({"action": "search", "path": "Assets", "page_size": 20} if role == "art" else
                    {"action": "get", "types": ["error"], "count": 20} if role == "qa" else
                    {"action": "get_hierarchy", "page_size": 50, "cursor": 0})
            result = _content_value(await facade.call(name, args))
            self.job.event(role, "mcp_tool_used", tool=name)
            return {"tool": name, "observation": json.dumps(result, ensure_ascii=False)[:12000]}
        except Exception as error:
            self._mcp_unavailable = True
            self.job.event(role, "mcp_fallback", error=type(error).__name__)
            return {}
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as error:
                    self.job.event(role, "mcp_cleanup_failed", error=type(error).__name__)

    async def post(self, route, payload, role, model, ceiling):
        self.job.check_cancelled()
        await self.refresh_budget()
        key = self.provider.api_key()
        entry = self.ledger.reserve(role, model, ceiling)
        if self.hybrid:
            entry["task_id"] = self._active_tasks.get(role)
        self.job.event(role, "api_call", model=model, reserved_usd=ceiling)
        timeout = min(REQUEST_TIMEOUT_SECONDS, OPENROUTER_TIMEOUT_SECONDS) if self.provider.name == "openrouter" else REQUEST_TIMEOUT_SECONDS
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
                try:
                    response = await asyncio.wait_for(client.post(self.provider.base_url + "/" + route,
                        headers={"Authorization": "Bearer " + key}, json=payload), timeout=timeout)
                except asyncio.TimeoutError as error:
                    raise RequestDeadlineExceeded(
                        f"{self.provider.label} {role} request timed out after the {timeout:g}s "
                        "total deadline; provider keepalives cannot extend it") from error
                try:
                    data = response.json()
                except ValueError as error:
                    if response.is_success:
                        detail = provider_response_message(response)
                        raise ResponseInvalid(f"{self.provider.label} returned a non-JSON response: {detail[:250]}") from error
                    data = {}
                if not isinstance(data, dict):
                    raise ResponseInvalid(f"{self.provider.label} response must be an object")
                if not response.is_success or data.get("error"):
                    # Preserve provider diagnostics while redacting credentials.
                    provider_error = data.get("error")
                    code = provider_error.get("code") if isinstance(provider_error, dict) else None
                    status = (int(code) if response.is_success and str(code).isdigit()
                              else response.status_code)
                    if self.provider.name == "openrouter" and not data.get("choices"):
                        if isinstance(data.get("usage"), dict) and data["usage"].get("cost") is not None:
                            self.ledger.settle(entry, data["usage"])
                        elif not response.is_success and status in (400, 401, 402, 403, 404, 422, 429):
                            self.ledger.rejected(entry)
                    if entry["status"] == "pending":
                        entry["status"] = "failed_or_unknown_charge"
                        self.job.save()
                    message = provider_response_message(response)
                    if status == 401:
                        message = (self.provider.authentication_error() + " Provider response: " + message)
                    try:
                        retry_after = max(0, float(response.headers.get("Retry-After", "1")))
                    except ValueError:
                        retry_after = 1
                    request_id = response.headers.get("x-request-id") or response.headers.get("x-openrouter-request-id")
                    if request_id:
                        request_id = redact_provider_message(request_id)[:120]
                    rejection = ServiceRejected(status, f"{self.provider.label} error {status} for {model}: {message}",
                                          retry_after, provider_code=code, request_id=request_id,
                                          limit_source=(provider_error.get("metadata") or {}).get("limit_source")
                                          if isinstance(provider_error, dict) else None)
                    if self.provider.name == "openrouter" and route == "images" and credit_rejection(rejection):
                        self.registry.free_only = True
                        self.job.data.update(openrouter_free_only=True,
                            openrouter_restricted_key=getattr(self.registry, "_identity", None))
                        self.job.save()
                    raise rejection
            self.ledger.settle(entry, data.get("usage", {}))
            self.job.check_cancelled()
            return data
        except BaseException:
            if entry["status"] == "pending":
                entry["status"] = "failed_or_unknown_charge"
                self.job.save()
            raise

    async def json(self, role, system, value, max_tokens=5000):
        await self.refresh_budget()
        if (self.hybrid and self.job.data.get("openrouter_rate_limited_key") == getattr(self.registry, "_identity", None)
                and self.job.data.get("openrouter_account_retry_at", 0) > time.time()):
            raise ModelAttemptsFailed(self.provider.label, role, [],
                "OpenRouter reported an account-wide rate limit; saved progress is preserved until Retry-After.", fallback_blocked=True)
        from studio.model_selection import compact_context, complex_request
        complex_task = complex_request(value) if self.hybrid else False
        escalated = self.hybrid and bool(self.job.data.get("model_escalations", {}).get(role))
        if self.hybrid:
            await self.registry.refresh_benchmarks()
            self._active_tasks[role] = uuid4().hex
        # Frameworks keep using the same metered gateway. Observations are data,
        # never instructions, model-selected commands, or arbitrary MCP arguments.
        owner = {"ui": "designer", "scene": "developer"}.get(role, role)
        shared = self.job.data.get("shared_context")
        if shared:
            if self.hybrid:
                shared = compact_context(value, shared)
            value = {"task": value, "shared_game_context": shared,
                     "assigned_work": [item for item in self.job.data.get("work_items", []) if item["owner"] == owner]}
        unity = await self.unity_context(owner)
        if unity:
            value = {"task": value, "unity_tool_observation": unity}
        messages = [{"role": "system", "content": system + "\nReturn only valid JSON."},
                    {"role": "user", "content": json.dumps(value, ensure_ascii=False,
                        **({"separators": (",", ":")} if self.hybrid else {}))}]
        errors = []
        discovered = list(self.registry.candidates(role))
        policy = {"hybrid": True, "escalate": escalated, "complex_task": complex_task} if self.hybrid else {}
        candidates = list(self.registry.candidates(
            role, messages, max_tokens, float(self.ledger.request_budget(role)), **policy))
        if self.hybrid:
            self.job.event(role, "model_policy", **self.registry.value_decisions.get(role, {}))
        rejected = self.contract_failures.get(role, set())
        # A JSON envelope is not a valid game implementation. Try an unfailed
        # eligible model before asking the same model to correct itself again.
        alternatives = [model for model in candidates if model not in rejected]
        if alternatives:
            candidates = alternatives + [model for model in candidates if model in rejected]
            if rejected:
                self.job.event(role, "contract_model_fallback", rejected_models=sorted(rejected),
                               to_model=alternatives[0])
        if not candidates:
            if discovered:
                fits_request = [mid for mid in discovered if not hasattr(self.registry, "request_fits")
                                or self.registry.request_fits(mid, messages, max_tokens)]
                if fits_request:
                    quotes = [(self.registry.text_ceiling(mid, messages, max_tokens), mid)
                              for mid in fits_request]
                    cheapest, model = min(quotes)
                    raise BudgetExceeded(
                        f"No compatible model request fits the remaining ${float(self.ledger.remaining):.6f} "
                        f"job budget; the lowest estimated reservation is ${cheapest:.6f} for {model}.")
                limits = [{"model": mid, "kind": "request_limit",
                           "reason": "model context or maximum completion tokens are below this request's requirements"}
                          for mid in discovered]
                raise ModelAttemptsFailed(self.provider.label, role, limits,
                    "Models are present in the provider catalog, but none meet this request's context and output limits.")
            config = "DAHL_MODEL_* in .env" if self.provider.name == "dahl" else "studio_models.json"
            raise ModelAttemptsFailed(self.provider.label, role, [],
                f"No text-only JSON model is eligible for {role} from the selected provider/account. "
                f"Check {config}, model access and output capabilities, then run main.py models.")
        free_only = bool(getattr(self.registry, "free_only", False)) if self.provider.name == "openrouter" else False
        previous_model = None
        last_error = ""
        model_index = 0
        budget_blocked = False
        while model_index < len(candidates):
            model = candidates[model_index]
            model_index += 1
            if free_only and not (self.registry._is_free(model) if hasattr(self.registry, "_is_free") else model.endswith(":free")):
                continue
            if previous_model:
                self.job.event(role, "model_fallback", provider=self.provider.name,
                               from_model=previous_model, to_model=model, reason=last_error[:1000])
            previous_model = model
            prompt_price, completion_price = self.registry.rates(model)
            request_max_tokens = max_tokens
            reasoning_enabled = False
            reasoning_retry_used = False
            transient_retry_used = False
            token_retry_used = False
            truncated_retry_used = False
            json_retry_used = False
            for _attempt in range(3):
                await self.refresh_budget()
                if self.provider.name == "openrouter" and hasattr(self.registry, "output_limit"):
                    from studio.registry import minimum_output
                    fitted = self.registry.output_limit(model, messages, request_max_tokens,
                        float(self.ledger.request_budget(role)), min_tokens=minimum_output(role, request_max_tokens))
                    if fitted is None:
                        budget_blocked = True
                        last_error = "model cannot fit its minimum useful output within the effective allowance"
                        errors.append({"model": model, "kind": "budget_limit", "reason": last_error})
                        break
                    request_max_tokens = fitted
                if hasattr(self.registry, "request_fits") and not self.registry.request_fits(model, messages, request_max_tokens):
                    detail = "context or output limit is too small for this request"
                    errors.append({"model": model, "kind": "request_limit", "reason": detail})
                    last_error = detail
                    self.job.event(role, "model_failed", provider=self.provider.name, model=model,
                                   error=detail, kind="request_limit")
                    break
                ceiling = self.registry.text_ceiling(model, messages, request_max_tokens)
                if not self.ledger.can_reserve(ceiling):
                    detail = "request reservation does not fit the remaining job budget"
                    errors.append({"model": model, "kind": "budget_limit", "reason": detail})
                    last_error = detail
                    budget_blocked = True
                    self.job.event(role, "model_skipped", provider=self.provider.name, model=model,
                                   reason=detail, reserved_usd=ceiling,
                                   remaining_usd=float(self.ledger.remaining))
                    break
                payload = {
                        "model": model, "messages": messages, "max_tokens": request_max_tokens,
                        "response_format": {"type": "json_object"},
                    }
                if model in getattr(self.registry, "json_disabled", set()):
                    payload.pop("response_format", None)
                supported = self.registry.models.get(model, {}).get("supported_parameters")
                if supported is None or "temperature" in supported:
                    payload["temperature"] = 0.2
                if supported and "max_completion_tokens" in supported and "max_tokens" not in supported:
                    payload["max_completion_tokens"] = payload.pop("max_tokens")
                if self.provider.name == "openrouter":
                    payload["provider"] = {"require_parameters": True, "max_price": {
                            "prompt": prompt_price * 1_000_000, "completion": completion_price * 1_000_000}}
                # Coding work can use catalog-advertised low-effort reasoning;
                # format-only roles keep optional thinking off. All thinking
                # still consumes the same reserved output/token budget.
                if self.provider.name == "openrouter" and (reasoning_enabled or
                        "reasoning" in self.registry.models.get(model, {}).get("supported_parameters", [])):
                    metadata = self.registry.models.get(model, {}).get("reasoning") or {}
                    efforts = metadata.get("supported_efforts") or []
                    requested_effort = os.environ.get("STUDIO_REASONING_EFFORT", "low")
                    if requested_effort in efforts and (role in ("developer", "scene", "qa") or metadata.get("mandatory")):
                        payload["reasoning"] = {"effort": requested_effort}
                    elif metadata.get("mandatory"):
                        payload["reasoning"] = {"enabled": True}
                    else:
                        payload["reasoning"] = {"enabled": reasoning_enabled}
                if self.hybrid:
                    for parameter in self.registry.unsupported_parameters.get(model, set()):
                        payload.pop(parameter, None)
                try:
                    from studio.model_selection import capabilities
                    self.job.event(role, "model_selected", provider=self.provider.name, model=model,
                                   capabilities=capabilities(self.registry.models.get(model, {})),
                                   prompt_usd_per_million=prompt_price * 1_000_000,
                                   completion_usd_per_million=completion_price * 1_000_000)
                    data = await self.post("chat/completions", payload, role, model, ceiling)
                    result = decode_json_response(data)
                    self.selected_models[role] = model
                    if self.hybrid:
                        self.selected_tasks[role] = self._active_tasks[role]
                    if hasattr(self.registry, "record_outcome"):
                        self.registry.record_outcome(model, role, True, kind="availability")
                    self.job.event(role, "model_response_validated", provider=self.provider.name, model=model)
                    return result
                except BudgetExceeded as error:
                    if self.provider.name != "openrouter" or self.job.data.get("budget_halted"):
                        raise
                    budget_blocked = True
                    last_error = str(error)
                    errors.append({"model": model, "kind": "budget_limit", "reason": last_error})
                    break
                except JobCancelled:
                    raise
                except Exception as error:
                    if isinstance(error, (ServiceRejected, ResponseInvalid)):
                        detail = str(error)
                    elif isinstance(error, httpx.HTTPError):
                        detail = f"{type(error).__name__}: {redact_provider_message(error)}"
                    else:
                        detail = f"{type(error).__name__}: {redact_provider_message(error)}"
                    kind = ("provider_rejection" if isinstance(error, ServiceRejected) else
                            "invalid_response" if isinstance(error, ResponseInvalid) else
                            "transport_error" if isinstance(error, httpx.HTTPError) else "request_error")
                    attempt = {"model": model, "kind": kind, "reason": detail[:1000]}
                    if isinstance(error, ServiceRejected):
                        attempt["status"] = error.status
                        if error.provider_code is not None:
                            attempt["provider_code"] = str(error.provider_code)[:100]
                        if error.request_id:
                            attempt["request_id"] = error.request_id
                    errors.append(attempt)
                    last_error = detail
                    self.job.event(role, "model_failed", provider=self.provider.name, model=model,
                                   error=detail[:1000], kind=kind,
                                   status=getattr(error, "status", None),
                                   request_id=getattr(error, "request_id", None))
                    excluded_availability = (isinstance(error, ServiceRejected) and
                        (error.status in (401, 402) or
                         self.provider.name == "openrouter" and credit_rejection(error) or
                         credential_rejection(error, self.provider.name == "openrouter")))
                    if (self.provider.name == "openrouter" and isinstance(error, ServiceRejected)
                            and error.status in (400, 422) and not json_retry_used
                            and "response_format" in payload
                            and any(marker in str(error).casefold() for marker in ("response_format", "json mode", "json_object"))):
                        json_retry_used = True
                        self.registry.json_disabled.add(model)
                        self.job.event(role, "json_mode_retry", model=model, response_format=False)
                        continue
                    if (self.hybrid and isinstance(error, ServiceRejected) and error.status in (400, 422)
                            and any(s in str(error).casefold() for s in ("unsupported", "not supported", "unrecognized", "unknown parameter"))):
                        optional = [p for p in ("temperature", "reasoning") if p in payload
                                    and re.search(r"\b" + p + r"\b", str(error), re.I)]
                        if optional:
                            self.registry.unsupported_parameters.setdefault(model, set()).update(optional)
                            self.job.event(role, "optional_parameter_retry", model=model, removed=optional)
                            continue
                    if (self.hybrid and isinstance(error, ServiceRejected) and error.status == 429
                            and (error.limit_source in ("openrouter_account", "openrouter_key", "account", "key")
                                 or "account-wide" in str(error).casefold() or "key-wide" in str(error).casefold())):
                        self.job.data.update(openrouter_rate_limited_key=getattr(self.registry, "_identity", None),
                            openrouter_account_retry_at=time.time() + max(1, error.retry_after or 60))
                        self.job.save()
                        raise ModelAttemptsFailed(self.provider.label, role, errors,
                            "OpenRouter reported an account-wide rate limit; changing models cannot resolve this limit.",
                            fallback_blocked=True) from error
                    if hasattr(self.registry, "record_outcome") and not excluded_availability:
                        self.registry.record_outcome(model, role, False,
                            kind="contract" if isinstance(error, ResponseInvalid) else "availability")
                    if self.hybrid and isinstance(error, ResponseInvalid):
                        self.job.data.setdefault("model_escalations", {})[role] = "invalid_json_or_output_limit"
                        alternatives = list(self.registry.candidates(role, messages, max_tokens,
                            float(self.ledger.request_budget(role)), hybrid=True, escalate=True, complex_task=complex_task))
                        tried = candidates[:model_index]
                        candidates = tried + [mid for mid in alternatives if mid not in tried][:max(0, 4 - model_index)]
                        self.job.event(role, "model_escalation", from_model=model,
                            trigger="invalid_json_or_output_limit", **self.registry.value_decisions.get(role, {}))
                    if (not truncated_retry_used and isinstance(error, ResponseInvalid)
                            and error.truncated and request_max_tokens < 32000):
                        # Reasoning consumes output tokens too. Retry a complete
                        # contract once, under a new reservation and the same cap.
                        truncated_retry_used = True
                        grown = min(32000, request_max_tokens * 2)
                        if self.hybrid:
                            grown = self.registry.output_limit(model, messages, grown,
                                float(self.ledger.request_budget(role))) or 0
                        if grown > request_max_tokens:
                            request_max_tokens = grown
                            self.job.event(role, "output_limit_retry", model=model, retry_tokens=request_max_tokens)
                            continue
                    if (self.provider.name == "openrouter" and not reasoning_retry_used
                            and isinstance(error, ServiceRejected) and error.status == 400
                            and any(marker in str(error).casefold() for marker in (
                                "reasoning is mandatory", "reasoning is required", "reasoning cannot be disabled",
                                "does not support disabling reasoning"))):
                        # Some OpenRouter endpoints advertise reasoning support
                        # but reject disabling it. Adapt only to this explicit
                        # provider response and reserve the retry independently.
                        reasoning_enabled = reasoning_retry_used = True
                        self.job.event(role, "reasoning_mode_retry", model=model, enabled=True)
                        continue
                    reduced_limit = (affordable_retry_limit(error, request_max_tokens)
                                     if self.provider.name == "openrouter" and not token_retry_used else None)
                    if reduced_limit is not None:
                        token_retry_used = True
                        self.job.event(role, "token_limit_retry", model=model,
                                       requested_tokens=request_max_tokens, retry_tokens=reduced_limit)
                        request_max_tokens = reduced_limit
                        continue
                    if credential_rejection(error, self.provider.name == "openrouter"):
                        raise ModelAttemptsFailed(self.provider.label, role, errors,
                            "The provider rejected credentials or account access; model switching cannot fix this.",
                            fallback_blocked=True) from error
                    if isinstance(error, ServiceRejected) and error.status == 402 and self.provider.name != "openrouter":
                        raise ModelAttemptsFailed(self.provider.label, role, errors,
                            "The selected provider rejected the request for billing or credit reasons.",
                            fallback_blocked=True) from error
                    in_flight = (self.provider.name == "openrouter" and isinstance(error, ServiceRejected) and
                                 error.limit_source == "openrouter_in_flight_budget")
                    if self.provider.name == "openrouter" and credit_rejection(error) and not in_flight:
                        await self.refresh_budget(force=True)
                        # A credit rejection may still be recoverable through an eligible free model.
                        previous_free_only = getattr(self.registry, "free_only", False)
                        self.registry.free_only = True
                        free_models = [mid for mid in self.registry.candidates(
                            role, messages, request_max_tokens, float(self.ledger.remaining))
                                       if (self.registry._is_free(mid) if hasattr(self.registry, "_is_free") else mid.endswith(":free"))]
                        self.registry.free_only = previous_free_only
                        if free_models:
                            free_only = True
                            self.job.data["openrouter_free_only"] = True
                            self.job.data["openrouter_restricted_key"] = getattr(self.registry, "_identity", None)
                            self.registry.free_only = True
                            self.job.save()
                            for fallback in free_models:
                                if fallback not in candidates:
                                    candidates.append(fallback)
                        else:
                            raise ModelAttemptsFailed(self.provider.label, role, errors,
                                "Available key credits cannot cover a suitable model; saved assets and stages are preserved.",
                                fallback_blocked=True) from error
                    transient = isinstance(error, ServiceRejected) and error.status in (408, 429, 500, 502, 503, 504)
                    transient = transient or in_flight
                    # Every retry gets a fresh persisted reservation. Unknown charges stay reserved.
                    delay = getattr(error, "retry_after", None) or 1
                    if (not transient_retry_used and not isinstance(error, RequestDeadlineExceeded)
                            and (transient or isinstance(error, httpx.TransportError))
                            and not (self.hybrid and (isinstance(error, httpx.TimeoutException) or
                                 isinstance(error, ServiceRejected) and error.status == 429))
                            and delay <= 10):
                        transient_retry_used = True
                        self.job.check_cancelled()
                        await asyncio.sleep(delay)
                        continue
                    if in_flight:
                        raise ModelAttemptsFailed(self.provider.label, role, errors,
                            "OpenRouter has pending requests against its in-flight budget; saved progress is preserved.",
                            fallback_blocked=True) from error
                    if (self.provider.name == "openrouter" and hasattr(self.registry, "unavailable")
                            and not credit_rejection(error)):
                        self.registry.block_model(model)
                        self.job.data["openrouter_model_blocks"] = {
                            "identity": getattr(self.registry, "_identity", None),
                            "until": dict(self.registry.unavailable_until)}
                        self.job.save()
                    break
        if budget_blocked:
            details = "; ".join(f"{item['model']}: {item['reason']}" for item in errors)
            raise BudgetExceeded("No compatible model request fits the remaining job budget. "
                                 "Model/API attempts: " + details)
        raise ModelAttemptsFailed(self.provider.label, role, errors) if errors else ModelAttemptsFailed(
            self.provider.label, role, [], "No eligible model remained after applying provider and pricing restrictions.")
