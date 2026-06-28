"""transcription_watcher.py — the IN-PROCESS Integration (inbound watch → fire) that replaces the
standalone bridge container.

It runs as TWO daemon threads:

  (a) RELAY (``_run_relay``) — subscribe to the meeting-api collector's canonical change-only feed
      ``tc:meeting:{numeric}:mutable`` (``{confirmed:[…], pending:[…]}``, the SAME wire the dashboard
      renders) and re-fan each confirmed/pending segment onto the per-meeting feed ``tc:meeting:{native}``.
      We no longer RE-DERIVE the transcript from the raw stream here (that hand-rolled dedup lost lines
      when the bot recycled a ``segment_id`` across utterances) — we CONSUME the collector's already-
      correct, dashboard-consistent output (read-only: zero writes to the collector). The terminal and
      ``serve_meeting`` both read ``tc:meeting:{native}`` exactly as before, so both get the deduped feed.
  (b) ARM (``_run_arm``) — the jobs only the agent-api can do: tail ``transcription_segments`` to FREEZE
      one native routing key per meeting, REGISTER the live meeting, RE-ARM the copilot dispatch on
      transcript activity (spawn-or-touch, idempotent), and on first sight BACK-SEED ``tc:meeting:{native}``
      from the collector's durable store ``meeting:{numeric}:segments`` (the pubsub has no replay, so an
      in-progress meeting / agent-api restart shows its history immediately).

No extra container, no HTTP hop: it holds the Dispatcher directly. ``keymap`` (numeric → native, frozen by
the arm thread) and ``base`` (meeting-relative start) are shared with the relay thread.
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
    "people, companies, products, and projects worth tagging."
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
    """Spawn the watcher (arm + relay daemon threads) and return the arm thread (tests/introspection).

    ``keymap`` (numeric → frozen native key) and ``base`` (meeting-relative start anchor) are created
    here and SHARED by both threads: the arm thread writes them, the relay thread reads them."""
    keymap: dict[str, str] = {}   # numeric meeting_id → frozen native routing key (shared with relay)
    base: dict[str, float] = {}   # native key → first segment start (meeting-relative normalization)
    t = threading.Thread(
        target=_run_arm, args=(redis_url, dispatcher, live, subject, keymap, base),
        daemon=True, name="tx-watch",
    )
    t.start()
    threading.Thread(
        target=_run_relay, args=(redis_url, keymap, base), daemon=True, name="tx-relay",
    ).start()
    return t


def _run_arm(redis_url: str, dispatcher, live, subject: str, keymap: dict, base: dict) -> None:
    """Inbound watch → freeze native key, register live, re-arm copilot, and back-seed the feed from the
    collector store on first sight. Does NOT fan raw segments — the relay fans the collector's :mutable."""
    import redis as redislib

    r = redislib.from_url(redis_url, decode_responses=True, socket_keepalive=True, health_check_interval=10)
    # id="$": only segments produced AFTER we start — never replay prior/ended meetings on (re)start.
    try:
        r.xgroup_create(SRC, GROUP, id="$", mkstream=True)
    except redislib.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    last_arm: dict[str, float] = {}     # native key → last spawn-or-touch (monotonic)
    first_seen: dict[str, float] = {}   # numeric meeting_id → first segment time (resolve-grace window)
    seeded: set[str] = set()            # native keys already back-seeded from the collector store
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
                            last_arm, base, keymap, first_seen, seeded)
                except Exception:  # noqa: BLE001
                    logger.exception("bad transcription frame; skipping")


# ── the relay: consume the meeting-api collector's canonical :mutable feed, re-fan to the native key ──

MUTABLE_PATTERN = "tc:meeting:*:mutable"   # the collector's change-only pubsub (services/redis.md)


def _fan_segment(r, out_stream: str, native: str, base: dict, seg: dict) -> None:
    """Re-fan ONE canonical collector segment onto ``tc:meeting:{native}`` in the per-segment wire shape
    the terminal SSE + ``serve_meeting`` already consume. The collector already deduped/normalized it
    (unique ids, garbage finals dropped), so we add no dedup of our own — just re-key + relative-anchor."""
    text = (seg.get("text") or "").strip()
    sid = seg.get("segment_id")
    if not text or not sid:
        return
    raw = float(seg.get("start") or 0.0)
    b = base.setdefault(native, raw)
    start_rel = max(0.0, raw - b)
    out = {
        "type": "transcription", "session_uid": native, "meeting_id": native,
        "segments": [{
            "speaker": seg.get("speaker") or "Speaker", "text": text,
            "start": round(start_rel, 1), "end": round(max(start_rel, float(seg.get("end") or raw) - b), 1),
            "abs_start_ms": round(raw * 1000),
            "absolute_start_time": seg.get("absolute_start_time"),
            "completed": bool(seg.get("completed")), "language": seg.get("language") or "en",
            "segment_id": sid,
        }],
    }
    r.xadd(out_stream, {"payload": json.dumps(out)})


