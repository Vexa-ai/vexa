"""Regression: the in-process transcription watcher must keep DISTINCT meetings SEPARATE.

The bug: bots sent to two different Google Meets all collapsed into ONE meeting in the terminal —
every meeting's transcripts fanned onto a single ``tc:meeting:{key}`` stream / ``agent-meet-{key}``
copilot / live-registry row. Root cause lived in ``_handle``/``_resolve_native``: the numeric
meeting_id → native resolution could flip the routing key mid-stream (the gateway row lags the first
segments), forking or merging meetings. The fix freezes ONE stable key per numeric meeting_id and
returns the native ONLY for the exact id matched.
"""
from __future__ import annotations

import agent_api.transcription_watcher as w


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[dict]] = {}

    def xadd(self, key, fields):
        self.streams.setdefault(key, []).append(fields)


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


def _seg(text="hi", completed=True):
    return {"text": text, "completed": completed, "start": 0.0, "end": 1.0, "segment_id": text}


def _payload(meeting_id):
    return {"type": "transcription", "meeting_id": meeting_id, "segments": [_seg()]}


def _fresh_state():
    return ({}, {}, set(), {}, {}, {})  # last_arm, base, final_done, last_text, keymap, first_seen


def _reset_module_caches():
    w._native.clear()
    w._resolve_miss_at.clear()


def test_two_distinct_meetings_stay_separate(monkeypatch):
    """Two different numeric meeting_ids → two different native codes → two separate streams,
    copilots, and live rows. The exact collapse the bug produced must NOT happen."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: {
        "42": ("aaa-aaaa-aaa", "google_meet"),
        "43": ("bbb-bbbb-bbb", "google_meet"),
    }.get(mid))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    last_arm, base, final_done, last_text, keymap, first_seen = _fresh_state()
    for mid in ("42", "43", "42", "43"):
        w._handle(r, disp, live, "u_live", _payload(mid),
                  last_arm, base, final_done, last_text, keymap, first_seen)

    assert set(live.by_uid) == {"aaa-aaaa-aaa", "bbb-bbbb-bbb"}
    assert "tc:meeting:aaa-aaaa-aaa" in r.streams
    assert "tc:meeting:bbb-bbbb-bbb" in r.streams
    # the two meetings never share a stream
    assert len(r.streams) == 2


def test_late_native_resolution_does_not_fork_or_collapse(monkeypatch):
    """Meeting 43's gateway row lags: its first segment resolves to None, later ones resolve to its
    native. The key must NOT flip mid-stream (no fork), and 43 must never borrow 42's native."""
    _reset_module_caches()
    state = {"43": None}  # 43 unresolved at first

    def resolve(mid):
        if mid == "42":
            return ("aaa-aaaa-aaa", "google_meet")
        return state["43"]

    monkeypatch.setattr(w, "_resolve_native", resolve)
    monkeypatch.setattr(w, "RESOLVE_GRACE_SEC", 1e9)  # never fall back to numeric during the test

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    st = _fresh_state()

    # 42 flows normally; 43's first segment is held back (unresolved, within grace → dropped, not numeric-keyed)
    w._handle(r, disp, live, "u", _payload("42"), *st)
    w._handle(r, disp, live, "u", _payload("43"), *st)
    assert set(live.by_uid) == {"aaa-aaaa-aaa"}            # 43 not surfaced yet
    assert "tc:meeting:43" not in r.streams               # never numeric-keyed under its own native's nose

    # now 43 resolves; it gets its OWN native, distinct from 42
    state["43"] = ("bbb-bbbb-bbb", "google_meet")
    w._handle(r, disp, live, "u", _payload("43"), *st)
    assert set(live.by_uid) == {"aaa-aaaa-aaa", "bbb-bbbb-bbb"}
    assert "tc:meeting:bbb-bbbb-bbb" in r.streams


def test_resolve_native_returns_only_the_matched_id(monkeypatch):
    """_resolve_native must return the native for the EXACT meeting_id — never the first/any row in
    an (unfiltered, paginated) gateway list."""
    _reset_module_caches()
    import json

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
    assert w._resolve_native("99") is None  # unknown id → miss, NOT some other meeting's native
