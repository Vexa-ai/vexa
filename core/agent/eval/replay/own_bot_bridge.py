#!/usr/bin/env python3
"""own_bot_bridge.py — OUR-OWN-BOT → live-transcript Integration (inbound watch → fire).

The self-hosted counterpart of vexa_cloud_bridge.py: instead of Vexa Cloud's hosted bot + REST, we run
our own ``vexaai/vexa-bot`` via the local meeting-api (POST /bots), and the bot publishes its live
transcript to OUR redis stream ``transcription_segments``. Every bot appends to that one stream, and
only meeting-api's collector can check that an entry was signed by the meeting's own bot; it writes
what it admitted to ``tc:meeting:{meeting_id}``. So this bridge reads THAT feed, never the raw stream,
for the one meeting it owns:

    our bot ──(transcription_segments)──▶ meeting-api collector ──(tc:meeting:{meeting_id})──▶ THIS bridge
                                                                         ──(tc:meeting:{native})──▶ terminal
                       (no dispatch — PRD decision 34: the transcript IS the product surface)

One meeting per process: the numeric meeting_id POST /bots returns (or ``--meeting-id``) names the feed,
and each segment is fanned onto ``tc:meeting:{native_id}`` (identical drafts skipped).

    VEXA_API_KEY=... python own_bot_bridge.py --meeting-url https://meet.google.com/abc-defg-hij

Env: VEXA_API_KEY (the stack API key; never logged). Flags: --gateway (POST /bots sink), --agent-api
(dispatch sink), --redis, --subject, --bot-name, --no-bot (needs --meeting-id), --idle-end N.
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

    # 1) launch OUR bot via the local meeting-api (gateway) — returns the numeric meeting_id we filter on
    meeting_id = args.meeting_id
    if not args.no_bot:
        res = _req("POST", args.gateway, "/bots", {
            "platform": platform, "native_meeting_id": native_id,
            "bot_name": args.bot_name, "language": args.language,
        }, key=KEY)
        meeting_id = str(res.get("id") or res.get("meeting_id") or "")
        print(f"[bridge] our bot requested → meeting_id={meeting_id} status={res.get('status')}", flush=True)

    # 2) follow the collector's verified feed for THIS meeting (only entries its own bot signed reach
    #    it) and fan its segments onto tc:meeting:{native_id}.
    if not meeting_id.isdigit():
        raise SystemExit("no numeric meeting_id to follow — pass --meeting-id with --no-bot")
    src = f"tc:meeting:{meeting_id}"
    last = "0-0"
    print(f"[bridge] following {src} (the collector's verified feed) → {out_stream}", flush=True)

    final_done: set[str] = set()    # segment_ids already finalized → never re-emit
    last_text: dict[str, str] = {}  # segment_id → last draft text (skip identical re-emits)
    base: list[float] = []  # first segment's start → normalize to meeting-relative seconds (the bot emits absolute)
    last_seg = time.monotonic()
    while True:
        if args.idle_end and time.monotonic() - last_seg > args.idle_end:
            print("[bridge] idle-end reached", flush=True)
            break
        try:
            resp = r.xread({src: last}, count=50, block=4000)
        except (redislib.exceptions.TimeoutError, redislib.exceptions.ConnectionError):
            continue  # blocking XREAD can raise on its own block window — just loop
        for _s, entries in resp or []:
            for msg_id, fields in entries:
                last = msg_id
                try:
                    p = json.loads(fields.get("payload") or "{}")
                except Exception:
                    continue
                t = p.get("type")
                if t == "session_end":
                    r.xadd(out_stream, {"payload": json.dumps({"type": "session_end", "uid": native_id})})
                    print("[bridge] session_end", flush=True)
                    return
                if t != "transcription":
                    continue
                # Fan BOTH drafts and finals: the terminal shows drafts live (dimmed) and upserts on
                # segment_id. We only skip identical
                # draft re-emits and anything after a segment is finalized.
                for seg in (p.get("segments") or []):
                    text = (seg.get("text") or "").strip()
                    if not text:
                        continue
                    completed = bool(seg.get("completed"))
                    sid = str(seg.get("segment_id") or f"{seg.get('start')}:{text[:16]}")
                    if sid in final_done:
                        continue                       # already locked in — ignore stragglers
                    if not completed and last_text.get(sid) == text:
                        continue                       # identical draft — don't spam the wire
                    last_text[sid] = text
                    if completed:
                        final_done.add(sid)
                        last_text.pop(sid, None)
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
                            "completed": completed, "language": seg.get("language", "en"), "segment_id": sid,
                        }],
                    }
                    r.xadd(out_stream, {"payload": json.dumps(out)})
                    last_seg = time.monotonic()
                    print(f"[seg {start_rel:6.1f}{'' if completed else '~'}] {out['segments'][0]['speaker']}: {text[:56]}", flush=True)


if __name__ == "__main__":
    main()
