"""CLI for the same persistent supervisor used by Telegram."""
import argparse
import asyncio
import json

from studio.config import ROOT
from studio.costs import Ledger
from studio.registry import Registry
from studio.state import ProcessLock
from studio.supervisor import Supervisor


async def console(text):
    print(text, flush=True)


async def run(args):
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
        return 0 if supervisor.job.data["status"] in ("completed","awaiting_approval","cancelled") else 1
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    for name in ("create","demo","approve","verify"):
        command=sub.add_parser(name)
        if name=="create": command.add_argument("description")
        if name in ("create","demo"): command.add_argument("--approve",action="store_true")
        command.add_argument("--android",action="store_true")
    for name in ("models","status","plan","cancel"): sub.add_parser(name)
    args=parser.parse_args()
    with ProcessLock(ROOT/".studio_cache"/"controller.lock"):
        return asyncio.run(run(args))


if __name__=="__main__":
    raise SystemExit(main())
