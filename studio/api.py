import json
import os

import httpx

from studio.costs import BudgetExceeded, Ledger
from studio.state import JobCancelled


class ServiceRejected(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class ModelService:
    def __init__(self, registry, job):
        self.registry, self.job = registry, job
        self.ledger = Ledger(job)

    async def post(self, route, payload, role, model, ceiling):
        self.job.check_cancelled()
        entry = self.ledger.reserve(role, model, ceiling)
        self.job.event(role, "api_call", model=model, reserved_usd=ceiling)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20)) as client:
                response = await client.post("https://openrouter.ai/api/v1/" + route,
                    headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]}, json=payload)
                if not response.is_success:
                    # Response bodies can include provider metadata; never log credentials.
                    entry["status"] = "failed_or_unknown_charge"
                    self.job.save()
                    try:
                        message = str(response.json().get("error", {}).get("message", "request rejected"))[:400]
                    except (ValueError, AttributeError):
                        message = "request rejected"
                    for key in ("OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN"):
                        secret = os.environ.get(key)
                        if secret: message = message.replace(secret, "[redacted]")
                    raise ServiceRejected(response.status_code, f"OpenRouter HTTP {response.status_code} for {model}: {message}")
                data = response.json()
            self.ledger.settle(entry, data.get("usage", {}))
            self.job.check_cancelled()
            return data
        except Exception:
            if entry["status"] == "pending":
                entry["status"] = "failed_or_unknown_charge"
                self.job.save()
            raise

    async def json(self, role, system, value, max_tokens=5000):
        messages = [{"role": "system", "content": system + "\nReturn only valid JSON."},
                    {"role": "user", "content": json.dumps(value, ensure_ascii=False)}]
        errors = []
        for model in self.registry.candidates(role):
            ceiling = self.registry.text_ceiling(model, messages, max_tokens)
            prompt_price, completion_price = self.registry.rates(model)
            try:
                data = await self.post("chat/completions", {
                    "model": model, "messages": messages, "max_tokens": max_tokens,
                    "temperature": 0.2, "response_format": {"type": "json_object"},
                    "provider": {"require_parameters": True, "max_price": {
                        "prompt": prompt_price * 1_000_000, "completion": completion_price * 1_000_000}},
                }, role, model, ceiling)
                return json.loads(data["choices"][0]["message"]["content"])
            except (BudgetExceeded, JobCancelled):
                raise
            except Exception as error:
                detail=str(error) if isinstance(error,ServiceRejected) else type(error).__name__
                errors.append(f"{model}: {detail}")
                self.job.event(role, "model_failed", model=model, error=detail)
                if isinstance(error,ServiceRejected) and error.status in (401,402,403):
                    break
        raise RuntimeError("Configured model attempts failed: " + "; ".join(errors))
