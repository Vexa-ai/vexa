"""transcription_watcher.py — the agent's IN-PROCESS inbound watch over the live transcript.

It runs ONE daemon thread (``_run_arm``): tail ``transcription_segments`` purely as a TRIGGER to do the
jobs only the agent-api can do — REGISTER the live meeting under its meetings-domain numeric ROW id, and
on ``session_end`` drop it and connect the meeting's kg doc.

It no longer dispatches anything. PRD decision 34 removed the in-product inference pipeline, and this
loop's other half was its arbiter: it armed and kept alive a per-meeting "copilot" worker while a
``proc:meeting:{row}:on`` flag was set, and reaped it at the end. There is no such worker, no flag, and
no processed-notes stream any more — the agent reaches a meeting over the MCP, on a human's turn.

P0 (cross-tenant leak fix): the transcript CARRIER + ``:on`` + ``:cursor`` + dispatch keys are the numeric
ROW id ``mid`` (unique per (user, platform, native, run)), NOT the native Meet code (which collides across
DIFFERENT users AND across ONE user's re-sends — keying transcript data by it leaked one user's transcript
to another). The native code is resolved best-effort for DISPLAY only (the kg doc/title + the ``native_id``
field); a resolution miss no longer diverges the carrier key.

It does NOT write the transcript carrier. The MEETINGS domain (meeting-api's collector) is the SINGLE
writer of the per-meeting feed ``tc:meeting:{row_id}`` and its ``session_end`` marker (P23) — the agent only
CONSUMES it. `meetings ⊥ agent` (P3): the agent re-derives nothing. ``keymap`` (numeric meeting_id → row-id routing
key) is the arm thread's own state.

No extra container, no HTTP hop: it holds the Dispatcher directly.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from shared import units

logger = logging.getLogger("agent_api.tx_watch")

SRC = "transcription_segments"           # the wire every bot publishes to (configurable upstream)
GROUP = "agent_copilot"                  # our consumer group — independent of the collector's
                                         # (the name is the redis group's identity; renaming it would
                                         # orphan every in-flight PEL entry on a live deployment)
_PLATFORM = {"google_meet": "Google Meet", "teams": "Microsoft Teams", "zoom": "Zoom", "jitsi": "Jitsi Meet"}
_native: dict[str, tuple[str, str]] = {}  # numeric meeting_id → (native_meeting_id, platform), cached
# Only the meeting_id whose row we actually matched is cached above. A MISS is NOT cached (so it is
# retried on the next segment — the new meeting's row may not be visible in the gateway list yet),
# but we throttle the refetch per meeting_id so a quiet miss doesn't hammer the gateway every segment.
_resolve_miss_at: dict[str, float] = {}  # numeric meeting_id → last failed-resolve (monotonic)
RESOLVE_RETRY_SEC = 3.0
# The gateway/meeting-api caps `limit` at 100 (>100 → HTTP 422 Unprocessable Entity). Asking for more
# made EVERY resolve fail, so _resolve_native always returned None. Post-P0 the carrier no longer
# depends on this resolve (it keys on the row id `mid`, always present) — a miss now degrades only
# the human-readable native DISPLAY, never the transcript itself. Keep at/under the cap. (Pagination
# isn't needed: live meetings are always among the newest rows, which the gateway returns first.)
MEETINGS_LIST_LIMIT = 100

# ── P18 (ADR 0010) — fail loud & attributable: the relay's observable health ─────────────────────────
# The transcript relay used to fail SILENTLY: a stale VEXA_BOT_API_KEY made GET /meetings 401, native
# resolution failed, segments fell back to the numeric key, and the native feed stayed empty —
# logged once as "native-id resolve failed" then retried quietly forever. P18: a dependency failure is a
# TYPED fault surfaced on an OBSERVABLE channel, and "absence of an expected signal is itself a reportable
# state." `relay_health()` is that channel (read by /api/meeting/relay-health → the control panel).
_relay_health: dict = {
    "native_resolve": {"ok": True, "kind": None, "detail": None, "at": None, "misses": 0},
    "ingest": {"ok": True, "last_segment_at": None, "segments": 0},
}
_HEALTH_LOCK = threading.Lock()


def relay_health() -> dict:
    """A cheap snapshot of the transcript relay's health (P18 observable). True == flowing."""
    with _HEALTH_LOCK:
        return {k: dict(v) for k, v in _relay_health.items()}


