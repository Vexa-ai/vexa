#!/usr/bin/env python3
"""own_bot_bridge.py — OUR-OWN-BOT → live-meeting-copilot Integration (inbound watch → fire).

The self-hosted counterpart of vexa_cloud_bridge.py: instead of Vexa Cloud's hosted bot + REST, we run
our own ``vexaai/vexa-bot`` via the local meeting-api (POST /bots), and the bot publishes its live
transcript to OUR redis stream ``transcription_segments`` (the same wire 0.11's collector consumes). This
bridge tails that stream with a DEDICATED consumer group (so it never steals from the collector), and for
the one meeting it owns:

    our bot ──(transcription_segments)──▶ redis ──┬─▶ meeting-api collector (postgres + hash)
                                                  └─▶ THIS bridge ──(tc:meeting:{native})──▶ copilot
                       POST /api/meeting/start ─────────────────▶ agent-api dispatch agent-meet-{native}

A new transcript SOURCE is config, not a new copilot — the canonical Integration primitive. One meeting
per process: we filter ``transcription_segments`` by the numeric meeting_id POST /bots returns, and fan
each COMPLETED segment onto ``tc:meeting:{native_id}`` (skipping live drafts → a clean growing feed).

    VEXA_API_KEY=... python own_bot_bridge.py --meeting-url https://meet.google.com/abc-defg-hij

Env: VEXA_API_KEY (the stack API key; never logged). Flags: --gateway (POST /bots sink), --agent-api
(dispatch sink), --redis, --subject, --bot-name, --no-bot, --idle-end N.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request

KEY = os.environ.get("VEXA_API_KEY", "")


def parse_meeting(url: str) -> tuple[str, str]:
    m = re.search(r"meet\.google\.com/([a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3})", url)
    if m:
        return "google_meet", m.group(1)
    raise SystemExit(f"can't parse platform/native_id from {url!r}")


def _req(method: str, base: str, path: str, body: dict | None = None, *, key: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meeting-url", required=True)
    ap.add_argument("--gateway", default="http://gateway:8000")
    ap.add_argument("--agent-api", default="http://agent-api:8100")
    ap.add_argument("--redis", default="redis://redis:6379/0")
    ap.add_argument("--subject", default="u_live")
    ap.add_argument("--bot-name", default="Vexa EI")
    ap.add_argument("--language", default="en")
    ap.add_argument("--no-bot", action="store_true", help="don't send a bot — consume an existing one")
    ap.add_argument("--meeting-id", default="", help="numeric meeting_id to filter on (with --no-bot)")
    ap.add_argument("--idle-end", type=int, default=0, help="end after N seconds with no new segment")
    args = ap.parse_args()
    if not KEY:
        raise SystemExit("VEXA_API_KEY not set")

    platform, native_id = parse_meeting(args.meeting_url)
    print(f"[bridge] meeting platform={platform} native_id={native_id}", flush=True)

    import redis as redislib

    r = redislib.from_url(args.redis, decode_responses=True, socket_keepalive=True, health_check_interval=10)
    out_stream = f"tc:meeting:{native_id}"
    SRC = "transcription_segments"

    # 1) launch OUR bot via the local meeting-api (gateway) — returns the numeric meeting_id we filter on
    meeting_id = args.meeting_id
    if not args.no_bot:
        res = _req("POST", args.gateway, "/bots", {
            "platform": platform, "native_meeting_id": native_id,
            "bot_name": args.bot_name, "language": args.language,
        }, key=KEY)
        meeting_id = str(res.get("id") or res.get("meeting_id") or "")
        print(f"[bridge] our bot requested → meeting_id={meeting_id} status={res.get('status')}", flush=True)

    # 2) fire the copilot dispatch (the ONE make_dispatch on the control plane) keyed on native_id
    started = _req("POST", args.agent_api, "/api/meeting/start", {
        "platform": platform, "native_id": native_id, "subject": args.subject,
        "title": f"{platform} · {native_id}",
    })
    print(f"[bridge] copilot dispatched → {started.get('unit_id')}", flush=True)

    # 3) tail transcription_segments via a DEDICATED group (never steal from the collector); fan THIS
    #    meeting's completed segments onto tc:meeting:{native_id}.
    group, consumer = "ei_copilot_bridge", "bridge-1"
    try:
        r.xgroup_create(SRC, group, id="0", mkstream=True)
    except redislib.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    print(f"[bridge] consuming {SRC} (group={group}) for meeting_id={meeting_id or '*'} → {out_stream}", flush=True)

    seen: set[str] = set()
    base: list[float] = []  # first segment's start → normalize to meeting-relative seconds (the bot emits absolute)
    last_seg = time.monotonic()
    while True:
        if args.idle_end and time.monotonic() - last_seg > args.idle_end:
            print("[bridge] idle-end reached", flush=True)
            break
        try:
            resp = r.xreadgroup(group, consumer, {SRC: ">"}, count=50, block=4000)
        except (redislib.exceptions.TimeoutError, redislib.exceptions.ConnectionError):
            continue  # blocking XREADGROUP can raise on its own block window — just loop
        for _s, entries in resp or []:
            for msg_id, fields in entries:
                r.xack(SRC, group, msg_id)
                try:
                    p = json.loads(fields.get("payload") or "{}")
                except Exception:
                    continue
                if meeting_id and str(p.get("meeting_id")) != meeting_id:
                    continue  # another meeting sharing the stream
                t = p.get("type")
                if t == "session_end":
                    r.xadd(out_stream, {"payload": json.dumps({"type": "session_end", "uid": native_id})})
                    print("[bridge] session_end → reaping copilot", flush=True)
                    return
                if t != "transcription":
                    continue
                for seg in (p.get("segments") or []):
                    if not seg.get("completed"):
                        continue  # skip live drafts — fan only finalized segments
                    text = (seg.get("text") or "").strip()
                    sid = str(seg.get("segment_id") or f"{seg.get('start')}:{text[:16]}")
                    if not text or sid in seen:
                        continue
                    seen.add(sid)
                    raw = float(seg.get("start") or 0.0)
                    if not base:
                        base.append(raw)
                    start_rel = max(0.0, raw - base[0])
                    end_rel = max(start_rel, float(seg.get("end") or raw) - base[0])
                    out = {
                        "type": "transcription", "session_uid": native_id, "meeting_id": native_id,
                        "segments": [{
                            "speaker": seg.get("speaker") or "Speaker", "text": text,
                            "start": round(start_rel, 1), "end": round(end_rel, 1),
                            "completed": True, "language": seg.get("language", "en"), "segment_id": sid,
                        }],
                    }
                    r.xadd(out_stream, {"payload": json.dumps(out)})
                    last_seg = time.monotonic()
                    print(f"[seg {start_rel:6.1f}] {out['segments'][0]['speaker']}: {text[:60]}", flush=True)


if __name__ == "__main__":
    main()