def _seed_from_store(r, numeric: str, native: str, base: dict) -> None:
    """Back-seed ``tc:meeting:{native}`` from the collector's durable store ``meeting:{numeric}:segments``
    (the full transcript so far), in ascending start order. The :mutable pubsub has no replay, so this is
    how an in-progress meeting (or an agent-api restart) shows its history without a gap. Idempotent:
    re-fanning a segment_id the terminal already has is an upsert."""
    out_stream = f"tc:meeting:{native}"
    try:
        raw = r.hgetall(f"meeting:{numeric}:segments")
    except Exception:  # noqa: BLE001 — seeding is best-effort; the live relay still fills the feed
        logger.exception("seed read failed for meeting:%s:segments", numeric)
        return
    segs = []
    for v in (raw.values() if isinstance(raw, dict) else []):
        try:
            segs.append(json.loads(v))
        except Exception:  # noqa: BLE001 — skip a single bad stored segment
            continue
    segs.sort(key=lambda s: float(s.get("start") or 0.0))
    for seg in segs:
        _fan_segment(r, out_stream, native, base, seg)


def _relay_message(r, keymap: dict, base: dict, data: dict) -> None:
    """Relay ONE collector ``:mutable`` delta onto ``tc:meeting:{native}``. Re-keys numeric → native via
    the arm thread's frozen ``keymap``; a delta whose meeting isn't keyed yet is skipped (the arm thread's
    store-seed back-fills it). Confirmed AND pending are both fanned — the collector already deduped, so
    every segment it sends is relayed faithfully (no drop), keyed by its own unique ``segment_id``."""
    meeting = data.get("meeting") or {}
    numeric = str(meeting.get("id") or "")
    # Prefer the native id the collector STAMPED on the payload — it owns the meetings table and is not
    # user-scoped, so it resolves cross-user where the keymap (built off the user-scoped /meetings list)
    # cannot. Fall back to the frozen keymap for older payloads that don't carry it.
    native = meeting.get("native_id") or keymap.get(numeric)
    if not native:
        return
    # Cache the cross-user mapping so the arm thread's copilot keying converges on the native id too.
    if numeric and meeting.get("native_id"):
        keymap.setdefault(numeric, native)
    out_stream = f"tc:meeting:{native}"
    for seg in (data.get("confirmed") or []):
        _fan_segment(r, out_stream, native, base, seg)
    for seg in (data.get("pending") or []):
        _fan_segment(r, out_stream, native, base, seg)


def _run_relay(redis_url: str, keymap: dict, base: dict) -> None:
    """Subscribe to the collector's ``tc:meeting:*:mutable`` and relay each delta via ``_relay_message``."""
    import redis as redislib

    while True:
        try:
            r = redislib.from_url(redis_url, decode_responses=True, socket_keepalive=True, health_check_interval=10)
            ps = r.pubsub(ignore_subscribe_messages=True)
            ps.psubscribe(MUTABLE_PATTERN)
            logger.info("transcription relay up — consuming collector %s", MUTABLE_PATTERN)
            for msg in ps.listen():
                if msg.get("type") != "pmessage":
                    continue
                try:
                    _relay_message(r, keymap, base, json.loads(msg.get("data") or "{}"))
                except Exception:  # noqa: BLE001 — never die on one bad frame
                    logger.exception("bad :mutable frame; skipping")
        except (redislib.exceptions.TimeoutError, redislib.exceptions.ConnectionError):
            time.sleep(1)
        except Exception:  # noqa: BLE001 — keep the relay alive across transient pubsub errors
            logger.exception("relay loop error; retrying")
            time.sleep(1)


RESOLVE_GRACE_SEC = 6.0  # how long to wait for a native id before falling back to the numeric key


def _handle(r, dispatcher, live, subject, p, last_arm, base, keymap, first_seen, seeded) -> None:
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
        seeded.discard(key)
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
    # Processing is OPT-IN per meeting: only arm / keep-alive the copilot while the user has enabled it
    # (the terminal sets ``proc:meeting:{key}`` via /api/meeting/process). Default OFF → no copilot →
    # no processing; the RAW transcript still flows through the relay/seed above. The initial full-history
    # backfill is dispatched by the endpoint; here we just keep it alive while processing stays on.
    now = time.monotonic()
    # The opt-in flag is ``proc:meeting:{key}:on`` — a DISTINCT key from the processed-notes stream
    # ``proc:meeting:{key}`` (a GET on that stream raises WRONGTYPE and would crash this arm loop).
    if r.get(f"proc:meeting:{key}:on") and now - last_arm.get(key, 0.0) > REARM_SEC:
        last_arm[key] = now
        _arm(dispatcher, subject, key, platform, transcript_start_id=transcript_start_id)

    # (b) transcript CONTENT is the relay's job (it fans the collector's canonical confirmed/pending
    #     deltas from :mutable). Here we only BACK-SEED the feed ONCE per meeting, from the collector's
    #     durable store, so an in-progress meeting (or an agent-api restart) shows its history immediately
    #     — the pubsub relay has no replay. The relay then keeps it live.
    if key not in seeded:
        seeded.add(key)
        _seed_from_store(r, mid, key, base)


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
