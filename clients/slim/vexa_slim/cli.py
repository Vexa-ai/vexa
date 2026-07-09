"""Thin CLI shell — argparse → the SDK / scenarios. Holds no logic of its own."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .client import Slim
from .config import api_key, gateway_url, load_env
from .scenarios import run_processor


def build_slim() -> Slim:
    load_env()
    key = api_key()
    if not key:
        print("ERROR: no API key — set VEXA_API_KEY (or VEXA_BOT_API_KEY), or run against a stack whose "
              "clients/terminal/.env.local carries one.", file=sys.stderr)
        sys.exit(2)
    return Slim(gateway_url(), key)


def parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="vexa_slim",
                                description="slim gateway-only client for the meeting/agent control plane")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="full meeting-processor agent validation scenario")
    sp.add_argument("native")
    sp.add_argument("--platform", default="google_meet")
    sp.add_argument("--seconds", type=float, default=45.0)
    sp.add_argument("--send-bot", metavar="MEET_URL", default=None,
                    help="POST /bots with this meeting url first (meetings prerequisite)")

    sp = sub.add_parser("process", help="toggle the copilot processor on/off")
    sp.add_argument("native")
    sp.add_argument("--platform", default="google_meet")
    sp.add_argument("--off", action="store_true", help="turn processing OFF (default is ON)")

    sp = sub.add_parser("watch", help="tail the merged live feed and tally event types")
    sp.add_argument("native")
    sp.add_argument("--seconds", type=float, default=30.0)

    sp = sub.add_parser("doc", help="read the agent's durable meeting doc")
    sp.add_argument("native")

    sub.add_parser("models", help="auth + agent-api reachability smoke test")

    sp = sub.add_parser("send-bot", help="put a bot in a meeting (meetings domain)")
    sp.add_argument("native")
    sp.add_argument("--url", required=True)
    sp.add_argument("--platform", default="google_meet")

    sp = sub.add_parser("stop-bot", help="remove the bot from a meeting")
    sp.add_argument("native")
    sp.add_argument("--platform", default="google_meet")

    return p.parse_args(argv)


async def _dispatch(args: argparse.Namespace, slim: Slim) -> int:
    if args.cmd == "models":
        print(json.dumps(await slim.agent.models(), indent=2))
        return 0
    if args.cmd == "run":
        return await run_processor(slim, args.native, platform=args.platform,
                                   seconds=args.seconds, send_bot_url=args.send_bot)
    if args.cmd == "process":
        agent = slim.agent
        result = (await agent.stop_processing(args.native, platform=args.platform) if args.off
                  else await agent.start_processing(args.native, platform=args.platform))
        print(json.dumps(result))
        return 0
    if args.cmd == "watch":
        tally = await slim.agent.watch(args.native, seconds=args.seconds,
                                       on_event=lambda e: print(json.dumps(e)[:200]))
        print(f"tally: {json.dumps(tally)}")
        return 0
    if args.cmd == "doc":
        doc = await slim.agent.read_doc(args.native)
        print(doc["content"] if doc and doc.get("content") else "(not written yet)")
        return 0
    if args.cmd == "send-bot":
        print(json.dumps(await slim.meetings.send_bot(args.native, url=args.url, platform=args.platform)))
        return 0
    if args.cmd == "stop-bot":
        print(f"stop-bot → HTTP {await slim.meetings.stop_bot(args.native, platform=args.platform)}")
        return 0
    return 2


def main(argv: "list[str] | None" = None) -> None:
    args = parse_args(argv)
    slim = build_slim()
    sys.exit(asyncio.run(_dispatch(args, slim)))
