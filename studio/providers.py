"""Provider settings shared by model discovery and the existing metered client."""
import os
import re

from studio import openrouter


def redact_provider_message(message):
    message = str(message)
    for key in ("OPENROUTER_API_KEY", "DAHL_API_KEY", "TELEGRAM_BOT_TOKEN"):
        secret = os.environ.get(key, "").strip()
        if secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", message)
    return message[:1000]


def provider_response_message(response):
    try:
        data = response.json()
    except ValueError:
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("type")
        code = error.get("code")
        error_type = error.get("type")
        metadata = []
        if code is not None:
            metadata.append(f"provider code: {code}")
        if error_type and error_type != message:
            metadata.append(f"type: {error_type}")
        if metadata:
            message = f"{message or 'provider rejected request'} ({'; '.join(metadata)})"
    elif isinstance(error, str):
        message = error
    else:
        message = ""
    if not message:
        message = response.text.strip()[:400] or "empty response body"
    return redact_provider_message(message)


class AIProvider:
    def __init__(self):
        self.name = os.environ.get("AI_PROVIDER", "openrouter").strip().lower()
        if self.name not in ("openrouter", "dahl"):
            raise ValueError("AI_PROVIDER must be openrouter or dahl")
        self.label = "OpenRouter" if self.name == "openrouter" else "Dahl"
        self.base_url = ("https://openrouter.ai/api/v1" if self.name == "openrouter" else
                         os.environ.get("DAHL_BASE_URL", "https://inference.dahl.global/v1").strip().rstrip("/"))

    def api_key(self):
        if self.name == "openrouter":
            return openrouter.api_key()
        key = os.environ.get("DAHL_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Set DAHL_API_KEY in .env, then restart the bot.")
        return key

    def authentication_error(self):
        if self.name == "openrouter":
            return openrouter.authentication_error()
        return ("Dahl authentication failed (401): the active DAHL_API_KEY was rejected. "
                "Check your Dahl inference key in .env, then restart the bot. "
                "Changing models cannot fix a rejected key.")
