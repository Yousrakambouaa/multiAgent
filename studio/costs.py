from decimal import Decimal


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, job):
        self.job = job

    @property
    def committed(self):
        return sum((Decimal(str(e["charged_usd"])) for e in self.job.data["costs"]), Decimal(0))

    def reserve(self, role, model, ceiling):
        if self.job.data.get("budget_halted"):
            raise BudgetExceeded("Paid calls stopped after a provider pricing anomaly")
        ceiling = Decimal(str(ceiling))
        if ceiling < 0 or not ceiling.is_finite():
            raise ValueError("Invalid request price")
        if self.committed + ceiling > Decimal(str(self.job.data["cap_usd"])):
            raise BudgetExceeded("Job spending cap reached; no further API calls will be made")
        entry = dict(role=role, model=model, charged_usd=float(ceiling),
                     reserved_usd=float(ceiling), basis="conservative reservation", status="pending")
        self.job.data["costs"].append(entry)
        self.job.save()  # Persist before any paid request, including retries.
        return entry

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
        return f"API-reported: ${actual:.6f}; estimates/reservations: ${estimates:.6f}; cap: ${self.job.data['cap_usd']:.2f}"
