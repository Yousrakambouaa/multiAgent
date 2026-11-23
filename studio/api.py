import asyncio
import json
import os
import re

import httpx

from studio.costs import BudgetExceeded, Ledger
from studio.state import JobCancelled
from studio.providers import AIProvider


class ServiceRejected(RuntimeError):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ResponseInvalid(RuntimeError):
    def __init__(self, message, truncated=False):
        super().__init__(message)
        self.truncated = truncated


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
        if self.provider.name == "openrouter" and job.data.get("openrouter_free_only"):
            registry.free_only = True
        self.ledger = Ledger(job)
        self._mcp_unavailable = False
        self.selected_models = {}

    def record_validation(self, role, passed):
        model = self.selected_models.get(role)
        if model and hasattr(self.registry, "record_outcome"):
            self.registry.record_outcome(model, role, passed, kind="unity")
            self.job.event(role, "model_outcome", provider=self.provider.name, model=model,
                           kind="unity", passed=bool(passed))

    def record_contract_failure(self, role):
        model = self.selected_models.get(role)
        if model and hasattr(self.registry, "record_outcome"):
            self.registry.record_outcome(model, role, False, kind="contract")
            self.job.event(role, "model_outcome", provider=self.provider.name, model=model,
                           kind="contract", passed=False)

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
        key = self.provider.api_key()
        entry = self.ledger.reserve(role, model, ceiling)
        self.job.event(role, "api_call", model=model, reserved_usd=ceiling)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
                response = await client.post(self.provider.base_url + "/" + route,
                    headers={"Authorization": "Bearer " + key}, json=payload)
                try:
                    data = response.json()
                except ValueError as error:
                    if response.is_success:
                        raise ResponseInvalid(f"{self.provider.label} returned a non-JSON response") from error
                    data = {}
                if not isinstance(data, dict):
                    raise ResponseInvalid(f"{self.provider.label} response must be an object")
                if not response.is_success or data.get("error"):
                    # Response bodies can include provider metadata; never log credentials.
                    entry["status"] = "failed_or_unknown_charge"
                    self.job.save()
                    try:
                        message = str(data.get("error", {}).get("message", "request rejected"))[:400]
                    except (ValueError, AttributeError):
                        message = "request rejected"
                    for key in ("OPENROUTER_API_KEY", "DAHL_API_KEY", "TELEGRAM_BOT_TOKEN"):
                        secret = os.environ.get(key)
                        if secret:
                            message = message.replace(secret, "[redacted]").replace(secret.strip(), "[redacted]")
                    provider_error = data.get("error")
                    code = provider_error.get("code") if isinstance(provider_error, dict) else None
                    status = int(code) if str(code).isdigit() else response.status_code
                    if status == 401:
                        message = self.provider.authentication_error()
                    try:
                        retry_after = max(0, float(response.headers.get("Retry-After", "1")))
                    except ValueError:
                        retry_after = 1
                    raise ServiceRejected(status, f"{self.provider.label} error {status} for {model}: {message}", retry_after)
            self.ledger.settle(entry, data.get("usage", {}))
            self.job.check_cancelled()
            return data
        except BaseException:
            if entry["status"] == "pending":
                entry["status"] = "failed_or_unknown_charge"
                self.job.save()
            raise

    async def json(self, role, system, value, max_tokens=5000):
        # Frameworks keep using the same metered gateway. Observations are data,
        # never instructions, model-selected commands, or arbitrary MCP arguments.
        owner = {"ui": "designer", "scene": "developer"}.get(role, role)
        shared = self.job.data.get("shared_context")
        if shared:
            value = {"task": value, "shared_game_context": shared,
                     "assigned_work": [item for item in self.job.data.get("work_items", []) if item["owner"] == owner]}
        unity = await self.unity_context(owner)
        if unity:
            value = {"task": value, "unity_tool_observation": unity}
        messages = [{"role": "system", "content": system + "\nReturn only valid JSON."},
                    {"role": "user", "content": json.dumps(value, ensure_ascii=False)}]
        errors = []
        candidates = list(self.registry.candidates(role))
        if not candidates:
            config = "DAHL_MODEL_* in .env" if self.provider.name == "dahl" else "studio_models.json"
            raise RuntimeError(f"No configured {role} model or automatic fallback is available with JSON output "
                               f"and permitted pricing. Check {config} and run main.py models.")
        free_only = bool(self.job.data.get("openrouter_free_only"))
        for model in candidates:
            if free_only and not model.endswith(":free"):
                continue
            prompt_price, completion_price = self.registry.rates(model)
            request_max_tokens = max_tokens
            reasoning_enabled = False
            reasoning_retry_used = False
            transient_retry_used = False
            token_retry_used = False
            truncated_retry_used = False
            for _attempt in range(3):
                if hasattr(self.registry, "request_fits") and not self.registry.request_fits(model, messages, request_max_tokens):
                    errors.append(f"{model}: context or output limit is too small for this task")
                    break
                ceiling = self.registry.text_ceiling(model, messages, request_max_tokens)
                payload = {
                        "model": model, "messages": messages, "max_tokens": request_max_tokens,
                        "temperature": 0.2, "response_format": {"type": "json_object"},
                    }
                if self.provider.name == "openrouter":
                    payload["provider"] = {"require_parameters": True, "max_price": {
                            "prompt": prompt_price * 1_000_000, "completion": completion_price * 1_000_000}}
                # Disable optional thinking for structured contracts; keep token/cost caps.
                if self.provider.name == "openrouter" and (reasoning_enabled or
                        "reasoning" in self.registry.models.get(model, {}).get("supported_parameters", [])):
                    payload["reasoning"] = {"enabled": reasoning_enabled}
                try:
                    from studio.model_selection import capabilities
                    self.job.event(role, "model_selected", provider=self.provider.name, model=model,
                                   capabilities=capabilities(self.registry.models.get(model, {})),
                                   prompt_usd_per_million=prompt_price * 1_000_000,
                                   completion_usd_per_million=completion_price * 1_000_000)
                    data = await self.post("chat/completions", payload, role, model, ceiling)
                    result = decode_json_response(data)
                    self.selected_models[role] = model
                    if hasattr(self.registry, "record_outcome"):
                        self.registry.record_outcome(model, role, True)
                    self.job.event(role, "model_response_validated", provider=self.provider.name, model=model)
                    return result
                except (BudgetExceeded, JobCancelled):
                    raise
                except Exception as error:
                    detail = str(error) if isinstance(error, (ServiceRejected, ResponseInvalid)) else type(error).__name__
                    errors.append(f"{model}: {detail}")
                    self.job.event(role, "model_failed", provider=self.provider.name, model=model, error=detail)
                    if hasattr(self.registry, "record_outcome") and not (isinstance(error, ServiceRejected) and error.status in (401, 402, 403)):
                        self.registry.record_outcome(model, role, False,
                            kind="contract" if isinstance(error, ResponseInvalid) else "availability")
                    if (self.provider.name == "dahl" and not truncated_retry_used and isinstance(error, ResponseInvalid)
                            and error.truncated and request_max_tokens < 32000):
                        # Reasoning consumes output tokens too. Retry a complete
                        # contract once, under a new reservation and the same cap.
                        truncated_retry_used = True
                        request_max_tokens = min(32000, request_max_tokens * 2)
                        self.job.event(role, "output_limit_retry", model=model, retry_tokens=request_max_tokens)
                        continue
                    if (self.provider.name == "openrouter" and not reasoning_retry_used
                            and isinstance(error, ServiceRejected) and error.status == 400
                            and "reasoning is mandatory" in str(error).casefold()):
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
                    if self.provider.name == "openrouter" and isinstance(error, ServiceRejected) and error.status == 402:
                        if reduced_limit is not None:
                            # A cheaper configured/discovered model may fit even
                            # after this model's reduced-token request was rejected.
                            break
                        if any(mid.endswith(":free") for mid in candidates[candidates.index(model) + 1:]):
                            free_only = True
                            self.job.data["openrouter_free_only"] = True
                            self.registry.free_only = True
                            self.job.save()
                            break
                    if isinstance(error, ServiceRejected) and error.status in (401, 402, 403):
                        raise RuntimeError("Configured model attempts failed: " + "; ".join(errors)) from error
                    transient = isinstance(error, ServiceRejected) and error.status in (408, 429, 500, 502, 503, 504)
                    # Every retry gets a fresh persisted reservation. Unknown charges stay reserved.
                    delay = getattr(error, "retry_after", None) or 1
                    if (not transient_retry_used and (transient or isinstance(error, httpx.TransportError))
                            and delay <= 10):
                        transient_retry_used = True
                        self.job.check_cancelled()
                        await asyncio.sleep(delay)
                        continue
                    break
        raise RuntimeError("Configured model attempts failed: " + "; ".join(errors))
