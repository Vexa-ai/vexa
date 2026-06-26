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
import os
import threading
import time
import urllib.error
import urllib.request

from . import units

logger = logging.getLogger("agent_api.tx_watch")

SRC = "transcription_segments"           # the wire every bot publishes to (configurable upstream)
GROUP = "agent_copilot"                  # our consumer group — independent of the collector's
REARM_SEC = 30.0                         # re-touch a meeting's dispatch at most this often (keep-alive)
_BRIEF = (
    "You are the live meeting copilot. Watch the meeting transcript as it streams in and surface the "
    "people, companies, topics, decisions, and action items worth acting on."
)
_PLATFORM = {"google_meet": "Google Meet", "teams": "Microsoft Teams", "zoom": "Zoom"}
_native: dict[str, tuple[str, str]] = {}  # numeric meeting_id → (native_meeting_id, platform), cached
# Only the meeting_id whose row we actually matched is cached above. A MISS is NOT cached (so it is
# retried on the next segment — the new meeting's row may not be visible in the gateway list yet),
# but we throttle the refetch per meeting_id so a quiet miss doesn't hammer the gateway every segment.
_resolve_miss_at: dict[str, float] = {}  # numeric meeting_id → last failed-resolve (monotonic)
RESOLVE_RETRY_SEC = 3.0
# The gateway/meeting-api caps `limit` at 100 (>100 → HTTP 422 Unprocessable Entity). Asking for more
# made EVERY resolve fail, so _resolve_native always returned None → the watcher fell back to the
# numeric key (tc:meeting:17) while the terminal listens on the native key (tc:meeting:<native>) — the
# transcript never reached the UI. Keep at/under the cap. (Pagination isn't needed: live meetings are
# always among the newest rows, which the gateway returns first.)
MEETINGS_LIST_LIMIT = 100


def _title(platform: str, native: str) -> str:
    return f"{_PLATFORM.get(platform, platform)} · {native}"


def _resolve_native(meeting_id: str) -> "tuple[str, str] | None":
    """Map the bot's NUMERIC meeting_id → its native Meet code (e.g. nba-agyz-gbe) via the gateway, so
    the wire/dispatch/feed key on ONE id per physical meeting (re-launches dedupe to one entry) — and the
    terminal can stop the bot by its native id.

    Cache discipline (the multi-meeting-collapse fix): we cache ONLY the exact meeting_id→native pair we
    matched, and we ONLY return the native for THIS meeting_id (never the first/any row in the list). A
    miss is left UNCACHED so it retries (the just-launched meeting's row can lag the gateway list by a
    beat), but throttled so a genuinely-unknown id doesn't refetch on every segment."""
    if meeting_id in _native:
        return _native[meeting_id]
    now = time.monotonic()
    if now - _resolve_miss_at.get(meeting_id, 0.0) < RESOLVE_RETRY_SEC:
        return None  # recently failed — don't refetch yet (caller keys on numeric id meanwhile)
    key = os.environ.get("VEXA_BOT_API_KEY", "")
    if not key:
        _resolve_miss_at[meeting_id] = now
        return None
    gw = os.environ.get("VEXA_GATEWAY_URL", "http://gateway:8000").rstrip("/")
    try:
        req = urllib.request.Request(
            gw + f"/meetings?limit={MEETINGS_LIST_LIMIT}", headers={"X-API-Key": key})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode() or "{}")
        items = data if isinstance(data, list) else (data.get("meetings") or data.get("items") or [])
        for mt in items:
            mid = str(mt.get("id") or mt.get("meeting_id") or "")
            nat = mt.get("native_meeting_id") or mt.get("native_id") or mt.get("platform_specific_id")
            if mid and nat:
                _native[mid] = (nat, mt.get("platform") or "google_meet")
    except Exception:  # noqa: BLE001 — resolution is best-effort; caller falls back to the numeric id
        logger.exception("native-id resolve failed")
        _resolve_miss_at[meeting_id] = now
        return None
    hit = _native.get(meeting_id)
    if hit is None:
        _resolve_miss_at[meeting_id] = now  # our id wasn't in the list yet — retry shortly
    return hit


def _record_meeting_doc(native: str, platform: str, subject: str) -> None:
    """Best-effort: connect the meeting's own kg doc ref to the meeting on session_end, via the
    gateway (X-API-Key). Recorded from the watcher — NOT the isolated worker — so the user key never
    enters the agent container. MUST NEVER raise: a failure here can't be allowed to crash the
    watcher, so everything is wrapped and merely logged."""
    try:
        key = os.environ.get("VEXA_BOT_API_KEY", "")
        if not key:
            return
        gw = os.environ.get("VEXA_GATEWAY_URL", "http://gateway:8000").rstrip("/")
        body = json.dumps({
            "workspace": subject,
            "path": f"kg/entities/meeting/{native}.md",
            "title": native,
            "kind": "meeting",
        }).encode()
        url = f"{gw}/meetings/{platform}/{native}/docs"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"X-API-Key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:  # noqa: BLE001 — recording the doc ref is best-effort; never crash the watcher
        logger.exception("connect meeting doc ref failed for %s/%s", platform, native)


