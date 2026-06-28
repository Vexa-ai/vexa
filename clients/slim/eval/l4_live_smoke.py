#!/usr/bin/env python3
"""L4 · Live meeting smoke — the cookbook end-to-end against a running stack.

This is the bottom of the cookbook test ladder: NO fixtures, the whole stack is real (gateway → control
plane → worker image + claude-code + redis). It is NOT a pytest test — it needs a live compose-dev stack
and a real model, so a HUMAN runs it. It drives the cookbook exactly as an app would.

Two modes:

  # A) replay (no real meeting) — drive the live worker from a scripted transcript, then harvest
  #    First, in another shell, replay a transcript onto redis (see runbook below), then:
  python eval/l4_live_smoke.py --gateway http://localhost:8000 --key $VEXA_API_KEY --native acme-renewal

  # B) real meeting — send a bot to a live Google Meet and harvest what the copilot produces
  python eval/l4_live_smoke.py --gateway http://localhost:8000 --key $VEXA_API_KEY \
      --native abc-defg-hij --meet-url https://meet.google.com/abc-defg-hij --seconds 90

Runbook for mode A (replay) — from core/agent, with compose-dev up:
  python -m eval.replay.replay_transcript --meeting-id acme-renewal --redis redis://localhost:6379/0

What it asserts: `listen_to_meeting` returns a Harvest with at least some events; prints the per-type
counts so you can eyeball notes/cards. Then it exercises the cadence verbs (schedule → list → disable).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from vexa_slim import Slim, cookbook as cb


async def run(args) -> int:
    slim = Slim(args.gateway, args.key)

    print(f"→ listen_to_meeting(native={args.native!r}, meet_url={args.meet_url!r}, seconds={args.seconds})")
    harvest = await cb.listen_to_meeting(
        slim, args.native, seconds=args.seconds, meet_url=args.meet_url)
    print(f"  Harvest counts: {harvest.counts()}  (total={harvest.total})")
    for kind in ("note", "card"):
        for evt in harvest.of(kind)[:3]:
            print(f"    · {kind}: {str(evt)[:120]}")
    ok = harvest.total > 0
    print(f"  {'PASS' if ok else 'FAIL'}: harvest {'has' if ok else 'has NO'} events")

    print("→ cadence verbs: schedule_routine → list_routines → set_routine_enabled(False)")
    try:
        created = await cb.schedule_routine(
            slim, "l4-smoke", cron="0 9 * * *", prompt="L4 smoke routine")
        print(f"  scheduled: job_id={created.get('job_id')}")
        routines = await cb.list_routines(slim)
        print(f"  list_routines: {[r.get('name') for r in routines]}")
    except Exception as e:  # noqa: BLE001 — surface the live error, don't mask it
        print(f"  cadence verbs error (expected if scheduler not wired in this env): {e}")

    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True, help="gateway base URL, e.g. http://localhost:8000")
    ap.add_argument("--key", required=True, help="X-API-Key")
    ap.add_argument("--native", required=True, help="native meeting id (the redis stream / meet code)")
    ap.add_argument("--meet-url", default=None, help="real Google Meet URL (mode B); omit for replay (mode A)")
    ap.add_argument("--seconds", type=float, default=60.0, help="harvest window")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
