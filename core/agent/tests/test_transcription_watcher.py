"""The in-process transcription watcher (ARM-only): keep DISTINCT meetings SEPARATE, arm the copilot
opt-in, and — crucially — NEVER write the transcript carrier.

Post-D7 (P23): meeting-api's collector is the SINGLE writer of ``tc:meeting:{native}`` (segments AND the
session_end marker). The agent watcher only tails ``transcription_segments`` as a TRIGGER to do agent-domain
jobs: freeze ONE native routing key per meeting, register the live row, re-arm the copilot while processing
is enabled (resuming from the worker-advanced cursor — the ONE resume source, ADR 0027), and reap on
session_end (live row, keymap, AND the desired-state flag). It writes nothing to the carrier —
`meetings ⊥ agent` (P3). These tests assert that behaviour via keymap/live/dispatch, and
that no ``tc:meeting:*`` write ever originates here.
"""
from __future__ import annotations

import json

import control_plane.transcription_watcher as w


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[dict]] = {}
        self.kv: dict[str, str] = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value

    def delete(self, key):
        self.kv.pop(key, None)

    def xadd(self, key, fields):
        self.streams.setdefault(key, []).append(fields)

    def xrevrange(self, key, _max="+", _min="-", count=None):
        rows = self.streams.get(key) or []
        if not rows:
            return []
        selected = list(reversed(rows))
        if count is not None:
            selected = selected[:count]
        return [(f"{len(rows) - i}-0", fields) for i, fields in enumerate(selected)]


class _FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[dict] = []

    def dispatch(self, inv):
        self.dispatched.append(inv)
        return "unit-id"


class _FakeLive:
    def __init__(self) -> None:
        self.by_uid: dict[str, dict] = {}

    def add(self, meeting):
        self.by_uid[meeting["session_uid"]] = dict(meeting)

    def drop(self, uid):
        self.by_uid.pop(uid, None)


def _payload(meeting_id):
    # Only meeting_id matters to the arm thread — transcript CONTENT is the collector's (P23).
    return {"type": "transcription", "meeting_id": meeting_id, "segments": [
        {"text": "hi", "completed": True, "start": 0.0, "end": 1.0, "segment_id": "x"}]}


def _fresh_state():
    return ({}, {}, {})  # last_arm, keymap, first_seen


def _reset_module_caches():
    w._native.clear()
    w._resolve_miss_at.clear()


def _native_streams(r):
    """The transcript-carrier streams — these must NEVER be written by the agent (the collector owns them)."""
    return [k for k in r.streams if k.startswith("tc:meeting:")]


# ── multi-meeting separation (resolution + keying) ─────────────────────────────────────────────────