def _classify_http(status: int) -> str:
    if status in (401, 403):
        return "unauthorized"
    if status == 402:
        return "payment_required"
    if status == 429:
        return "rate_limited"
    if status == 422:
        return "bad_request"
    if status >= 500:
        return "unavailable"
    return "error"


def _report_fault(stage: str, kind: str, detail: str) -> None:
    """Fail LOUD + attributed (P18). Record the typed fault and log at ERROR with an ESCALATING throttle
    (scream the first couple, then keep visible without flooding every 3s)."""
    with _HEALTH_LOCK:
        h = _relay_health.setdefault(stage, {"ok": True, "kind": None, "detail": None, "at": None, "misses": 0})
        h.update(ok=False, kind=kind, detail=detail, at=time.time(), misses=int(h.get("misses", 0)) + 1)
        n = h["misses"]
    if n <= 2 or n % 30 == 0:
        logger.error("RELAY FAULT [%s] %s — %s (occurrence #%d)", stage, kind, detail, n)


def _clear_fault(stage: str) -> None:
    """Mark a stage healthy again (loud once on recovery)."""
    with _HEALTH_LOCK:
        h = _relay_health.get(stage)
        recovered = bool(h and not h.get("ok", True))
        misses = int(h.get("misses", 0)) if h else 0
        _relay_health[stage] = {"ok": True, "kind": None, "detail": None, "at": time.time(), "misses": 0}
    if recovered:
        logger.info("RELAY RECOVERED [%s] after %d failure(s)", stage, misses)


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
        _report_fault("native_resolve", "unauthorized",
                      "VEXA_BOT_API_KEY not set — cannot resolve numeric→native meeting id")
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
    except urllib.error.HTTPError as e:
        # P18: a TYPED, ATTRIBUTED fault — not a swallowed "best-effort" miss. 401/403 almost always means
        # the bot key is stale/invalid (e.g. after a DB wipe), which is exactly the 90-minute mystery.
        kind = _classify_http(e.code)
        hint = " — VEXA_BOT_API_KEY is stale/invalid for this stack" if kind == "unauthorized" else ""
        _report_fault("native_resolve", kind, f"GET {gw}/meetings → HTTP {e.code}{hint}")
        _resolve_miss_at[meeting_id] = now
        return None
    except Exception as e:  # noqa: BLE001 — network/parse fault: still surface it, never swallow
        _report_fault("native_resolve", "unavailable",
                      f"GET {gw}/meetings failed: {type(e).__name__}: {e}")
        _resolve_miss_at[meeting_id] = now
        return None
    hit = _native.get(meeting_id)
    if hit is None:
        _resolve_miss_at[meeting_id] = now  # our id wasn't in the list yet — retry shortly (not a fault)
    else:
        _clear_fault("native_resolve")      # reachable + resolved → relay healthy again
    return hit


def _record_meeting_doc(native: str, platform: str, subject: str) -> None:
    """Best-effort: connect the meeting's own kg doc ref to the meeting on session_end, via the
    gateway (X-API-Key). Recorded from the watcher — NOT any isolated worker — so the user key never
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


def start(redis_url: str, dispatcher, live, *, subject: str = "u_live") -> threading.Thread:
    """Spawn the watcher daemon thread and return it (tests/introspection). ``keymap``
    (numeric meeting_id → row-id routing key) is the thread's own state.

    ``subject`` is a PRE-M2 placeholder (defaults to ``u_live``): the kg-doc connect below is attributed
    to this one subject. ``dispatcher`` is retained on the signature (every caller passes it, and the
    kg-doc path may yet need one) but nothing here dispatches any more."""
    keymap: dict[str, str] = {}
    t = threading.Thread(
        target=_run_arm, args=(redis_url, dispatcher, live, subject, keymap),
        daemon=True, name="tx-watch",
    )
    t.start()
    return t


def _run_arm(redis_url: str, dispatcher, live, subject: str, keymap: dict) -> None:
    """Inbound watch → key on the row id, register live, drop on session_end. Does NOT write the
    transcript carrier — meeting-api's collector owns ``tc:meeting:{row_id}`` (P23/P0)."""
    import redis as redislib

    r = redislib.from_url(redis_url, decode_responses=True, socket_keepalive=True, health_check_interval=10)
    # id="$": only segments produced AFTER we start — never replay prior/ended meetings on (re)start.
    try:
        r.xgroup_create(SRC, GROUP, id="$", mkstream=True)
    except redislib.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
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
                            keymap, first_seen)
                except Exception:  # noqa: BLE001
                    logger.exception("bad transcription frame; skipping")


