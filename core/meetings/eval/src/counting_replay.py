#!/usr/bin/env python3
"""counting_replay — push a counting fixture's stage-3 segments THROUGH THE REAL LOCAL PIPELINE to the end.

Fake-bot replay (no live meeting): publish `3-segments.jsonl` onto `transcription_segments` with the native
id STAMPED (the P23 path) → meeting-api collector writes `tc:meeting:{native}` → agent-api watcher arms the
copilot → copilot emits notes/cards on `unit:agent-meet-{native}:out`. Then assert the 1..N oracle survived to
the copilot output.

Runs against the local vexa-v012 stack (gateway :18056, redis via docker exec). Usage:
  python counting_replay.py --fixture ~/vexa-test-rig/fixtures/google_meet/count-silence-1to20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/dima/vexa-0.12/clients/slim")
import vexa_slim.client as C  # noqa: E402

C.AgentApi.PREFIX = "/api"
from vexa_slim import Slim, cookbook as cb  # noqa: E402
from vexa_slim.harvest import harvest  # noqa: E402

GW = "http://127.0.0.1:18056"
_W = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
      "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
      "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}


def nums_in(text: str) -> list[int]:
    out = []
    for tok in re.findall(r"\d+|[a-z]+", str(text).lower()):
        if tok.isdigit():
            out.append(int(tok))
        elif tok in _W:
            out.append(_W[tok])
    return out


def _key() -> str:
    env = Path("/home/dima/vexa-0.12/clients/terminal/.env.local").read_text()
    return next(l.split("=", 1)[1].strip() for l in env.splitlines() if l.startswith("VEXA_API_KEY="))


def publish_segments(native: str, segs: list[dict]) -> None:
    """XADD each stage-3 segment onto transcription_segments with native stamped (paced ~1s, live cadence)."""
    payloads = [json.dumps({"type": "transcription", "meeting_id": "900001",
                            "native_meeting_id": native, "platform": "google_meet", "segments": [s]})
                for s in segs]
    script = (
        "import os,sys,json,time,redis\n"
        "r=redis.from_url(os.environ.get('REDIS_URL','redis://redis:6379/0'),decode_responses=True)\n"
        "for line in sys.stdin:\n"
        "    line=line.strip()\n"
        "    if not line: continue\n"
        "    r.xadd('transcription_segments',{'payload':line}); time.sleep(1.0)\n"
        "print('published ok (no session_end — let the copilot process live)')\n"
    )
    subprocess.run(["docker", "exec", "-i", "vexa-v012-meeting-api-1", "python", "-c", script],
                   input="\n".join(payloads), text=True, check=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--seconds", type=float, default=45.0)
    a = ap.parse_args()
    fx = Path(a.fixture)
    segs = [json.loads(l) for l in (fx / "3-segments.jsonl").read_text().splitlines() if l.strip()]
    truth = [json.loads(l) for l in (fx / "truth.jsonl").read_text().splitlines() if l.strip()]
    n = max(x for t in truth for x in t["numbers"])
    import os as _os; native = f"rep-{fx.name}-{_os.getpid()%10000}"
    print(f"· replaying {fx.name}: {len(segs)} segments → native={native}  (expect 1..{n})")

    slim = Slim(GW, _key())
    await cb.agent_on_meeting(slim, native)        # arm the copilot (proc on + dispatch)
    # Wait for the copilot worker to actually be consuming before the FIRST segment, else segment 1 is
    # seeded as catch-up history (not processed into a note). Give the spawn + collector interval room.
    await asyncio.sleep(12)

    # publish in a thread so we can harvest concurrently
    pub = asyncio.get_event_loop().run_in_executor(None, publish_segments, native, segs)
    h = await harvest(slim, native, seconds=a.seconds)
    await pub

    notes = [e.get("note", {}) for e in h.of("note")]
    cards = [e.get("card", {}) for e in h.of("card")]
    note_text = " ".join(str(x.get("text", "")) for x in notes)
    got = nums_in(note_text)
    missing = [k for k in range(1, n + 1) if k not in got]
    print(f"· copilot out: {h.counts()}")
    for x in notes[:6]:
        print(f"    note[{x.get('speaker','?')}]: {str(x.get('text',''))[:70]!r}")
    for c in cards[:4]:
        print(f"    card: {c.get('kind')} · {c.get('title')}")
    print(f"\n· END-TO-END ORACLE: numbers reaching copilot notes = {len(set(got))}/{n}  missing={missing or 'none'}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
