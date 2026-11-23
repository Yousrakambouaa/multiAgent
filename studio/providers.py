"""Provider settings shared by model discovery and the existing metered client."""
import os

from studio import openrouter


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