def test_two_distinct_meetings_stay_separate(monkeypatch):
    """Two ROW ids → two separate live rows / copilot keys, keyed on the ROW id (P0 — the native id is
    NOT unique; keying by it collapsed/leaked). The natives still resolve for DISPLAY. The agent writes
    NO transcript stream (the collector owns it)."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: {
        "42": ("aaa-aaaa-aaa", "google_meet"),
        "43": ("bbb-bbbb-bbb", "google_meet"),
    }.get(mid))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = state = _fresh_state()
    for mid in ("42", "43", "42", "43"):
        w._handle(r, disp, live, "u_live", _payload(mid), *state)

    assert set(live.by_uid) == {"42", "43"}                       # live rows keyed by ROW id
    assert keymap == {"42": "42", "43": "43"}                     # routing key == the row id
    assert live.by_uid["42"]["native_id"] == "aaa-aaaa-aaa"       # native carried for DISPLAY
    assert live.by_uid["43"]["native_id"] == "bbb-bbbb-bbb"
    assert _native_streams(r) == []                               # agent wrote NO transcript carrier


def test_late_native_resolution_does_not_fork_or_collapse(monkeypatch):
    """Meeting 43's gateway row lags: native None first, resolved later. P0: the carrier keys on the ROW
    id `mid` from the FIRST segment (always present — no fork, no grace wait, no collapse). 43 never
    borrows 42's native. The native fills in on the DISPLAY (`native_id`) once the gateway row surfaces;
    it never changes the routing key."""
    _reset_module_caches()
    state = {"43": None}

    def resolve(mid):
        if mid == "42":
            return ("aaa-aaaa-aaa", "google_meet")
        return state["43"]

    monkeypatch.setattr(w, "_resolve_native", resolve)

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = st = _fresh_state()

    w._handle(r, disp, live, "u", _payload("42"), *st)
    w._handle(r, disp, live, "u", _payload("43"), *st)   # 43's native unresolved — still keyed on the ROW id
    assert set(live.by_uid) == {"42", "43"}              # BOTH live, each on its own row id
    assert keymap["43"] == "43"                           # keyed on the row id, never 42's native
    assert live.by_uid["43"]["native_id"] == "43"        # native pending → display falls back to the row id

    state["43"] = ("bbb-bbbb-bbb", "google_meet")
    w._handle(r, disp, live, "u", _payload("43"), *st)
    assert keymap["43"] == "43"                           # routing key UNCHANGED (no fork)
    assert live.by_uid["43"]["native_id"] == "bbb-bbbb-bbb"  # display native filled in


def test_resolve_native_returns_only_the_matched_id(monkeypatch):
    """_resolve_native must return the native for the EXACT meeting_id — never the first/any row."""
    _reset_module_caches()

    listing = {"meetings": [
        {"id": 43, "native_meeting_id": "bbb-bbbb-bbb", "platform": "google_meet", "status": "active"},
        {"id": 42, "native_meeting_id": "aaa-aaaa-aaa", "platform": "google_meet", "status": "active"},
    ]}

    class _Resp:
        def read(self): return json.dumps(listing).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setenv("VEXA_BOT_API_KEY", "k")
    monkeypatch.setattr(w.urllib.request, "urlopen", lambda req, timeout=5: _Resp())

    assert w._resolve_native("42") == ("aaa-aaaa-aaa", "google_meet")
    assert w._resolve_native("43") == ("bbb-bbbb-bbb", "google_meet")
    assert w._resolve_native("99") is None


def test_resolve_native_requests_limit_within_gateway_cap(monkeypatch):
    """The gateway rejects limit>100 (HTTP 422) — which made every resolve fail. Stay at/under the cap."""
    _reset_module_caches()

    captured: dict[str, str] = {}

    class _Resp:
        def read(self): return json.dumps({"meetings": []}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=5):
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setenv("VEXA_BOT_API_KEY", "k")
    monkeypatch.setattr(w.urllib.request, "urlopen", _fake_urlopen)

    w._resolve_native("42")
    requested = int(captured["url"].split("limit=")[1].split("&")[0])
    assert requested <= 100, f"gateway caps limit at 100; requested {requested} → HTTP 422 every call"


# ── copilot arming (opt-in) resumes from the ONE cursor — never the stream tail (ADR 0027) ──────────

def test_arm_resumes_from_the_frozen_cursor(monkeypatch):
    """The arm's transcript_start_id is the worker-advanced cursor (proc:meeting:{row}:cursor) — the
    SAME resume source the /process toggle reports. Arming from the feed TAIL here used to race the
    toggle's dispatch, and a tail-armed win silently skipped the backfill (run-46). Writes nothing."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    # The collector has already written 2 entries onto the ROW-keyed feed (simulated here).
    r.streams["tc:meeting:42"] = [{"payload": "c-1"}, {"payload": "c-2"}]
    r.set("proc:meeting:42:on", "1")        # processing is opt-in — enable it (ROW-keyed)
    r.set("proc:meeting:42:cursor", "1-0")  # the worker cleaned up to 1-0 before it was reaped

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())

    meeting = disp.dispatched[0]["context"]["meeting"]
    assert meeting["transcript_start_id"] == "1-0"                # the frozen cursor — gap-fill, no skip
    assert len(r.streams["tc:meeting:42"]) == 2                   # unchanged — the agent appended nothing


def test_arm_without_cursor_backfills_full_history(monkeypatch):
    """A never-processed meeting has no cursor ⇒ the arm starts from 0-0 (full-history backfill), even
    when the transcript already has entries — the tail is NOT a resume source."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    r.streams["tc:meeting:42"] = [{"payload": "c-1"}, {"payload": "c-2"}]  # history exists
    r.set("proc:meeting:42:on", "1")

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())

    assert disp.dispatched[0]["context"]["meeting"]["transcript_start_id"] == "0-0"


def test_copilot_processing_is_opt_in(monkeypatch):
    """Processing is OPT-IN: with no proc:meeting flag the copilot is NOT dispatched, yet the meeting still
    registers. Flipping the flag arms it. The agent writes no transcript stream either way."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())
    assert disp.dispatched == []                    # OFF → no copilot, no processing
    assert "42" in live.by_uid                      # …but the meeting still registers (by ROW id)
    assert _native_streams(r) == []                 # …and the agent writes no transcript carrier

    r.set("proc:meeting:42:on", "1")                   # user enables processing (ROW-keyed) → now it arms
    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())
    assert len(disp.dispatched) == 1


