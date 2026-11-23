"""Real isolated generation audit using .env; never changes the current Telegram job."""
import argparse
import asyncio
import json

from studio.config import JOBS, ROOT, UNITY, safe_path
from studio.mcp_client import shutdown_environment_clients
from studio.registry import Registry
from studio.state import ProcessLock, write_json
from studio.supervisor import Supervisor
from studio.unity import available_space


DEFAULT_PROMPT = (
    "Create a small complete 3D desktop game using only colored primitive geometry. "
    "Use WASD to move a cube on a bounded platform, collect three yellow spheres to win, "
    "show a score and win message, and press R to restart. Include a perspective camera "
    "and directional light. Do not require external images or touch controls."
)


async def run(args):
    async def notify(text):
        print(text, flush=True)
    try:
        if args.unity_fixture:
            # Explicit deterministic engine audit, never presented as AI output.
            import sys
            sys.path.insert(0, str(ROOT / "tests"))
            from game_examples import examples
            from studio.generation import save_implementation
            from studio.state import Job
            from studio.supervisor import TASKS
            from studio.unity import bootstrap
            from studio.verification import verify
            studio = Supervisor(JOBS / "verification_runs/unity_fixtures")
            request, spec, plan = examples(args.unity_fixture)
            if args.resume_fixture:
                directory = safe_path(studio.root, args.resume_fixture)
                job = Job.load(directory / "job.json")
                if job.data["request"] != request:
                    raise ValueError("The saved job is not this deterministic Unity fixture")
            else:
                job = Job.create(request, 0, root=studio.root)
            studio.job = job
            job.data["tasks"] = [dict(role=r, name=n, dependencies=d, status="pending") for r,n,d in TASKS]
            write_json(job.directory / "game_spec.json", spec.model_dump())
            write_json(job.directory / "ui_plan.json", {"controls": spec.controls})
            write_json(job.directory / "art_manifest.json", [])
            project = bootstrap(job, spec.dimension if spec.version == 2 else "2D")
            save_implementation(job, project, spec, plan)
            # The scene builder always loads this resource, even when the
            # deterministic fixture intentionally has no image assets.
            write_json(project / "Assets/Resources/studio_art.json", {"assets": []})
            await verify(studio, notify, android=False)
            write_json(ROOT / (".studio_cache/unity_fixture_" + args.unity_fixture + ".json"),
                {"status": job.data["status"], "job_directory": str(job.directory),
                 "error": job.data.get("error", ""), "ai_generated": False})
            return 0 if job.data["status"] in ("completed", "partial") else 1
        if args.preflight:
            registry = Registry()
            await registry.refresh()
            result = {"provider": registry.provider.name, "unity_installed": UNITY.is_file(),
                      "free_bytes": available_space(ROOT), "capabilities": registry.capability_summary(),
                      "models": {role: list(registry.candidates(role)) for role in registry.roles}}
            write_json(ROOT / ".studio_cache/live_preflight.json", result)
            print(json.dumps(result, indent=2))
            return 0
        studio = Supervisor(JOBS / "verification_runs")
        if studio.busy():
            raise RuntimeError("A previous live audit still has an active job; inspect jobs/verification_runs before rerunning")
        job = await studio.submit(args.prompt, notify=notify)
        print("Audit job:", job.directory, flush=True)
        if job.data["status"] == "awaiting_approval":
            await studio.approve(notify=notify, android=False)
        result = {"job_id": job.data["id"], "job_directory": str(job.directory),
                  "status": job.data["status"], "error": job.data.get("error", ""),
                  "costs": job.data["costs"], "scope": "Real provider, Unity compilation, scene assembly, EditMode and PlayMode; Telegram delivery excluded"}
        write_json(ROOT / ".studio_cache/live_verification.json", result)
        return 0 if job.data["status"] in ("completed", "partial") else 1
    finally:
        await shutdown_environment_clients()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--unity-fixture", choices=("racing", "puzzle"), help="Real Unity checks of a deterministic test fixture; no AI calls")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--resume-fixture", help="Saved fixture job ID to reverify after package imports finish")
    args = parser.parse_args()
    if args.resume_fixture and not args.unity_fixture:
        parser.error("--resume-fixture requires --unity-fixture")
    with ProcessLock(ROOT / ".studio_cache/controller.lock"):
        return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
