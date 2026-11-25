from decimal import Decimal
import json
import time
from uuid import uuid4
from studio.state import ProcessLock, write_json


def validate_budget(value):
    from studio.config import MAX_JOB_CAP
    try:
        cap = Decimal(str(value))
        maximum = Decimal(str(MAX_JOB_CAP))
    except Exception as error:
        raise ValueError("Budget must be a finite dollar amount") from error
    if not cap.is_finite() or not maximum.is_finite() or cap < Decimal("0.01") or cap > maximum:
        raise ValueError(f"Budget must be between $0.01 and ${maximum:g}")
    return float(cap)


class BudgetExceeded(RuntimeError):
    pass


def task_cost_report(job, events=(), completion_status=None):
    """Reported bills and unknown reservations stay separate, including failures."""
    grouped, checks = {}, {}
    for event in events:
        if event.get("stage") == "model_outcome" and event.get("task_id"):
            key = (event["role"], event["task_id"], event.get("kind"))
            checks[key] = bool(event.get("passed"))
    succeeded = {}
    tasks = {(role, task) for role, task, _ in checks}
    for role, task in tasks:
        kinds = {kind: passed for (r, t, kind), passed in checks.items() if (r, t) == (role, task)}
        verified = kinds.get("unity") if role in ("developer", "scene") else kinds.get("contract", kinds.get("usable_image"))
        if verified:
            succeeded[role] = succeeded.get(role, 0) + 1
    for entry in job.data.get("costs", []):
        role = entry.get("role", "unknown")
        item = grouped.setdefault(role, {"reported_usd": 0., "estimated_or_reserved_usd": 0., "attempts": 0})
        field = "reported_usd" if entry.get("basis") == "reported API cost" else "estimated_or_reserved_usd"
        item[field] += float(entry["charged_usd"])
        item["attempts"] += 1
    agents = {}
    for role, item in grouped.items():
        count = succeeded.get(role, 0)
        item.update(successful_tasks=count, reported_cost_per_successful_task_usd=item["reported_usd"] / count if count else None)
        owner = {"ui": "designer", "scene": "developer"}.get(role, role)
        agent = agents.setdefault(owner, {"reported_usd": 0., "estimated_or_reserved_usd": 0.})
        for field in agent:
            agent[field] += item[field]
    actual = sum(a["reported_usd"] for a in agents.values())
    estimates = sum(a["estimated_or_reserved_usd"] for a in agents.values())
    status = completion_status or job.data.get("status")
    return {"by_task_profile": grouped, "by_agent": agents,
            "total_reported_usd": actual, "total_estimated_or_reserved_usd": estimates,
            "reported_cost_is_complete": all(e.get("basis") in ("reported API cost", "provider rejection (no completion)")
                                             for e in job.data.get("costs", [])),
            "reported_cost_per_completed_game_usd": actual if status == "completed" else None,
            "note": "All failed attempts are included. API JSON alone is not task success; unknown costs are not reported bills."}