def test_unresolved_native_still_keys_on_row_id_immediately(monkeypatch):
    """P0: a meeting whose native NEVER resolves is NOT held — the ROW id `mid` keys the carrier from the
    FIRST segment (always present), so the transcript never leaks/starves. Only the human-readable native
    (display) is degraded to the row id until/unless the gateway row surfaces."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: None)   # native never resolves
    monkeypatch.setattr(w.time, "monotonic", lambda: 1000.0)

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = st = _fresh_state()

    w._handle(r, disp, live, "u", _payload("77"), *st)          # keyed IMMEDIATELY on the row id
    assert "77" in live.by_uid and keymap["77"] == "77"         # surfaced under the row id (not swallowed)
    assert live.by_uid["77"]["native_id"] == "77"              # display falls back to the row id


class _WrongTypeRedis(_FakeRedis):
    """A redis whose GET raises WRONGTYPE when the key is actually a STREAM — mirrors real redis. Proves
    the arm-loop reads the :on FLAG, never the proc:meeting:{key} processed-notes stream (the collision
    that crashed the loop before the flag was suffixed :on)."""
    def get(self, key):
        if key in self.streams:
            raise RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return self.kv.get(key)


def test_proc_flag_get_never_hits_the_processed_stream(monkeypatch):
    """With processing ON, the arm-loop GETs the :on flag, NOT the proc:meeting:{key} STREAM that coexists
    — so a real redis WRONGTYPE never crashes the loop."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("nat-77", "google_meet"))
    monkeypatch.setattr(w.time, "monotonic", lambda: 100000.0)   # > REARM_SEC since last_arm(0) → arms

    r, disp, live = _WrongTypeRedis(), _FakeDispatcher(), _FakeLive()
    r.xadd("proc:meeting:77", {"payload": "{}"})               # the ROW-keyed processed-notes STREAM (collision bait)
    r.set("proc:meeting:77:on", "1")                           # processing ENABLED via the ROW-keyed flag

    w._handle(r, disp, live, "u", _payload("77"), *_fresh_state())  # must NOT raise WRONGTYPE
    assert len(disp.dispatched) == 1                            # armed off the flag


# ── session_end reap (agent-domain only — the collector emits the carrier marker) ───────────────────

