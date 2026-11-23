"""Rank live capabilities and observed task outcomes, never marketing model names."""


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
    reasoning_fit = caps["reasoning"] if role in ("supervisor", "designer", "qa") else False
    return (-rate(verified), -rate(contracts), -rate(availability), -int(bool(reasoning_fit)),
            -min(context, 131072), price, preference)
