"""transcription_watcher.py — the IN-PROCESS Integration (inbound watch → fire) that replaces the
standalone bridge container.

A daemon thread tails the bot's shared ``transcription_segments`` stream (the wire every self-hosted bot
publishes) and, per meeting, does the two jobs the bridge used to:

  (a) FAN each segment onto the per-meeting wire ``tc:meeting:{uid}`` — drafts (``completed:false``) and
      finals both, so the terminal shows pending live (dimmed) while ``serve_meeting`` drops drafts.
  (b) RE-ARM the copilot dispatch on transcript activity (spawn-or-touch, idempotent on the unit id) —
      so the agent is spawned the moment speech starts (not before admission, when it would idle-reap)
      and stays alive while the meeting is talking, TTL-reaping only when it truly goes quiet.

No extra container, no HTTP hop: it holds the Dispatcher directly. Keyed on the bot's ``uid`` (session
uid), which IS in the payload — so ``meeting_id == session_uid == uid`` and the wire, the dispatch
(``agent-meet-{uid}``), and the terminal all agree without a meeting-api lookup.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from . import units

logger = logging.getLogger("agent_api.tx_watch")

SRC = "transcription_segments"           # the wire every bot publishes to (configurable upstream)
GROUP = "agent_copilot"                  # our consumer group — independent of the collector's
REARM_SEC = 30.0                         # re-touch a meeting's dispatch at most this often (keep-alive)
_BRIEF = (
    "You are the live meeting copilot. Watch the meeting transcript as it streams in and surface the "
    "people, companies, topics, decisions, and action items worth acting on."
)


def start(redis_url: str, dispatcher, live, *, subject: str = "u_live") -> threading.Thread:
    """Spawn the watcher as a daemon thread. Returns it (mostly for tests/introspection)."""
    t = threading.Thread(
        target=_run, args=(redis_url, dispatcher, live, subject), daemon=True, name="tx-watch",
    )
    t.start()
    return t


def _run(redis_url: str, dispatcher, live, subject: str) -> None:
    import redis as redislib

    r = redislib.from_url(redis_url, decode_responses=True, socket_keepalive=True, health_check_interval=10)
    # id="$": only segments produced AFTER we start — never replay prior/ended meetings on (re)start.
    try:
        r.xgroup_create(SRC, GROUP, id="$", mkstream=True)
    except redislib.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    last_arm: dict[str, float] = {}     # uid → last spawn-or-touch (monotonic)
    base: dict[str, float] = {}         # uid → first segment start (normalize to meeting-relative)
    final_done: set[str] = set()        # segment_ids finalized → never re-emit
    last_text: dict[str, str] = {}      # segment_id → last draft text (skip identical re-emits)
    logger.info("transcription watcher up — consuming %s (group=%s)", SRC, GROUP)

    while True:
        try:
            resp = r.xreadgroup(GROUP, "agent-api", {SRC: ">"}, count=50, block=5000)
        except (redislib.exceptions.TimeoutError, redislib.exceptions.ConnectionError):
            continue
        except Exception:  # noqa: BLE001 — a watcher must never die on a bad frame
            logger.exception("xreadgroup failed; retrying")
            time.sleep(1)
            continue
        for _stream, entries in resp or []:
            for msg_id, fields in entries:
                try:
                    r.xack(SRC, GROUP, msg_id)
                    _handle(r, dispatcher, live, subject, json.loads(fields.get("payload") or "{}"),
                            last_arm, base, final_done, last_text)
                except Exception:  # noqa: BLE001
                    logger.exception("bad transcription frame; skipping")


def _handle(r, dispatcher, live, subject, p, last_arm, base, final_done, last_text) -> None:
    # Key on the meeting id the bot stamps on every segment (uid is not always present); the wire,
    # dispatch, and terminal all agree on this one id.
    key = str(p.get("meeting_id") or p.get("uid") or "")
    if not key:
        return
    kind = p.get("type")
    out_stream = f"tc:meeting:{key}"
    if kind == "session_end":
        r.xadd(out_stream, {"payload": json.dumps({"type": "session_end", "uid": key})})
        live.drop(key)
        base.pop(key, None)
        last_arm.pop(key, None)
        logger.info("meeting %s ended → reaping copilot", key)
        return
    if kind != "transcription":
        return

    # (a) re-arm the dispatch on activity (idempotent spawn-or-touch keeps the worker alive)
    now = time.monotonic()
    if now - last_arm.get(key, 0.0) > REARM_SEC:
        last_arm[key] = now
        _arm(dispatcher, live, subject, key, p.get("platform") or "google_meet")

    # (b) fan segments (drafts + finals) onto the per-meeting wire
    for seg in p.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        completed = bool(seg.get("completed"))
        sid = str(seg.get("segment_id") or f"{key}:{seg.get('start')}:{text[:16]}")
        if sid in final_done:
            continue
        if not completed and last_text.get(sid) == text:
            continue
        last_text[sid] = text
        if completed:
            final_done.add(sid)
            last_text.pop(sid, None)
        raw = float(seg.get("start") or 0.0)
        b = base.setdefault(key, raw)
        start_rel = max(0.0, raw - b)
        out = {
            "type": "transcription", "session_uid": key, "meeting_id": key,
            "segments": [{
                "speaker": seg.get("speaker") or "Speaker", "text": text,
                "start": round(start_rel, 1), "end": round(max(start_rel, float(seg.get("end") or raw) - b), 1),
                "completed": completed, "language": seg.get("language", "en"), "segment_id": sid,
            }],
        }
        r.xadd(out_stream, {"payload": json.dumps(out)})


def _arm(dispatcher, live, subject: str, uid: str, platform: str) -> None:
    """Spawn-or-touch the meeting's copilot (keyed agent-meet-{uid}) and register it as live."""
    inv = units.make_dispatch(
        subject=subject, trigger="transcription",
        start=units.entrypoint(inline=_BRIEF),
        context={"kind": "meeting", "meeting": {
            "meeting_id": uid, "session_uid": uid, "platform": platform,
        }},
    )
    try:
        unit_id = dispatcher.dispatch(inv)  # idempotent: spawns if reaped, touches if running
    except Exception:  # noqa: BLE001
        logger.exception("dispatch failed for meeting %s", uid)
        return
    live.add({
        "meeting_id": uid, "session_uid": uid, "platform": platform,
        "title": f"{platform} meeting", "unit_id": unit_id,
    })
