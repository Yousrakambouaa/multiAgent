"""Rank live capabilities and observed task outcomes, never marketing model names."""
import math
from statistics import median


def benchmark_signal(rows, role):
    """Broad external indices are advisory priors, not Unity pass rates."""
    fields = (("engineering_index", "intelligence_index") if role in ("developer", "scene", "qa")
              else ("intelligence_index", "decision_index"))
    for field in fields:
        values = [r[field] / 100 for r in rows if r.get("source") == "artificial-analysis"
                  and isinstance(r.get(field), (int, float)) and math.isfinite(r[field]) and 0 <= r[field] <= 100]
        if values:
            return max(values), "Artificial Analysis via OpenRouter: " + field
    return .5, "unknown; neutral prior"


def measured_quality(records, prior=.5):
    weights = {"unity": 4, "contract": 2, "availability": .25, "usable_image": 4}
    selected = [r for r in records[-100:] if r.get("kind") in weights]
    total = sum(weights[r["kind"]] for r in selected)
    passed = sum(weights[r["kind"]] for r in selected if r.get("passed"))
    # Local checks quickly outweigh broad benchmark priors. An API envelope
    # alone is deliberately weak evidence of a usable game implementation.
    return (3 * prior + passed) / (3 + total)


def value_ranking(options, role, evidence, benchmarks, preferences, *, escalate=False, complex_task=False):
    """Expected request + recovery cost; quotes are ceilings, not actual bills."""
    paid = sorted(o["quoted_usd"] for o in options if o["quoted_usd"] > 0)
    recovery_quote = median(paid) if paid else 0
    economy_limit = min(recovery_quote, paid[(len(paid) - 1) // 4] * 3) if paid else 0
    ranked = []
    for option in options:
        mid = option["model"]
        prior, source = benchmark_signal(benchmarks.get(mid, []), role)
        records = evidence.get(mid, {}).get(role, [])[-100:]
        quality = measured_quality(records, prior)
        checked = [r for r in records if r.get("kind") in ("unity", "contract", "usable_image")]
        poor = len(checked) >= 3 and sum(bool(r.get("passed")) for r in checked) / len(checked) < .5
        preference = preferences.index(mid) if mid in preferences else len(preferences)
        # Free-but-unreliable can cost more once a repair is included. This
        # prior-based estimate is reported explicitly, never as a benchmark win.
        score = (option["quoted_usd"] + 2 * (1 - quality) * recovery_quote)
        score *= 1 if mid not in preferences else .95
        score *= option.get("requested_tokens", 1) / max(1, option.get("output_tokens", 1))
        ranked.append({**option, "expected_cost_score": score, "quality_estimate": quality,
                       "local_samples": len(checked), "benchmark_signal": source,
                       "economical": option["quoted_usd"] <= economy_limit,
                       "poor_validation_history": poor, "preference": preference})
    pool = ranked if escalate or complex_task else [o for o in ranked if o["economical"]]
    if not pool or all(o["poor_validation_history"] for o in pool):
        pool = ranked
    return sorted(pool, key=lambda o: (o["poor_validation_history"], o["expected_cost_score"],
                                      -o["quality_estimate"], o["preference"], o["quoted_usd"], o["model"]))


def complex_request(value):
    """Use structural workload, never model-name or genre guesses."""
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("task"), dict):
        return complex_request(value["task"])
    spec = value.get("game_spec", value.get("spec", {}))
    if isinstance(spec, dict) and (len(spec.get("scenes", [])) >= 3 or len(spec.get("requirements", [])) >= 24):
        return True
    plan = value.get("implementation", {})
    scripts = plan.get("scripts", {}).get("scripts", []) if isinstance(plan, dict) else []
    return len(scripts) >= 8 or sum(len(s.get("content", "")) for s in scripts if isinstance(s, dict)) >= 24000


def compact_context(value, shared):
    """Remove exact duplicate handoff fields; keep all requirements somewhere."""
    reduced = dict(shared)
    current = value
    while isinstance(current, dict) and isinstance(current.get("task"), dict):
        current = current["task"]
    if not isinstance(current, dict):
        return reduced
    spec = current.get("game_spec", current.get("spec", {}))
    if isinstance(spec, dict):
        for shared_key, spec_key in (("requirements", "requirements"), ("scenes", "scenes"),
                                     ("controls", "controls"), ("dimension", "dimension"), ("target", "target_platform")):
            if shared_key in reduced and spec.get(spec_key) == reduced[shared_key]:
                reduced.pop(shared_key)
    if any(current.get(key) == reduced.get("original_request") for key in ("request", "original_request")):
        reduced.pop("original_request", None)
    return reduced


def capabilities(model):
    architecture = model.get("architecture") or {}
    inputs = architecture.get("input_modalities", model.get("input_modalities", ["text"]))
    outputs = architecture.get("output_modalities", model.get("output_modalities", ["text"]))
    parameters = model.get("supported_parameters") or []
    unknown = None if "capability_source" in model else False
    return {"text": "text" in outputs, "vision": "image" in inputs,
            "image_generation": "image" in outputs,
            "tools": True if "tools" in parameters else unknown,
            "reasoning": True if "reasoning" in parameters else unknown,
            "json": True if "response_format" in parameters else unknown,
            "context_length": model.get("context_length"),
            "max_completion_tokens": (model.get("top_provider") or {}).get("max_completion_tokens")}


def rank_key(model, role, evidence, price, preference):
    records = evidence.get(role, [])[-100:]
    verified = [r for r in records if r["kind"] == "unity"]
    contracts = [r for r in records if r["kind"] == "contract"]
    availability = [r for r in records if r["kind"] == "availability"]
    # A failure is evidence too. Unmeasured models have a neutral prior; a JSON
    # response is weaker evidence than real Unity validation of its output.
    def rate(items):
        return (1 + sum(r["passed"] for r in items)) / (2 + len(items))
    caps = capabilities(model)
    context = caps["context_length"] or 0
    reasoning_fit = caps["reasoning"] if role in ("supervisor", "designer", "developer", "scene", "qa") else False
    # Once required context/output fit, a larger context window is not evidence
    # of better code. Honor task-specific preferences among equally measured
    # models rather than letting a cheap general model displace coding profiles.
    return (-rate(verified), -rate(contracts), -rate(availability), preference,
            -int(bool(reasoning_fit)), -min(context, 131072), price)
