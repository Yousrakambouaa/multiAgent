"""Credential handling shared by discovery and metered inference."""
import os


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY in .env, then restart the bot.")
    return key


def authentication_error():
    return ("OpenRouter authentication failed (401): the active OPENROUTER_API_KEY was rejected. "
            "'User not found' means OpenRouter does not recognize the credential. "
            "Check or replace it at https://openrouter.ai/settings/keys, ensure it is an OpenRouter key, "
            "then restart the bot. An existing environment variable overrides .env. "
            "Changing models cannot fix a rejected key.")


def validate_inference_key(metadata):
    """Account/key endpoints accept admin keys that cannot run completions."""
    if not isinstance(metadata, dict):
        raise RuntimeError("OpenRouter key lookup returned invalid key metadata")
    if metadata.get("is_management_key") is True or metadata.get("is_provisioning_key") is True:
        raise RuntimeError(
            "OPENROUTER_API_KEY is a management/provisioning key. OpenRouter accepts it for "
            "administration, but it cannot call any model, including paid models. "
            "Use a regular inference API key from the supervisor's account at "
            "https://openrouter.ai/settings/keys, set it in .env, then restart the bot. "
            "No generation request was made. Changing models will not enable inference for this key type.")
