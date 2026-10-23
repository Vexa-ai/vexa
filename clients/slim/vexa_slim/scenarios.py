"""Validation scenarios — high-level flows built ON the Slim SDK. The messy bits (event formatting,
the verdict rules) live in their own small functions so each scenario reads as a list of steps."""
from __future__ import annotations

import json

import httpx

from .client import Slim


def format_event(evt: dict) -> "str | None":
    """A one-line trace string for a live event, or None to skip it."""
    t = evt.get("type")
    if t == "transcript":
        return f"    · transcript [{evt.get('speaker') or '?'}] {(evt.get('text') or '')[:80]}"
    if t == "retract":
        return f"    · retract   {json.dumps(evt.get('segment_ids'))[:80]}"
    if t == "meeting-end":
        return "    · meeting-end"
    return None


def verdict(tally: dict) -> "tuple[int, str]":
    """Map an event tally → (exit_code, human verdict line). The one place the pass/fail rules live.

    The bar is the TRANSCRIPT. It used to be notes + cards from the in-product processor, which PRD
    decision 34 removed: whether the agent then makes something of a meeting is a question for a
    chat turn over the MCP, not for this feed."""
    transcript = tally.get("transcript", 0)
    if transcript == 0:
        return 3, ("? INCONCLUSIVE · no transcript flowed — the bot isn't in the meeting (or wrong "
                   "native_id). Send a bot / pick a live meeting and retry.")
    return 0, f"✓ PASS · transcript flowed ({transcript} segs) onto the live feed."


async def run_processor(slim: Slim, native: str, *, platform: str = "google_meet",
                        seconds: float = 45.0, send_bot_url: "str | None" = None) -> int:
    """Validate the live-transcript path end-to-end through the gateway. Reads as 5 steps."""
    print(f"── live-transcript validation · native={native} platform={platform} ──\n")

    # 1 · auth + agent-api reachable
    try:
        print(f"[1] auth OK · models: {json.dumps(await slim.agent.models())}")
    except httpx.HTTPStatusError as e:
        print(f"[1] FAIL · agent-api unreachable: {e.response.status_code} {e.response.text[:200]}")
        return 2

    # 2 · (optional) put a bot in the meeting so a transcript flows  (meetings domain)
    if send_bot_url:
        try:
            res = await slim.meetings.send_bot(native, url=send_bot_url, platform=platform)
            print(f"[2] bot requested · {json.dumps(res)[:200]}")
        except httpx.HTTPStatusError as e:
            print(f"[2] WARN · send-bot failed ({e.response.status_code}); a transcript must already flow")
    else:
        print("[2] skip send-bot (expecting a live transcript already on this meeting)")

    # 3 · watch the live feed, printing a compact trace
    print(f"[3] watching the live feed for {seconds:.0f}s …")

    def trace(evt: dict) -> None:
        line = format_event(evt)
        if line:
            print(line)

    tally = await slim.agent.watch(native, seconds=seconds, on_event=trace)
    print(f"\n[3] tally: {json.dumps(tally)}")

    # 4 · verdict
    rc, line = verdict(tally)
    print(f"\n── verdict ──\n  {line}")

    # 5 · the durable artifact, if a chat turn has written one
    doc = await slim.agent.read_doc(native)
    if doc and doc.get("content"):
        head = "\n".join(doc["content"].splitlines()[:8])
        print(f"\n[5] meeting doc kg/entities/meeting/{native}.md (head):\n{head}")
    else:
        print(f"\n[5] meeting doc not written yet (kg/entities/meeting/{native}.md)")
    return rc
