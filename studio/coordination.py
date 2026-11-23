"""Persisted handoffs and completion gates for the existing five agents."""
import hashlib
import json

from studio.state import write_json
from studio.config import ROOT


def share_game(job, spec, provider):
    document = spec.model_dump()
    digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    job.data["shared_context"] = {
        "job_id": job.data["id"], "spec_sha256": digest, "provider": provider,
        "original_request": job.data.get("effective_request", job.data["request"]),
        "dimension": document.get("dimension", "2D"), "target": spec.target_platform,
        "controls": document.get("controls", document.get("input_requirements", [])),
        "scenes": spec.scenes, "requirements": document.get("requirements", []),
        "ownership": {"ui": "designer", "scene": "developer", "scripts": "developer",
                      "assets": "art", "validation_and_repairs": "qa"},
        "completion_criteria": "Compiled, assembled, EditMode and requirement-linked PlayMode checks passed; manual review stays unverified.",
    }
    version_file = ROOT / "generated_game/ProjectSettings/ProjectVersion.txt"
    if version_file.is_file():
        job.data["shared_context"]["unity_version"] = version_file.read_text(encoding="utf-8").splitlines()[0]
    job.data["work_items"] = [dict(t.model_dump(), status="planned") for t in getattr(spec, "tasks", [])]
    write_json(job.directory / "shared_context.json", job.data["shared_context"])
    job.save()


def finish_stage(job, name, status="passed", evidence=None):
    tasks = {task["name"]: task for task in job.data["tasks"]}
    task = tasks[name]
    blocked = [dep for dep in task["dependencies"] if tasks[dep]["status"] not in ("passed", "partial")]
    if blocked:
        raise RuntimeError(f"Cannot complete {name}; unfinished dependencies: {', '.join(blocked)}")
    task["status"] = status
    if evidence:
        task["evidence"] = evidence
    job.event(task["role"], "handoff_completed", task=name, status=status, evidence=evidence)


def invalidate_validation(job):
    for task in job.data["tasks"]:
        if task["name"] in ("compilation", "assembly", "EditMode", "PlayMode", "artifacts"):
            task["status"] = "pending"
            task.pop("evidence", None)
    job.save()


def update_work_items(job, requirements):
    results = {r["id"]: r for r in requirements}
    items = {item["id"]: item for item in job.data.get("work_items", [])}
    pending = set(items)
    while pending:
        ready = [key for key in pending if all(dep not in pending for dep in items[key]["dependencies"])]
        if not ready:
            raise ValueError("Cyclic or missing work dependencies")
        for key in ready:
            item = items[key]
            deps = [items[dep]["status"] for dep in item["dependencies"]]
            linked = [results[rid] for rid in item["requirement_ids"]]
            item["status"] = ("blocked" if any(s != "verified" for s in deps) else
                              "verified" if all(r["verification_result"] == "passed" and
                                                r["implementation_status"] != "incomplete" for r in linked) else "unverified")
            pending.remove(key)
    write_json(job.directory / "work_items.json", list(items.values()))
    job.save()
