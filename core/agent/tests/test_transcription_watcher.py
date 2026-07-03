"""The in-process transcription watcher (ARM-only): keep DISTINCT meetings SEPARATE, arm the copilot
opt-in, and — crucially — NEVER write the transcript carrier.

Post-D7 (P23): meeting-api's collector is the SINGLE writer of ``tc:meeting:{native}`` (segments AND the
session_end marker). The agent watcher only tails ``transcription_segments`` as a TRIGGER to do agent-domain
jobs: freeze ONE native routing key per meeting, register the live row, re-arm the copilot while processing
is enabled, and reap on session_end. It READS the native feed's tail for the copilot resume cursor, but
writes nothing — `meetings ⊥ agent` (P3). These tests assert that behaviour via keymap/live/dispatch, and
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
    """Two numeric ids → two natives → two separate live rows / copilot keys. The collapse the bug
    produced must NOT happen — and the agent writes NO transcript stream (the collector owns it)."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: {
        "42": ("aaa-aaaa-aaa", "google_meet"),
        "43": ("bbb-bbbb-bbb", "google_meet"),
    }.get(mid))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = state = _fresh_state()
    for mid in ("42", "43", "42", "43"):
        w._handle(r, disp, live, "u_live", _payload(mid), *state)

    assert set(live.by_uid) == {"aaa-aaaa-aaa", "bbb-bbbb-bbb"}
    assert keymap == {"42": "aaa-aaaa-aaa", "43": "bbb-bbbb-bbb"}  # one frozen key per meeting
    assert _native_streams(r) == []                               # agent wrote NO transcript carrier


def test_late_native_resolution_does_not_fork_or_collapse(monkeypatch):
    """Meeting 43's gateway row lags: None first, native later. The key must NOT flip mid-stream (no fork),
    43 must never borrow 42's native, and it is keyed only once it resolves."""
    _reset_module_caches()
    state = {"43": None}

    def resolve(mid):
        if mid == "42":
            return ("aaa-aaaa-aaa", "google_meet")
        return state["43"]

    monkeypatch.setattr(w, "_resolve_native", resolve)
    monkeypatch.setattr(w, "RESOLVE_GRACE_SEC", 1e9)  # never fall back to numeric during the test

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = st = _fresh_state()

    w._handle(r, disp, live, "u", _payload("42"), *st)
    w._handle(r, disp, live, "u", _payload("43"), *st)   # 43 unresolved, within grace → held
    assert set(live.by_uid) == {"aaa-aaaa-aaa"}
    assert "43" not in keymap                             # never numeric-keyed under its native's nose

    state["43"] = ("bbb-bbbb-bbb", "google_meet")
    w._handle(r, disp, live, "u", _payload("43"), *st)
    assert set(live.by_uid) == {"aaa-aaaa-aaa", "bbb-bbbb-bbb"}
    assert keymap["43"] == "bbb-bbbb-bbb"


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


# ── copilot arming (opt-in) reads the collector-owned feed tail for the resume cursor ───────────────

def test_arm_reads_native_feed_tail_for_resume_cursor(monkeypatch):
    """The copilot resumes after the native feed's CURRENT tail. The arm thread READS that tail (the
    collector writes the feed) and passes it as the dispatch's transcript_start_id — it writes nothing."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    # The collector has already written 2 entries onto the native feed (simulated here).
    r.streams["tc:meeting:aaa-aaaa-aaa"] = [{"payload": "c-1"}, {"payload": "c-2"}]
    r.set("proc:meeting:aaa-aaaa-aaa:on", "1")  # processing is opt-in — enable it so the copilot arms

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())

    meeting = disp.dispatched[0]["context"]["meeting"]
    assert meeting["transcript_start_id"] == "2-0"                # tail of the collector-written feed
    assert len(r.streams["tc:meeting:aaa-aaaa-aaa"]) == 2         # unchanged — the agent appended nothing


def test_copilot_processing_is_opt_in(monkeypatch):
    """Processing is OPT-IN: with no proc:meeting flag the copilot is NOT dispatched, yet the meeting still
    registers. Flipping the flag arms it. The agent writes no transcript stream either way."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())
    assert disp.dispatched == []                    # OFF → no copilot, no processing
    assert "aaa-aaaa-aaa" in live.by_uid            # …but the meeting still registers
    assert _native_streams(r) == []                 # …and the agent writes no transcript carrier

    r.set("proc:meeting:aaa-aaaa-aaa:on", "1")         # user enables processing → now it arms
    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())
    assert len(disp.dispatched) == 1


def test_unresolved_meeting_surfaces_under_numeric_after_grace(monkeypatch):
    """A meeting whose native NEVER resolves is HELD during RESOLVE_GRACE_SEC (not yet keyed), then
    surfaces under its NUMERIC key — never swallowed."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: None)   # never resolves
    clock = [1000.0]
    monkeypatch.setattr(w.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(w, "RESOLVE_GRACE_SEC", 6.0)

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _, keymap, _ = st = _fresh_state()

    w._handle(r, disp, live, "u", _payload("77"), *st)          # within grace → HELD
    assert set(live.by_uid) == set() and "77" not in keymap

    clock[0] += 7.0                                              # grace elapses
    w._handle(r, disp, live, "u", _payload("77"), *st)          # now surfaces under numeric
    assert "77" in live.by_uid and keymap["77"] == "77"         # not swallowed


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
    r.xadd("proc:meeting:nat-77", {"payload": "{}"})            # the processed-notes STREAM exists (collision bait)
    r.set("proc:meeting:nat-77:on", "1")                        # processing ENABLED via the flag

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

    w._handle(r, disp, live, "u", _payload("9"), *st)        # establish the meeting
    assert "nat-9" in live.by_uid and keymap.get("9") == "nat-9"

    w._handle(r, disp, live, "u", {"type": "session_end", "meeting_id": "9"}, *st)
    assert "nat-9" not in live.by_uid                        # live row dropped
    assert "9" not in keymap                                 # keymap cleared (clean relaunch)
    assert _native_streams(r) == []                          # the agent wrote NO session_end marker


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
