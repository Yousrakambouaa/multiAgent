"""Short user-facing failure reports; full diagnostics stay in the local job record."""
import re
import json


def model_quality_report(job):
    """Summarize measured provider, contract and Unity outcomes without guessing causes."""
    events_path = job.directory / "events.jsonl"
    events = []
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return {"status": "unavailable", "reason": "No readable job event history"}

    grouped = {}
    for event in events:
        provider, model, role = event.get("provider"), event.get("model"), event.get("role")
        if not model or not role:
            continue
        key = (provider or "unknown", model, role)
        item = grouped.setdefault(key, {"provider": key[0], "model": model, "role": role,
            "api_responses": 0, "api_failures": 0, "unity_passed": 0, "unity_failed": 0,
            "contract_failures": 0, "failure_examples": []})
        if event.get("stage") == "model_response_validated":
            item["api_responses"] += 1
        elif event.get("stage") == "model_failed":
            item["api_failures"] += 1
            if len(item["failure_examples"]) < 3:
                item["failure_examples"].append(str(event.get("error", ""))[:300])
        elif event.get("stage") == "model_outcome":
            kind = event.get("kind")
            if kind == "unity":
                item["unity_passed" if event.get("passed") else "unity_failed"] += 1
            elif kind == "contract" and not event.get("passed"):
                item["contract_failures"] += 1

    measured = list(grouped.values())
    recommendations = []
    by_role = {}
    for item in measured:
        if item["unity_passed"] + item["unity_failed"] >= 3:
            by_role.setdefault(item["role"], []).append(item)
    for role, candidates in by_role.items():
        free = [item for item in candidates if item["model"].endswith(":free")]
        paid = [item for item in candidates if not item["model"].endswith(":free")]
        for candidate in paid:
            paid_count = candidate["unity_passed"] + candidate["unity_failed"]
            if paid_count < 3:
                continue
            paid_rate = candidate["unity_passed"] / paid_count
            for baseline in free:
                free_count = baseline["unity_passed"] + baseline["unity_failed"]
                if free_count < 3:
                    continue
                free_rate = baseline["unity_passed"] / free_count
                if paid_rate >= free_rate + .25:
                    recommendations.append({
                        "role": role, "model": candidate["model"],
                        "evidence": {"unity_pass_rate": paid_rate, "unity_samples": paid_count,
                                     "free_baseline": baseline["model"],
                                     "free_pass_rate": free_rate, "free_samples": free_count},
                        "recommendation": "Measured Unity outcomes support evaluating this paid model for this role.",
                    })

    if not measured:
        assessment = "No model outcome data was recorded; this job cannot support a model-quality conclusion."
    elif any(item["api_failures"] for item in measured):
        assessment = "Provider/API failures are recorded separately from Unity validation; check their error examples before attributing failures to model quality."
    elif any(item["unity_failed"] or item["contract_failures"] for item in measured):
        assessment = "The API returned usable responses, but some generated outputs failed contract or Unity checks; these outcomes alone do not distinguish model weakness from prompt or application defects."
    else:
        assessment = "Recorded responses passed observed checks; this does not establish quality for untested game types."
    if not recommendations:
        assessment += " There is not enough comparative evidence to recommend a paid model."
    return {"status": "measured" if measured else "insufficient_evidence",
            "assessment": assessment, "models": measured,
            "paid_model_recommendations": recommendations}


def failure_type(error):
    text = str(error).casefold()
    stage = getattr(error, "stage", "")
    if "qa review" in text or "gameplay blocker" in text:
        return "Unity QA finding"
    if any(marker in text for marker in ("429", "rate limit", "timed out", "timeout", "temporarily unavailable", "502", "503", "504")):
        return "temporary provider or service failure"
    if "api key" in text or "provider" in text and "configured" in text:
        return "provider configuration"
    if stage in ("compile", "compilation") or re.search(r"\bCS\d{4}\b", text):
        return "Unity C# compilation"
    if stage in ("EditMode", "PlayMode", "assembly"):
        return "Unity scene or gameplay verification"
    if stage == "android":
        return "Android APK build"
    if "repair contract" in text or "behavioral probes" in text:
        return "AI agent output contract"
    if "budget" in text or "cap" in text and "usd" in text:
        return "job budget"
    if getattr(error, "owner", "") == "infrastructure":
        return "Unity or machine environment"
    return "generation workflow"


def failure_report(error, job):
    kind = failure_type(error)
    stage = getattr(error, "stage", "") or job.data.get("stage", "generation")
    if kind == "Unity QA finding" and stage in ("api_call", "generation"):
        stage = "preflight_qa"
    report = job.data.get("failure_report") or {}
    report.update({"type": kind, "stage": stage,
                   "repairs_attempted": int(job.data.get("repairs", 0)),
                   "root_cause": "The bounded generation or verification attempt could not pass its checks."})
    if kind == "temporary provider or service failure":
        report["root_cause"] = "The selected provider did not return a usable response before the retry limit."
        report["needed"] = "Check provider availability, quota, and model access, then retry the saved job."
    elif kind == "Android APK build":
        report["root_cause"] = "Unity did not produce a verified APK."
        report["needed"] = "Install/enable Unity Android Build Support (SDK, NDK, and OpenJDK), free at least 5 GB, then retry."
    elif kind == "Unity scene or gameplay verification":
        report["root_cause"] = "A Unity scene or gameplay check still failed after bounded repair attempts."
        report["needed"] = "Review the local validation report and Unity diagnostics, then retry after correcting the remaining issue."
    elif kind == "Unity QA finding":
        report["root_cause"] = "QA found a concrete scene or gameplay risk that was not resolved before the repair budget was exhausted."
        report["needed"] = "Inspect the saved QA review and repair its named hierarchy, physics, or gameplay issue before retrying."
    elif kind == "Unity C# compilation":
        report["root_cause"] = "Generated C# did not compile after bounded repair attempts."
        report["needed"] = "Review compiler diagnostics in the saved job and retry after fixing the reported code issue."
    elif kind == "AI agent output contract":
        report["root_cause"] = "An AI agent returned a repair that violated the implementation contract."
        report["needed"] = "The job is preserved; retry after model selection has recorded the failed response and can choose a different suitable model."
    else:
        report["needed"] = "Review the saved job diagnostics and retry after resolving the reported blocker."
    job.data["failure_report"] = report
    return (f"Job stopped during {stage}: {report['type']}. {report['root_cause']} "
            f"Repairs attempted: {report['repairs_attempted']}. {report['needed']}")
