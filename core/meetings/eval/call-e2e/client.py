"""The call E2E client (#1783): start a call through the gateway (`POST /bots {"platform":
"vexa_call"}`), give two headless participants room tokens (what agent-api's token route mints, with
the same claims), let each publish its own speech WAV as a microphone track, wait for both to leave
and for the listener to complete the meeting, then read the transcript it wrote. Prints one JSON
report: per speaker, seconds published and seconds transcribed, and the text.

    python client.py --gateway http://127.0.0.1:G --wav-a a.wav --wav-b b.wav --bot-dir DIR --bot-log-dir DIR
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time


async def run(a) -> dict:
    import httpx
    import redis.asyncio as aioredis

    from meeting_api.calls import LiveKitConfig, participant_token

    key = os.environ["E2E_API_KEY"]
    async with httpx.AsyncClient(base_url=a.gateway, headers={"x-api-key": key}, timeout=30) as http:
        r = await http.post("/bots", json={"platform": "vexa_call", "session_id": "s-0123456789abcdef"})
        r.raise_for_status()
        call = r.json()
    room, mid = call["native_meeting_id"], call["id"]
    cfg = LiveKitConfig.from_env()
    people = [
        {"identity": "u:7", "name": "Ada", "wav": a.wav_a, "startSec": 2, "holdSec": 1},
        {"identity": "u:8", "name": "Grace", "wav": a.wav_b, "startSec": 2 + a.gap, "holdSec": 1},
    ]
    spec = {"url": cfg.url, "people": [
        {**p, "token": participant_token(cfg, room=room, identity=p["identity"], name=p["name"], ttl_sec=900)}
        for p in people]}
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        "node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "participants.mjs"),
        cwd=a.bot_dir, env={**os.environ, "CALL_E2E_SPEC": json.dumps(spec)},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    published = {}
    for line in out.decode().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # The SDK logs JSON on stdout too; a participant's result is the line with its identity.
        if isinstance(row, dict) and "seconds_published" in row:
            published[row["identity"]] = row
    left_at = time.time()
    status, speakers_line = None, None
    for _ in range(int(a.wait)):
        for name in os.listdir(a.bot_log_dir):
            if f"mtg-{mid}-" not in name:
                continue
            with open(os.path.join(a.bot_log_dir, name), errors="replace") as f:
                for line in f:
                    if "orchestrator: emitting completed" in line:
                        status = line.split("emitting ", 1)[1].strip()
                    if "[bot] call: speakers " in line:
                        speakers_line = line.split("[bot] call: speakers ", 1)[1].strip()
        if status:
            break
        await asyncio.sleep(1)
    await asyncio.sleep(3)
    r = aioredis.from_url(os.environ["E2E_REDIS_URL"], decode_responses=True)
    segs = {}
    for _id, fields in await r.xrange("transcription_segments", "-", "+"):
        p = json.loads(fields.get("payload", "{}"))
        if str(p.get("meeting_id")) != str(mid):
            continue
        for s in p.get("segments") or [p]:
            if s.get("text"):
                segs[s.get("segment_id") or _id] = s
    finals = sorted((s for s in segs.values() if s.get("completed", True)), key=lambda s: s.get("start", 0))
    by = {}
    for s in finals:
        who = s.get("speaker") or "?"
        d = by.setdefault(who, {"segments": 0, "seconds_transcribed": 0.0, "text": []})
        d["segments"] += 1
        d["seconds_transcribed"] += max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        d["text"].append(s["text"].strip())
    for d in by.values():
        d["seconds_transcribed"] = round(d["seconds_transcribed"], 2)
        d["text"] = " ".join(d["text"])
    return {
        "meeting_id": mid, "room": room,
        "published": {p["name"]: published.get(p["identity"], {}).get("seconds_published") for p in people},
        "participants_stderr_tail": err.decode()[-400:] if proc.returncode else "",
        "listener_received": json.loads(speakers_line) if speakers_line else None,
        "bot_terminal": status, "seconds_from_last_leave_to_completed": round(time.time() - left_at, 1) if status else None,
        "wall_sec": round(time.time() - t0, 1),
        "by_speaker": by,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--wav-a", required=True)
    ap.add_argument("--wav-b", required=True)
    ap.add_argument("--gap", type=float, default=20.0, help="seconds between the two speakers' starts")
    ap.add_argument("--wait", type=float, default=180)
    ap.add_argument("--bot-dir", required=True)
    ap.add_argument("--bot-log-dir", required=True)
    print(json.dumps(asyncio.run(run(ap.parse_args())), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
