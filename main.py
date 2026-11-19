"""CLI for the same persistent supervisor used by Telegram."""
import argparse
import asyncio
import json

from studio.config import JOBS, ROOT, safe_path
from studio.costs import Ledger
from studio.registry import Registry
from studio.state import ProcessLock
from studio.supervisor import Supervisor


async def console(text):
    print(text, flush=True)


async def run(args):
    if args.command in ("mcp-status", "mcp-smoke"):
        from studio.mcp_client import AGENT_TOOLS, MCPError, UnityMCPClient
        try:
            client = UnityMCPClient.from_environment()
        except MCPError as error:
            print(f"Unity MCP configuration failed: {error}")
            return 1
        if client is None:
            print("Unity MCP is disabled. Set STUDIO_UNITY_MCP_ENABLED=1 in .env to enable it.")
            return 0
        marker = JOBS / "current.txt"
        try:
            job_id = marker.read_text(encoding="utf-8").strip()
            job_dir = safe_path(JOBS, job_id)
        except (OSError, ValueError):
            job_dir = None
        project = job_dir / "UnityProject" if job_dir and (job_dir / "job.json").is_file() else None
        if project is None or not project.is_dir():
            print("No current job UnityProject exists. Create/approve a game first.")
            return 1
        try:
            await client.connect()
            info = await client.verify_project(project)
            if args.command == "mcp-smoke":
                result = await client.smoke_test(project)
                print(json.dumps({"status": "passed", "server": client.server_info,
                                  "unity": info.get("unityVersion"), "smoke_test": result}, indent=2))
            else:
                available = {agent: sorted(names.intersection(client.tools))
                             for agent, names in AGENT_TOOLS.items()}
                print(json.dumps({"status": "connected", "server": client.server_info,
                                  "project": str(project), "unity": info.get("unityVersion"),
                                  "discovered_tool_count": len(client.tools),
                                  "agent_tools": available}, indent=2))
            return 0
        except MCPError as error:
            print(f"Unity MCP check failed: {error}")
            return 1
        finally:
            await client.close()
    if args.command == "models":
        registry=Registry(); await registry.refresh()
        print("Verified at",registry.verified_at)
        for role in registry.roles:
            for mid in registry.candidates(role):
                if role=="art":
                    endpoint,cap=registry.image_endpoint(mid)
                    print(role,mid,endpoint["pricing"],"reservation",cap)
                else:
                    print(role,mid,"USD/M input/output",[v*1_000_000 for v in registry.rates(mid)])
        return 0
    supervisor=Supervisor()
    if args.command in ("create","demo"):
        request=args.description if args.command=="create" else "Create a polished woodland 2D platformer with jumping, coins, enemies and an exit"
        job=await supervisor.submit(request,notify=console,demo=args.command=="demo")
        print("Job:",job.directory,flush=True)
        if args.approve and job.data["status"]=="awaiting_approval":
            await supervisor.approve(notify=console,android=args.android)
    elif args.command=="approve":
        await supervisor.approve(notify=console,android=args.android)
    elif args.command=="verify":
        from studio.verification import verify
        await verify(supervisor,console,android=args.android)
    elif args.command=="cancel":
        print(supervisor.cancel())
    elif args.command=="status":
        print(json.dumps(supervisor.job.data if supervisor.job else {"status":"idle"},indent=2))
    elif args.command=="plan":
        print(supervisor.plan_text())
    if supervisor.job:
        print("Status:",supervisor.job.data["status"])
        print(Ledger(supervisor.job).summary())
        return 0 if supervisor.job.data["status"] in ("completed","partial","awaiting_approval","cancelled") else 1
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    for name in ("create","demo","approve","verify"):
        command=sub.add_parser(name)
        if name=="create": command.add_argument("description")
        if name in ("create","demo"): command.add_argument("--approve",action="store_true")
        command.add_argument("--android",action="store_true")
    for name in ("models","status","plan","cancel","mcp-status","mcp-smoke"): sub.add_parser(name)
    args=parser.parse_args()
    with ProcessLock(ROOT/".studio_cache"/"controller.lock"):
        return asyncio.run(run(args))


if __name__=="__main__":
    raise SystemExit(main())
