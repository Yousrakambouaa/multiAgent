"""Credential handling shared by discovery and metered inference."""
import os
from decimal import Decimal, InvalidOperation


def _money(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def key_allowance(metadata, credits=None):
    """Return only documented monetary bounds; unlimited is not a balance."""
    key_bounds, account_bounds = [], []
    remaining = _money(metadata.get("limit_remaining"))
    if remaining is None:
        limit = _money(metadata.get("limit"))
        period = metadata.get("limit_reset")
        usage = _money(metadata.get({"daily": "usage_daily", "weekly": "usage_weekly",
                                     "monthly": "usage_monthly"}.get(period, "usage")))
        if limit is not None and usage is not None:
            remaining = max(Decimal(0), limit - usage)
    if remaining is not None:
        key_bounds.append(remaining)
    for budget in [metadata.get("effective_budget"), *(metadata.get("budgets") or [])]:
        if isinstance(budget, dict):
            bound = _money(budget.get("remaining_usd"))
            if bound is not None:
                (account_bounds if budget.get("scope") in ("workspace", "organization") else key_bounds).append(bound)
    if isinstance(credits, dict):
        total, used = _money(credits.get("total_credits")), _money(credits.get("total_usage"))
        if total is not None and used is not None:
            account_bounds.append(max(Decimal(0), total - used))
    bounds = key_bounds + account_bounds
    return {"remaining_usd": float(min(bounds)) if bounds else None,
            "key_remaining_usd": float(min(key_bounds)) if key_bounds else None,
            "account_remaining_usd": float(min(account_bounds)) if account_bounds else None,
            "status": "reported_bound" if bounds else "unknown",
            "free_tier": metadata.get("is_free_tier") is True}


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