def test_session_end_reaps_copilot_without_writing_the_carrier(monkeypatch):
    """On session_end the agent does ONLY its own reaping: drop the live row, clear the keymap, connect
    the kg doc. It does NOT write the session_end marker onto tc:meeting:{native} — the collector owns
    that carrier (P23)."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("nat-9", "google_meet"))
    monkeypatch.delenv("VEXA_BOT_API_KEY", raising=False)   # _record_meeting_doc → no-op (no network)
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = st = _fresh_state()

    w._handle(r, disp, live, "u", _payload("9"), *st)        # establish the meeting (keyed by row id 9)
    assert "9" in live.by_uid and keymap.get("9") == "9"

    w._handle(r, disp, live, "u", {"type": "session_end", "meeting_id": "9"}, *st)
    assert "9" not in live.by_uid                            # live row dropped (by the row-id key)
    assert "9" not in keymap                                 # keymap cleared (clean relaunch)
    assert _native_streams(r) == []                          # the agent wrote NO session_end marker


def test_session_end_reaps_the_processing_flag(monkeypatch):
    """session_end also deletes the desired-state flag (proc:meeting:{row}:on) — the meeting is over, so
    a leftover flag must not survive to litter redis / re-arm a copilot for a dead meeting (ADR 0027:
    this watcher is the flag's end-of-life owner). The frozen cursor is intentionally LEFT in place."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("nat-9", "google_meet"))
    monkeypatch.delenv("VEXA_BOT_API_KEY", raising=False)
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    st = _fresh_state()
    r.set("proc:meeting:9:on", "1")
    r.set("proc:meeting:9:cursor", "5-0")

    w._handle(r, disp, live, "u", _payload("9"), *st)
    w._handle(r, disp, live, "u", {"type": "session_end", "meeting_id": "9"}, *st)

    assert r.get("proc:meeting:9:on") is None                # desired state reaped with the meeting
    assert r.get("proc:meeting:9:cursor") == "5-0"           # cursor frozen (audit / late gap-fill)


# ── P18 (ADR 0010) — fail-loud regression gates: the 90-minute incident as a red-then-green test ──────
def _reset_relay_health():
    w._relay_health["native_resolve"] = {"ok": True, "kind": None, "detail": None, "at": None, "misses": 0}


def test_native_resolve_401_fails_loud(monkeypatch):
    """A stale/invalid VEXA_BOT_API_KEY (401 on GET /meetings) MUST surface a typed, attributed fault on
    relay_health — never a silent best-effort miss. This is exactly the incident that took 90 minutes."""
    import urllib.error
    import urllib.request
    _reset_module_caches()
    _reset_relay_health()
    monkeypatch.setenv("VEXA_BOT_API_KEY", "stale-key")

    def _raise_401(*a, **k):
        raise urllib.error.HTTPError("http://gw/meetings", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise_401)

    assert w._resolve_native("1") is None
    h = w.relay_health()["native_resolve"]
    assert h["ok"] is False
    assert h["kind"] == "unauthorized"
    assert "VEXA_BOT_API_KEY" in (h["detail"] or "")
    assert h["misses"] >= 1


def test_native_resolve_missing_key_fails_loud(monkeypatch):
    """No VEXA_BOT_API_KEY at all is also a loud, attributed fault (not a silent return None)."""
    _reset_module_caches()
    _reset_relay_health()
    monkeypatch.delenv("VEXA_BOT_API_KEY", raising=False)
    assert w._resolve_native("1") is None
    h = w.relay_health()["native_resolve"]
    assert h["ok"] is False and h["kind"] == "unauthorized"


def test_native_resolve_recovers_clears_fault(monkeypatch):
    """A successful resolve after a fault clears health back to ok (loud recovery)."""
    import urllib.request
    _reset_module_caches()
    w._relay_health["native_resolve"] = {"ok": False, "kind": "unauthorized", "detail": "x", "at": 0.0, "misses": 3}
    monkeypatch.setenv("VEXA_BOT_API_KEY", "good-key")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"meetings": [
                {"id": "1", "native_meeting_id": "nba-agyz-gbe", "platform": "google_meet"}]}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert w._resolve_native("1") == ("nba-agyz-gbe", "google_meet")
    assert w.relay_health()["native_resolve"]["ok"] is True


def test_arm_carries_numeric_meeting_id_for_durable_proc_doc(monkeypatch):
    """The watcher knows the meetings-domain ROW id (the segments' numeric meeting_id) — the arm
    dispatch must carry it (numeric_meeting_id) so the worker keys its processed-notes stream by it
    (proc:meeting:{row_id}): unique per meeting run ⇒ a re-sent bot on the same native link never
    mixes/clobbers a previous meeting's processed doc, and the meeting-api db-writer can persist the
    stream into the meeting row's data JSONB. The live entry carries it too (for /api/meeting/process)."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    r.set("proc:meeting:42:on", "1")

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())

    meeting = disp.dispatched[0]["context"]["meeting"]
    assert meeting["numeric_meeting_id"] == "42"
    assert meeting["meeting_id"] == "42"                    # P0: routing keys by the ROW id
    assert meeting["native_id"] == "aaa-aaaa-aaa"          # native carried SEPARATELY for display
    assert live.by_uid["42"]["numeric_meeting_id"] == "42"


def test_arm_omits_numeric_meeting_id_when_key_is_not_numeric(monkeypatch):
    """A meeting that never resolved past its uid fallback has no row id to key the proc doc by —
    the hint is omitted (the worker falls back to the native key), never a bogus value."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    r.set("proc:meeting:sess-uid-fallback:on", "1")   # ROW-keyed flag; here the "row id" is the uid fallback

    payload = {**_payload("sess-uid-fallback"), "meeting_id": "sess-uid-fallback"}
    w._handle(r, disp, live, "u_live", payload, *_fresh_state())

    meeting = disp.dispatched[0]["context"]["meeting"]
    assert meeting["meeting_id"] == "sess-uid-fallback"    # keyed on the (non-numeric) uid fallback
    assert "numeric_meeting_id" not in meeting            # no row id → the durable-proc hint is omitted
    assert live.by_uid["sess-uid-fallback"]["numeric_meeting_id"] is None