RESOLVE_GRACE_SEC = 6.0  # how long to wait for a native id before falling back to the numeric key


def _handle(r, dispatcher, live, subject, p, keymap, first_seen) -> None:
    # P0 (cross-tenant leak fix): the TRANSCRIPT CARRIER keys on the numeric ROW id `mid` — NOT the
    # native Meet code. The native id is NOT unique (it collides across DIFFERENT
    # users and across ONE user's re-sends of the same link), so keying transcript data by it leaked one
    # user's transcript to another and hydrated the wrong row. The bot stamps a NUMERIC meeting_id (the
    # meetings-domain row id, unique per run) on every segment, so we can key on it IMMEDIATELY — no
    # resolve-grace wait, no gateway round-trip on the hot path.
    #
    # The native code is still resolved (best-effort) but ONLY for DISPLAY: the kg doc (`_record_meeting_doc`),
    # the human-readable title, and the `native_id` field on the live entry / meeting_ref. A resolution
    # miss no longer diverges the carrier key (that is `mid`, always present) — it only degrades display,
    # so the P18 relay-health fault is still reported (display only) but the transcript never leaks/starves.
    mid = str(p.get("meeting_id") or p.get("uid") or "")
    if not mid:
        return
    # PREFER the native id stamped on the segment by its producer (the bot knows it from its invocation).
    # The gateway lookup is only a labeled fallback for older bots that don't stamp it — and now purely a
    # DISPLAY concern (the carrier keys on `mid` regardless).
    stamped = p.get("native_meeting_id") or p.get("native_id")
    if stamped:
        resolved = (str(stamped), p.get("platform") or "google_meet")
    else:
        resolved = _resolve_native(mid)
    native, platform = resolved if resolved else (mid, p.get("platform") or "google_meet")
    if resolved is None and p.get("type") != "session_end":
        # DISPLAY-only divergence: the terminal still keys transcript data on the row id `mid`
        # (correct + isolated) — only the human-readable native code/title is unavailable until the
        # gateway row surfaces. Report it (P18) but do NOT hold or fork the meeting.
        _report_fault("native_resolve", "unresolved_display",
                      f"meeting {mid}: native id not resolved yet — transcript keyed on row id "
                      f"tc:meeting:{mid} (correct); the human-readable native code/title is pending")
    # The routing key is the numeric ROW id, frozen once per meeting_id (mid is stable, so this is
    # trivially stable — kept for structural parity with the reap path below).
    key = keymap.get(mid)
    if key is None:
        key = keymap[mid] = mid
    kind = p.get("type")
    if kind == "transcription":  # P18 liveness: record that segments ARE arriving (distinct from relayed)
        with _HEALTH_LOCK:
            ing = _relay_health["ingest"]
            ing["last_segment_at"] = time.time()
            ing["segments"] = int(ing.get("segments", 0)) + 1
    out_stream = f"tc:meeting:{key}"
    if kind == "session_end":
        # The collector emits the session_end MARKER onto tc:meeting:{row_id} (P23/P0, single writer);
        # the agent only does its OWN bookkeeping here — drop the live row (by the row-id key we
        # registered it under), clear keymap, connect the kg doc (native, for display).
        live.drop(key)
        keymap.pop(mid, None)
        first_seen.pop(mid, None)
        logger.info("meeting %s ended", key)
        # Connect this meeting's own kg doc ref to the meeting — from here, so the user key stays
        # out of any isolated worker container.
        _record_meeting_doc(native, platform, subject)
        return
    if kind != "transcription":
        return

    # Keep the terminal's live feed fresh on EVERY batch (a cheap dict write) so an agent-api restart
    # can't drop the meeting from the list — it reappears on the first segment. session_uid == the ROW
    # id `mid` too, so the terminal's SSE subscribes with the same id the transcript carrier
    # (tc:meeting:{mid}) is keyed by.
    live.add({
        "meeting_id": key, "session_uid": key, "native_id": native, "platform": platform,
        "title": _title(platform, native),
        # The meetings-domain ROW id (unique per meeting run) — the ROUTING key itself.
        "numeric_meeting_id": mid if mid.isdigit() else None,
    })


