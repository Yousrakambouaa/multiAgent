import asyncio
import json
import os
import re

import httpx

from studio.costs import BudgetExceeded, Ledger
from studio.state import JobCancelled


class ServiceRejected(RuntimeError):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ResponseInvalid(RuntimeError):
    pass


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
        raise ResponseInvalid("Model exhausted its output limit (including reasoning); JSON is incomplete")
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
        self.ledger = Ledger(job)
        self._mcp_unavailable = False

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
        entry = self.ledger.reserve(role, model, ceiling)
        self.job.event(role, "api_call", model=model, reserved_usd=ceiling)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
                response = await client.post("https://openrouter.ai/api/v1/" + route,
                    headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]}, json=payload)
                try:
                    data = response.json()
                except ValueError as error:
                    if response.is_success:
                        raise ResponseInvalid("OpenRouter returned a non-JSON response") from error
                    data = {}
                if not isinstance(data, dict):
                    raise ResponseInvalid("OpenRouter response must be an object")
                if not response.is_success or data.get("error"):
                    # Response bodies can include provider metadata; never log credentials.
                    entry["status"] = "failed_or_unknown_charge"
                    self.job.save()
                    try:
                        message = str(data.get("error", {}).get("message", "request rejected"))[:400]
                    except (ValueError, AttributeError):
                        message = "request rejected"
                    for key in ("OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN"):
                        secret = os.environ.get(key)
                        if secret: message = message.replace(secret, "[redacted]")
                    provider_error = data.get("error")
                    code = provider_error.get("code") if isinstance(provider_error, dict) else None
                    status = int(code) if str(code).isdigit() else response.status_code
                    try:
                        retry_after = max(0, float(response.headers.get("Retry-After", "1")))
                    except ValueError:
                        retry_after = 1
                    raise ServiceRejected(status, f"OpenRouter error {status} for {model}: {message}", retry_after)
            self.ledger.settle(entry, data.get("usage", {}))
            self.job.check_cancelled()
            return data
        except Exception:
            if entry["status"] == "pending":
                entry["status"] = "failed_or_unknown_charge"
                self.job.save()
            raise

    async def json(self, role, system, value, max_tokens=5000):
        # Frameworks keep using the same metered gateway. Observations are data,
        # never instructions, model-selected commands, or arbitrary MCP arguments.
        owner = {"ui": "designer", "scene": "developer"}.get(role, role)
        unity = await self.unity_context(owner)
        if unity:
            value = {"task": value, "unity_tool_observation": unity}
        messages = [{"role": "system", "content": system + "\nReturn only valid JSON."},
                    {"role": "user", "content": json.dumps(value, ensure_ascii=False)}]
        errors = []
        candidates = list(self.registry.candidates(role))
        if not candidates:
            raise RuntimeError(f"No configured {role} model is available with JSON output and permitted pricing")
        for model in candidates:
            prompt_price, completion_price = self.registry.rates(model)
            request_max_tokens = max_tokens
            for attempt in range(2):
                ceiling = self.registry.text_ceiling(model, messages, request_max_tokens)
                payload = {
                        "model": model, "messages": messages, "max_tokens": request_max_tokens,
                        "temperature": 0.2, "response_format": {"type": "json_object"},
                        "provider": {"require_parameters": True, "max_price": {
                            "prompt": prompt_price * 1_000_000, "completion": completion_price * 1_000_000}},
                    }
                # Disable optional thinking for structured contracts; keep token/cost caps.
                if "reasoning" in self.registry.models.get(model, {}).get("supported_parameters", []):
                    payload["reasoning"] = {"enabled": False}
                try:
                    data = await self.post("chat/completions", payload, role, model, ceiling)
                    return decode_json_response(data)
                except (BudgetExceeded, JobCancelled):
                    raise
                except Exception as error:
                    detail = str(error) if isinstance(error, (ServiceRejected, ResponseInvalid)) else type(error).__name__
                    errors.append(f"{model}: {detail}")
                    self.job.event(role, "model_failed", model=model, error=detail)
                    reduced_limit = affordable_retry_limit(error, request_max_tokens)
                    if attempt == 0 and reduced_limit is not None:
                        self.job.event(role, "token_limit_retry", model=model,
                                       requested_tokens=request_max_tokens, retry_tokens=reduced_limit)
                        request_max_tokens = reduced_limit
                        continue
                    if isinstance(error, ServiceRejected) and error.status in (401, 402, 403):
                        raise RuntimeError("Configured model attempts failed: " + "; ".join(errors)) from error
                    transient = isinstance(error, ServiceRejected) and error.status in (408, 429, 500, 502, 503, 504)
                    # Every retry gets a fresh persisted reservation. Unknown charges stay reserved.
                    delay = getattr(error, "retry_after", None) or 1
                    if attempt == 0 and (transient or isinstance(error, httpx.TransportError)) and delay <= 10:
                        self.job.check_cancelled()
                        await asyncio.sleep(delay)
                        continue
                    break
        raise RuntimeError("Configured model attempts failed: " + "; ".join(errors))