def _stream_tail_id(r, stream: str) -> str:
    """Return the current Redis Stream tail id, or ``0-0`` when the stream has no entries."""
    try:
        rows = r.xrevrange(stream, "+", "-", count=1)
    except Exception:  # noqa: BLE001 — cursoring is best-effort; an empty cursor is still valid
        logger.exception("stream tail lookup failed for %s", stream)
        return "0-0"
    if not rows:
        return "0-0"
    return str(rows[0][0])


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
    keymap: dict[str, str] = {}         # numeric meeting_id → the routing key chosen on first sight
    first_seen: dict[str, float] = {}   # numeric meeting_id → first segment time (resolve-grace window)
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
                            last_arm, base, final_done, last_text, keymap, first_seen)
                except Exception:  # noqa: BLE001
                    logger.exception("bad transcription frame; skipping")


RESOLVE_GRACE_SEC = 6.0  # how long to wait for a native id before falling back to the numeric key


def _handle(r, dispatcher, live, subject, p, last_arm, base, final_done, last_text, keymap, first_seen) -> None:
    # The bot stamps a NUMERIC meeting_id on every segment — but each re-launch of the SAME Meet gets a
    # fresh numeric id. Resolve it to the native Meet code so the wire/dispatch/feed key on ONE id per
    # physical meeting (re-launches dedupe to a single entry). Fall back to numeric if resolution fails.
    #
    # CRITICAL (multi-meeting fix): the routing key is decided ONCE per numeric meeting_id and frozen in
    # `keymap`. Without this, a meeting whose native resolves only on a LATER segment (the gateway row
    # lags the first segments) would flip from the numeric key to the native key mid-stream — forking it
    # into two streams/copilots, or, if a stale/shared fallback was used, fanning several meetings onto
    # one key. Freezing the first stable key keeps every distinct meeting a SEPARATE stream/copilot/entry.
    mid = str(p.get("meeting_id") or p.get("uid") or "")
    if not mid:
        return
    resolved = _resolve_native(mid)
    native, platform = resolved if resolved else (mid, p.get("platform") or "google_meet")
    key = keymap.get(mid)
    if key is None:
        if resolved is None and p.get("type") != "session_end":
            # Not yet resolved AND not the end — wait (briefly) for the native id rather than committing
            # this meeting to its numeric key for life (which would diverge from the terminal's native
            # key). Bounded by RESOLVE_GRACE_SEC so a gateway that never resolves still surfaces the
            # meeting under its numeric id instead of swallowing it forever.
            seen = first_seen.setdefault(mid, time.monotonic())
            if time.monotonic() - seen < RESOLVE_GRACE_SEC:
                return
        key = keymap[mid] = native
    kind = p.get("type")
    out_stream = f"tc:meeting:{key}"
    if kind == "session_end":
        r.xadd(out_stream, {"payload": json.dumps({"type": "session_end", "uid": key})})
        live.drop(key)
        base.pop(key, None)
        last_arm.pop(key, None)
        keymap.pop(mid, None)
        first_seen.pop(mid, None)
        logger.info("meeting %s ended → reaping copilot", key)
        # Connect this meeting's own kg doc (authored by the §4 worker on session_end) to the
        # meeting — from here, so the user key stays out of the isolated worker container.
        _record_meeting_doc(native, platform, subject)
        return
    if kind != "transcription":
        return

    transcript_start_id = _stream_tail_id(r, out_stream)

    # Keep the terminal's live feed fresh on EVERY batch (a cheap dict write) so an agent-api restart
    # can't drop the meeting from the list — it reappears on the first segment. Throttle only the spawn.
    live.add({
        "meeting_id": key, "session_uid": key, "native_id": native, "platform": platform,
        "title": _title(platform, native), "unit_id": f"agent-meet-{key}",
    })
    now = time.monotonic()
    if now - last_arm.get(key, 0.0) > REARM_SEC:
        last_arm[key] = now
        _arm(dispatcher, subject, key, platform, transcript_start_id=transcript_start_id)

    # (b) fan segments (drafts + finals) onto the per-meeting wire
    for seg in p.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        completed = bool(seg.get("completed"))
        # Scope the dedup id by the meeting key: each bot numbers its OWN segments (0,1,2…), so a bare
        # segment_id collides ACROSS meetings — without the key prefix, meeting B's "seg-3" would be
        # dropped as a duplicate of meeting A's "seg-3" (another facet of the multi-meeting collapse).
        raw_sid = seg.get("segment_id") or f"{seg.get('start')}:{text[:16]}"
        sid = f"{key}:{raw_sid}"
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


def _arm(dispatcher, subject: str, key: str, platform: str, *, transcript_start_id: str = "0-0") -> None:
    """Spawn-or-touch the meeting's copilot (keyed agent-meet-{key}). Idempotent: spawns if reaped,
    touches (keep-alive) if already running. The live-feed registration happens in _handle every batch."""
    inv = units.make_dispatch(
        subject=subject, trigger="transcription",
        start=units.entrypoint(inline=_BRIEF),
        context={"kind": "meeting", "meeting": {
            "meeting_id": key, "session_uid": key, "platform": platform,
            "transcript_start_id": transcript_start_id,
        }},
    )
    try:
        dispatcher.dispatch(inv)  # idempotent: spawns if reaped, touches if running
    except Exception:  # noqa: BLE001
        logger.exception("dispatch failed for meeting %s", key)