class Ledger:
    def __init__(self, job):
        self.job = job

    @property
    def job_cap(self):
        cap = Decimal(str(self.job.data["cap_usd"]))
        if self.job.data.get("provider_budget", {}).get("provider") == "openrouter":
            from studio.config import MAX_JOB_CAP
            cap = min(cap, Decimal(str(MAX_JOB_CAP)))
        return cap

    def bind_allowance(self, snapshot):
        self.job.data["provider_budget"] = dict(snapshot)
        available = self._provider_remaining()
        total = self.committed + available if available is not None else self.job_cap
        self.job.data["effective_cap_usd"] = float(min(self.job_cap, total))
        self.job.save()

    def _holds_path(self):
        return self.job.directory.parent / ".openrouter_reservations.json"

    def _holds(self):
        path = self._holds_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _provider_remaining(self, holds=None):
        snapshot = self.job.data.get("provider_budget")
        if not snapshot or snapshot.get("provider") != "openrouter":
            return None
        active = [hold for hold in (self._holds() if holds is None else holds).values()
                  if hold["scope"] == snapshot["scope"] and
                  (hold["status"] != "settled" or hold["settled_at"] >= snapshot["observed_at"])]
        bounds = []
        for field, same_key in (("key_remaining_usd", True), ("account_remaining_usd", False)):
            if snapshot.get(field) is not None:
                reserved = sum((Decimal(str(h["amount"])) for h in active
                                if not same_key or h["identity"] == snapshot["identity"]), Decimal(0))
                bounds.append(max(Decimal(0), Decimal(str(snapshot[field])) - reserved))
        if not bounds and snapshot.get("remaining_usd") is not None:
            bounds.append(max(Decimal(0), Decimal(str(snapshot["remaining_usd"])) -
                              sum((Decimal(str(h["amount"])) for h in active), Decimal(0))))
        return min(bounds) if bounds else None

    def request_budget(self, role):
        remaining = self.remaining
        if not self.job.data.get("provider_budget") or not self.job.data.get("tasks"):
            return remaining
        # Keep early planning/art from consuming the funds needed to implement
        # and verify the game. Local Unity/APK work needs no inference charge.
        future = .6 if role in ("supervisor", "designer", "ui", "art") else .2 if role in ("developer", "scene") else 0
        initial = Decimal(str(self.job.data.get("effective_cap_usd", self.job.data["cap_usd"])))
        return max(Decimal(0), remaining - initial * Decimal(str(future)))

    @property
    def committed(self):
        return sum((Decimal(str(e["charged_usd"])) for e in self.job.data["costs"]), Decimal(0))

    @property
    def remaining(self):
        local = max(Decimal(0), self.job_cap - self.committed)
        provider = self._provider_remaining()
        return min(local, provider) if provider is not None else local

    def can_reserve(self, ceiling):
        ceiling = Decimal(str(ceiling))
        return (not self.job.data.get("budget_halted") and ceiling.is_finite()
                and ceiling >= 0 and ceiling <= self.remaining)

    def reserve(self, role, model, ceiling):
        if self.job.data.get("provider_budget"):
            with ProcessLock(self._holds_path().with_suffix(".lock")):
                return self._reserve(role, model, ceiling, self._holds())
        return self._reserve(role, model, ceiling)

    def _reserve(self, role, model, ceiling, holds=None):
        if self.job.data.get("budget_halted"):
            raise BudgetExceeded("Paid calls stopped after a provider pricing anomaly")
        ceiling = Decimal(str(ceiling))
        if ceiling < 0 or not ceiling.is_finite():
            raise ValueError("Invalid request price")
        available = self._provider_remaining(holds)
        if (ceiling > self.remaining or (available is not None and ceiling > available)
                or ceiling > self.request_budget(role)):
            raise BudgetExceeded("Effective job/key allowance or reserved downstream budget cannot cover this request; saved progress is preserved")
        entry = dict(role=role, model=model, charged_usd=float(ceiling),
                     reserved_usd=float(ceiling), basis="conservative reservation", status="pending")
        self.job.data["costs"].append(entry)
        if holds is not None:
            snapshot = self.job.data["provider_budget"]
            entry["reservation_id"] = uuid4().hex
            holds[entry["reservation_id"]] = {"scope": snapshot["scope"], "identity": snapshot["identity"],
                "amount": float(ceiling), "status": "pending", "settled_at": 0}
            write_json(self._holds_path(), holds)
        self.job.save()  # Persist before any paid request, including retries.
        return entry

    def _update_hold(self, entry):
        if entry.get("reservation_id"):
            with ProcessLock(self._holds_path().with_suffix(".lock")):
                holds = self._holds()
                holds[entry["reservation_id"]].update(amount=entry["charged_usd"], status=entry["status"],
                                                       settled_at=time.time())
                write_json(self._holds_path(), holds)

    def rejected(self, entry):
        """An explicit pre-inference rejection is not a completed generation."""
        entry.update(charged_usd=0, status="settled", basis="provider rejection (no completion)", usage={})
        self._update_hold(entry)
        self.job.save()

    def settle(self, entry, usage):
        cost = usage.get("cost") if isinstance(usage, dict) else None
        if cost is not None:
            cost = Decimal(str(cost))
            if not cost.is_finite() or cost < 0:
                raise ValueError("Invalid reported API cost")
            entry["charged_usd"] = float(cost)
            entry["basis"] = "reported API cost"
        else:
            entry["basis"] = "conservative estimate (usage cost unavailable)"
        entry["status"] = "settled"
        entry["usage"] = usage or {}
        self._update_hold(entry)
        if Decimal(str(entry["charged_usd"])) > Decimal(str(entry["reserved_usd"])):
            self.job.data["budget_halted"] = True
            entry["pricing_anomaly"] = True
            self.job.save()
            raise BudgetExceeded("Provider reported a cost above the verified request reservation; job stopped")
        self.job.save()
        if self.committed > Decimal(str(self.job.data["cap_usd"])):
            raise BudgetExceeded("Provider reported a cost over the reserved maximum; job stopped")

    def summary(self):
        entries = self.job.data["costs"]
        actual = sum((Decimal(str(e["charged_usd"])) for e in entries if e["basis"] == "reported API cost"), Decimal(0))
        estimates = self.committed - actual
        summary = f"API-reported: ${actual:.6f}; estimates/reservations: ${estimates:.6f}; cap: ${self.job.data['cap_usd']:.2f}"
        if self.job.data.get("provider_budget"):
            summary += f"; effective remaining: ${self.remaining:.6f}; allowance: {self.job.data['provider_budget']['status']}"
        return summary
