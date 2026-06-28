"""play — minimal human interfaces over the cookbook (the things you actually run).

Four tiny surfaces, each driving ONLY `vexa_slim.cookbook.*` (zero logic of its own — a working run IS a
cookbook proof):

    python -m vexa_slim.play onboard                       # interactive cold-start interview (you type)
    python -m vexa_slim.play chat "what do you know?"      # one pure chat turn
    python -m vexa_slim.play chat --meeting ety-jhht-nek   # chat grounded on a meeting's durable doc
    python -m vexa_slim.play routine daily-graph --cron "0 18 * * *" --prompt "update kg/entities from today's meetings"

Auth: reads GATEWAY_URL + VEXA_API_KEY/VEXA_BOT_API_KEY (clients/terminal/.env.local), exactly like the rest
of the client. (Web meeting view: `python -m vexa_slim.web` — see web.py.)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import client as _client

# The deployed gateway fronts agent-api under /api/* (no /agent route yet) — match the live contract.
_client.AgentApi.PREFIX = "/api"

from . import cookbook as cb  # noqa: E402  (after the prefix is set)
from .client import Slim  # noqa: E402
from .config import ENV_FILE, api_key, gateway_url, load_env  # noqa: E402


def _slim() -> Slim:
    load_env()
    key = api_key()
    if not key:
        print("no API key — run `python -m vexa_slim.play login --email you@example.com` first "
              "(or set VEXA_API_KEY).", file=sys.stderr)
        raise SystemExit(2)
    return Slim(gateway_url(), key)


def _save_key(key: str) -> None:
    """Persist VEXA_API_KEY into the shared .env.local so every CLI + the web view auto-auth."""
    lines, found = [], False
    if ENV_FILE.exists():
        for ln in ENV_FILE.read_text().splitlines():
            if ln.startswith("VEXA_API_KEY="):
                lines.append(f"VEXA_API_KEY={key}"); found = True
            else:
                lines.append(ln)
    if not found:
        lines.append(f"VEXA_API_KEY={key}")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n")


async def cmd_login(args) -> int:
    """Provision (or bind) a user and persist the key so everything else just works."""
    load_env()
    import os
    admin_token = args.admin_token or os.environ.get("ADMIN_API_TOKEN")
    if args.api_key:
        slim = await cb.connect(gateway_url(), api_key=args.api_key)
    elif args.email and admin_token:
        slim = await cb.connect(gateway_url(), email=args.email, admin_api=args.admin_api, admin_token=admin_token)
    else:
        print("login: pass --api-key KEY, or --email X with --admin-token T (or ADMIN_API_TOKEN env).",
              file=sys.stderr)
        return 2
    me = await cb.whoami(slim)
    _save_key(slim._headers["X-API-Key"])
    print(f"· logged in as {me.get('email') or me.get('user_id')} — key saved to {ENV_FILE}")
    print("· now run:  python -m vexa_slim.play onboard")
    return 0


async def _entities(slim: Slim) -> list:
    tree = await cb.browse_workspace(slim)
    return [p for p in tree if "kg/entities/" in str(p)]


async def cmd_onboard(args) -> int:
    """Interactive cold-start interview: init → onboard → you answer → entities get written."""
    slim = _slim()
    me = await cb.whoami(slim)
    print(f"· connected as {me.get('email') or me.get('user_id')}")
    init = await cb.init_workspace(slim)
    print(f"· workspace {init.get('workspace')} ({'seeded' if init.get('seeded') else 'already there'})")
    print("· starting onboarding — answer the agent; blank line or Ctrl-D to finish.\n")

    S = "onboard"
    reply = await cb.chat(slim, "onboard me", files=["onboarding.md"], session=S)
    print(f"agent› {reply}\n")
    while True:
        try:
            line = input("you› ").strip()
        except EOFError:
            break
        if not line:
            break
        reply = await cb.chat(slim, line, session=S)
        print(f"\nagent› {reply}\n")

    ents = await _entities(slim)
    print("\n· entities in your workspace now:")
    for e in ents:
        print(f"    - {e}")
    if not ents:
        print("    (none yet)")
    return 0


async def cmd_chat(args) -> int:
    """One chat turn — pure, or grounded on a meeting's durable doc via files=."""
    slim = _slim()
    files = [f"kg/entities/meeting/{args.meeting}.md"] if args.meeting else None
    prompt = args.text or input("you› ").strip()
    reply = await cb.chat(slim, prompt, files=files, session=args.session)
    print(reply)
    return 0


async def cmd_routine(args) -> int:
    """Schedule a routine and validate it compiled to a job + shows up in list_routines."""
    slim = _slim()
    created = await cb.schedule_routine(slim, args.name, cron=args.cron, prompt=args.prompt, run_now=args.run_now)
    job = created.get("job_id") or created.get("routine", {}).get("job_id")
    print(f"· scheduled {args.name!r}  cron={args.cron!r}  job_id={job}")
    routines = await cb.list_routines(slim)
    match = [r for r in routines if r.get("name") == args.name]
    if match:
        r = match[0]
        print(f"· VALIDATED — list_routines shows it: cron={r.get('cron')} job_id={r.get('job_id')} "
              f"enabled={r.get('enabled')}")
        return 0
    print("· NOT FOUND in list_routines after create — store duality? (see H6)", file=sys.stderr)
    return 1


def main(argv: "list[str] | None" = None) -> None:
    p = argparse.ArgumentParser(prog="vexa_slim.play", description="minimal human interfaces over the cookbook")
    sub = p.add_subparsers(dest="cmd", required=True)

    sl = sub.add_parser("login", help="provision/bind a user and save the key (auth, once)")
    sl.add_argument("--email", help="provision a fresh user with this email")
    sl.add_argument("--api-key", help="bind an existing key instead of provisioning")
    sl.add_argument("--admin-api", default="http://localhost:18057", help="identity admin-api base")
    sl.add_argument("--admin-token", help="admin token (or ADMIN_API_TOKEN env)")

    sub.add_parser("onboard", help="interactive cold-start interview (you type)")

    sc = sub.add_parser("chat", help="one chat turn — pure or over a meeting")
    sc.add_argument("text", nargs="?", help="the prompt (omit to be asked)")
    sc.add_argument("--meeting", help="ground on this meeting's durable doc (native id)")
    sc.add_argument("--session", help="session id to continue a conversation")

    sr = sub.add_parser("routine", help="schedule a routine + validate it")
    sr.add_argument("name")
    sr.add_argument("--cron", required=True, help='5-field cron, e.g. "0 18 * * *"')
    sr.add_argument("--prompt", required=True, help="what the routine does each run")
    sr.add_argument("--run-now", action="store_true", help="fire one immediate dispatch")

    args = p.parse_args(argv)
    fn = {"login": cmd_login, "onboard": cmd_onboard, "chat": cmd_chat, "routine": cmd_routine}[args.cmd]
    raise SystemExit(asyncio.run(fn(args)))


if __name__ == "__main__":
    main()
