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
    from studio.costs import task_cost_report
    return {"status": "measured" if measured else "insufficient_evidence",
            "assessment": assessment, "models": measured,
            "paid_model_recommendations": recommendations, "costs": task_cost_report(job, events),
            "escalations": [e for e in events if e.get("stage") in ("model_policy", "model_escalation")]}


def failure_type(error):
    text = str(error).casefold()
    stage = getattr(error, "stage", "")
    if hasattr(error, "attempts") and hasattr(error, "provider"):
        return "AI provider/model failure"
    if hasattr(error, "operation") and hasattr(error, "diagnostic"):
        return "AI provider catalog failure"
    if "authentication failed" in text or "api key" in text:
        return "provider configuration"
    if "qa review" in text or "gameplay blocker" in text:
        return "Unity QA finding"
    if any(marker in text for marker in ("429", "rate limit", "timed out", "timeout", "temporarily unavailable", "502", "503", "504")):
        return "temporary provider or service failure"
    if "provider" in text and "configured" in text:
        return "provider configuration"
    if getattr(error, "owner", "") == "infrastructure" and stage != "android":
        return "Unity or machine environment"
    if stage in ("compile", "compilation") or re.search(r"\bCS\d{4}\b", text):
        return "Unity C# compilation"
    if stage in ("EditMode", "PlayMode", "assembly"):
        return "Unity scene or gameplay verification"
    if stage == "android":
        return "Android APK build"
    if any(marker in text for marker in ("repair contract", "behavioral probes", "implementation validation",
                                         "validation errors for implementation", "validation error for implementation")):
        return "AI agent output contract"
    if "budget" in text or "cap" in text and "usd" in text:
        return "job budget"
    if getattr(error, "owner", "") == "infrastructure":
        return "Unity or machine environment"
    return "generation workflow"


def failure_report(error, job):
    kind = failure_type(error)
    if isinstance(error, OSError):
        kind = "Unity or machine environment"
    stage = getattr(error, "stage", "") or job.data.get("stage", "generation")
    if kind == "AI provider/model failure":
        stage = f"{getattr(error, 'role', 'agent')} model request"
    elif kind == "AI provider catalog failure":
        stage = "provider model discovery"
    if kind == "Unity QA finding" and stage in ("api_call", "generation"):
        stage = "preflight_qa"
    report = job.data.get("failure_report") or {}
    for key in ("provider", "agent_role", "provider_attempts"):
        report.pop(key, None)
    report.update({"type": kind, "stage": stage,
                   "repairs_attempted": int(job.data.get("repairs", 0)),
                   "root_cause": "The bounded generation or verification attempt could not pass its checks."})
    report["repairs_attempted"] += int(job.data.get("implementation_repairs", 0))
    if kind == "AI provider/model failure":
        attempts = getattr(error, "attempts", [])
        report["provider"] = getattr(error, "provider", "unknown")
        report["agent_role"] = getattr(error, "role", "unknown")
        report["provider_attempts"] = attempts
        if getattr(error, "fallback_blocked", False):
            report["root_cause"] = getattr(error, "reason", None) or "The provider rejected the configured credentials or account access."
            if "credential" in report["root_cause"].casefold():
                report["needed"] = "Check the selected provider key/account permissions. Changing models will not fix a credential rejection."
            else:
                report["needed"] = "Check provider billing or quota. This provider-specific rejection stopped model fallback."
        elif attempts:
            report["root_cause"] = "No eligible model returned a usable response for this agent request."
            report["needed"] = ("The job stopped before Unity because its bounded model attempts were exhausted. "
                                "Review the model/API errors below. Studio tried eligible alternatives without "
                                "exceeding the job budget; if none were available, only the configured model could run.")
        else:
            report["root_cause"] = getattr(error, "reason", None) or str(error)
            report["needed"] = ("No eligible model request was made. Check the provider model catalog, JSON "
                                "support, model configuration and pricing restrictions, then retry.")
    elif kind == "AI provider catalog failure":
        report["provider"] = getattr(error, "provider", "unknown")
        report["root_cause"] = str(error)
        report["needed"] = ("The provider model catalog could not be loaded, so no Unity work or agent request "
                            "started. Check provider availability, key access and quota, then retry.")
    elif kind == "provider configuration":
        report["root_cause"] = str(error)
        report["needed"] = "Check the selected provider key/configuration. Changing models cannot fix a rejected API key."
    elif kind == "temporary provider or service failure":
        report["root_cause"] = str(error)
        report["needed"] = "Check the provider's status, quota and model access; retry the saved job after it recovers."
    elif kind == "AI agent output contract":
        lines = str(error).splitlines()
        report["root_cause"] = "\n".join(lines[:7])[:1600]
        report["needed"] = ("Generated data failed the implementation contract before Unity scene assembly. "
                            "Adding API credits does not fix this schema error. The responses and exact diagnostics "
                            "are saved as implementation_response_*.json and implementation_diagnostics_*.json; "
                            "the job is preserved for retry.")
    elif kind == "job budget":
        report["root_cause"] = str(error)
        report["needed"] = "No further model request was started beyond the job cap. Review the reservation details, reduce the request size, or explicitly raise the per-job cap."
    elif kind == "Android APK build":
        report["root_cause"] = "Android packaging stopped: " + str(error)[:1400]
        report["needed"] = "Review the named blocker, android.json and the Unity build log. Saved source and test evidence are preserved for a build retry."
    elif kind == "Unity or machine environment":
        report["root_cause"] = str(error)[:1800]
        report["needed"] = "Restore the matching Unity project/bridge or resolve the named machine issue. Saved code and art are preserved; this is not an API-credit or generated-C# diagnosis."
    elif kind == "Unity scene or gameplay verification":
        report["root_cause"] = "A Unity scene or gameplay check still failed after bounded repair attempts."
        report["needed"] = "Review the local validation report and Unity diagnostics, then retry after correcting the remaining issue."
    elif kind == "Unity QA finding":
        report["root_cause"] = "QA found a concrete scene or gameplay risk that was not resolved before the repair budget was exhausted."
        report["needed"] = "Inspect the saved QA review and repair its named hierarchy, physics, or gameplay issue before retrying."
    elif kind == "Unity C# compilation":
        report["root_cause"] = "Generated C# did not compile after bounded repair attempts."
        report["needed"] = "Review compiler diagnostics in the saved job and retry after fixing the reported code issue."
    else:
        report["needed"] = "Review the saved job diagnostics and retry after resolving the reported blocker."
    job.data["failure_report"] = report
    return (f"Job stopped during {stage}: {report['type']}. {report['root_cause']} "
            f"Repairs attempted: {report['repairs_attempted']}. {report['needed']}")


def failure_message(report, heading="", suffix=""):
    message = (heading + " " if heading else "") + (
        f"Job stopped during {report.get('stage', 'generation')}: "
        f"{report.get('type', 'generation workflow')}. "
        f"{report.get('root_cause', 'The job could not complete.')} "
        f"Repairs attempted: {report.get('repairs_attempted', 0)}. "
        f"{report.get('needed', 'Review saved diagnostics and retry.')}")
    attempts = report.get("provider_attempts", [])
    if attempts:
        message += "\nModel/API attempts:"
        for attempt in attempts:
            status = f"HTTP {attempt['status']}" if attempt.get("status") is not None else attempt.get("kind", "request")
            message += f"\n- {attempt.get('model', 'unknown model')} ({status}): {attempt.get('reason', 'no details')}"
            if attempt.get("request_id"):
                message += f" [request ID: {attempt['request_id']}]"
    if suffix:
        message += "\n" + suffix
    return message
