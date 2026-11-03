"""The in-process transcription watcher: act ONLY on what the collector verified, keep DISTINCT meetings
SEPARATE, register the live row, and — crucially — NEVER write the transcript carrier, and NEVER dispatch.

Every bot appends to one ``transcription_segments`` stream, and only meeting-api's collector can check
that an entry was signed by its own meeting's bot. So a raw entry is a HINT: it names which verified feed
to read. Everything the watcher does — register a live meeting, end it, connect its kg doc — comes from
``tc:meeting:{row}``, the feed the collector writes only for entries it admitted (P23, single writer).
The deny tests below hold it to that: a raw entry nobody signed for, or one that names another meeting,
leads to a feed with nothing new in it, and nothing happens.

Half of this file used to be about the OTHER thing it did: arm and keep alive a per-meeting copilot
while a ``proc:meeting:{row}:on`` flag was set. PRD decision 34 removed that pipeline; the watcher now
dispatches nothing at all, and the tests below hold it to that.
"""
from __future__ import annotations

import json

import fakeredis
import pytest

import control_plane.transcription_watcher as w

SRC = w.SRC


class _FakeDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[dict] = []

    def dispatch(self, inv):
        self.dispatched.append(inv)
        return "unit-id"


class _FakeLive:
    def __init__(self) -> None:
        self.by_uid: dict[str, dict] = {}
        self.dropped: list[str] = []

    def add(self, meeting):
        self.by_uid[meeting["session_uid"]] = dict(meeting)

    def drop(self, uid):
        self.dropped.append(uid)
        self.by_uid.pop(uid, None)


class _Rig:
    """One redis, one follow state, one live registry: raw hints in, verified feed read, actions out."""

    def __init__(self) -> None:
        self.r = fakeredis.FakeRedis(decode_responses=True)
        self.follow = w._Follow()
        self.live = _FakeLive()
        self.keymap: dict[str, str] = {}
        self.docs: list[tuple] = []
        self.now = 1000.0

    # what a bot (or anyone holding a redis connection) appends to the raw stream
    def raw(self, payload: dict, **extra) -> str:
        fields = {"payload": json.dumps(payload), **extra}
        entry_id = self.r.xadd(SRC, fields)
        self.follow.hint(fields, entry_id, self.now)
        return entry_id

    # what meeting-api's collector writes for an entry it ADMITTED
    def verified_segment(self, mid, native=None, text="hi", entry_id="*") -> str:
        uid = native or str(mid)
        wire = {"type": "transcription", "session_uid": uid, "meeting_id": uid, "segments": [
            {"speaker": "A", "text": text, "start": 0.0, "end": 1.0, "completed": True, "segment_id": "x"}]}
        return self.r.xadd(f"tc:meeting:{mid}", {"payload": json.dumps(wire)}, id=entry_id)

    def verified_end(self, mid, native=None, entry_id="*") -> str:
        return self.r.xadd(f"tc:meeting:{mid}",
                           {"payload": json.dumps({"type": "session_end", "session_uid": native or str(mid)})},
                           id=entry_id)

    def drain(self) -> None:
        w._drain(self.r, self.follow, self.live, "u_live", self.keymap)

    def carrier_lengths(self) -> dict[str, int]:
        return {k: self.r.xlen(k) for k in self.r.scan_iter(match="tc:meeting:*")}


def _segment(mid, native=None):
    p = {"type": "transcription", "meeting_id": mid,
         "segments": [{"text": "hi", "completed": True, "start": 0.0, "end": 1.0, "segment_id": "x"}]}
    if native:
        p["native_meeting_id"] = native
    return p


def _reset_module_caches():
    w._native.clear()
    w._resolve_miss_at.clear()


@pytest.fixture
def rig(monkeypatch):
    _reset_module_caches()
    rig = _Rig()
    monkeypatch.setattr(w, "_record_meeting_doc", lambda native, platform, subject:
                        rig.docs.append((native, platform, subject)))
    monkeypatch.setattr(w, "_resolve_native", lambda mid: None)
    return rig


# ── what it believes: the verified feed, never the raw stream ───────────────────────────────────────

def test_a_meeting_registers_from_its_verified_feed(rig):
    rig.raw(_segment("42", "aaa-aaaa-aaa"))
    rig.verified_segment(42, "aaa-aaaa-aaa")
    rig.drain()
    assert set(rig.live.by_uid) == {"42"}
    assert rig.live.by_uid["42"]["native_id"] == "aaa-aaaa-aaa"   # the collector's native, for display
    assert rig.keymap == {"42": "42"}


def test_an_unsigned_entry_registers_nothing(rig):
    """The collector drops an entry its meeting's bot did not sign, so the verified feed has nothing
    new; the raw entry alone registers nothing."""
    rig.raw(_segment("42", "aaa-aaaa-aaa"))           # unsigned: no auth, no sig
    rig.drain()
    assert rig.live.by_uid == {}
    assert rig.keymap == {}


def test_an_entry_naming_another_meeting_ends_nothing(rig):
    """Meeting 42 is live. A raw session_end for 42 arrives, signed by meeting 7's session (or by
    nobody): the collector admits it for no meeting, writes no marker, and 42 stays live with no kg-doc
    connect."""
    rig.raw(_segment("42", "aaa-aaaa-aaa"))
    rig.verified_segment(42, "aaa-aaaa-aaa")
    rig.drain()
    rig.raw({"type": "session_end", "meeting_id": "42", "native_meeting_id": "aaa-aaaa-aaa"},
            auth="header.claims-of-meeting-7", sig="00")
    for _ in range(3):
        rig.drain()
    assert "42" in rig.live.by_uid
    assert rig.live.dropped == []
    assert rig.docs == []


def test_an_entry_naming_another_meeting_registers_nothing(rig):
    """A raw segment that names meeting 43 registers nothing unless the collector admitted it for 43."""
    rig.raw(_segment("43", "bbb-bbbb-bbb"), auth="header.claims-of-meeting-7", sig="00")
    rig.drain()
    assert rig.live.by_uid == {}


def test_a_verified_end_ends_the_meeting_and_connects_its_doc(rig):
    rig.raw(_segment("9", "nat-9"))
    rig.verified_segment(9, "nat-9")
    rig.drain()
    rig.raw({"type": "session_end", "meeting_id": "9"})
    rig.drain()                                       # the collector has not written the marker yet
    assert "9" in rig.live.by_uid
    rig.verified_end(9, "nat-9")
    rig.drain()                                       # …now it has
    assert "9" not in rig.live.by_uid
    assert rig.live.dropped == ["9"]
    assert "9" not in rig.keymap
    assert rig.docs == [("nat-9", "google_meet", "u_live")]
    assert "9" not in rig.follow.until                # nothing left to follow for it


def test_old_verified_entries_are_not_replayed_by_a_new_hint(rig):
    """A meeting that ended long ago still has its marker in the feed. A raw entry naming it now reads
    from just before its own time, so the old marker is not acted on again."""
    rig.verified_segment(5, "old-5", entry_id="1000-0")
    rig.verified_end(5, "old-5", entry_id="1001-0")
    rig.raw(_segment("5", "old-5"))
    rig.drain()
    assert rig.live.by_uid == {} and rig.live.dropped == [] and rig.docs == []


def test_an_entry_acted_on_is_never_acted_on_twice(rig):
    rig.raw(_segment("9", "nat-9"))
    rig.verified_segment(9, "nat-9")
    rig.verified_end(9, "nat-9")
    rig.drain()
    rig.raw(_segment("9", "nat-9"))                   # hinted again, inside the skew window
    rig.drain()
    assert rig.docs == [("nat-9", "google_meet", "u_live")]


def test_a_non_numeric_meeting_id_is_never_followed(rig):
    """The collector keys its feed by the numeric row id, so nothing else can be verified."""
    rig.raw({**_segment("sess-uid-fallback"), "meeting_id": "sess-uid-fallback"})
    rig.raw({**_segment("42"), "meeting_id": "42:mutable"})
    assert rig.follow.until == {} and rig.follow.cursors == {}


def test_raw_entries_cannot_grow_the_follow_state_without_bound(rig, monkeypatch):
    monkeypatch.setattr(w, "MAX_FOLLOWED", 8)
    monkeypatch.setattr(w, "MAX_CURSORS", 16)
    for mid in range(100, 200):
        rig.raw(_segment(str(mid)))
    assert len(rig.follow.until) == 8
    assert len(rig.follow.cursors) <= 16
    rig.drain()
    assert rig.live.by_uid == {}


def test_a_follow_ends_when_its_meeting_goes_quiet(rig):
    rig.raw(_segment("42"))
    assert "42" in rig.follow.until
    rig.follow.expire(rig.now + w.FOLLOW_SEC + 1)
    assert rig.follow.until == {}
    assert "42" in rig.follow.cursors                 # the read position is kept


# ── multi-meeting separation (keying) ─────────────────────────────────────────────────────────────

def test_two_distinct_meetings_stay_separate(rig):
    """Two ROW ids → two separate live rows, keyed on the ROW id (P0 — the native id is NOT unique;
    keying by it collapsed/leaked). The agent writes NO transcript stream (the collector owns it)."""
    for mid, native in (("42", "aaa-aaaa-aaa"), ("43", "bbb-bbbb-bbb")):
        rig.raw(_segment(mid, native))
        rig.verified_segment(int(mid), native)
    before = rig.carrier_lengths()
    rig.drain()
    assert set(rig.live.by_uid) == {"42", "43"}
    assert rig.keymap == {"42": "42", "43": "43"}
    assert rig.live.by_uid["42"]["native_id"] == "aaa-aaaa-aaa"
    assert rig.live.by_uid["43"]["native_id"] == "bbb-bbbb-bbb"
    assert rig.carrier_lengths() == before                       # the agent appended nothing


def test_late_native_resolution_does_not_fork_or_collapse(rig, monkeypatch):
    """The collector knew no native for meeting 43, so its feed carries the row id; the gateway lookup
    fills the display in once it can. The routing key is the row id throughout."""
    state = {"43": None}
    monkeypatch.setattr(w, "_resolve_native", lambda mid: state.get(mid))
    rig.raw(_segment("43"))
    rig.verified_segment(43)
    rig.drain()
    assert rig.keymap["43"] == "43"
    assert rig.live.by_uid["43"]["native_id"] == "43"            # pending → display falls back
    state["43"] = ("bbb-bbbb-bbb", "google_meet")
    rig.raw(_segment("43"))
    rig.verified_segment(43)
    rig.drain()
    assert rig.keymap["43"] == "43"                               # routing key UNCHANGED (no fork)
    assert rig.live.by_uid["43"]["native_id"] == "bbb-bbbb-bbb"


def test_unresolved_native_still_keys_on_row_id_immediately(rig):
    rig.raw(_segment("77"))
    rig.verified_segment(77)
    rig.drain()
    assert "77" in rig.live.by_uid and rig.keymap["77"] == "77"
    assert rig.live.by_uid["77"]["native_id"] == "77"


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


# ── the watcher dispatches NOTHING (PRD decision 34) ────────────────────────────────────────────────

def test_the_watcher_never_dispatches_and_never_reads_legacy_keys(rig):
    """It used to arm a per-meeting copilot whenever ``proc:meeting:{row}:on`` was set. Whatever a
    legacy deployment still has in redis — the flag, the cursor, a processed-notes STREAM (a GET of
    which raises WRONGTYPE in real redis) — the watcher registers the live meeting, dispatches
    nothing, and leaves those keys alone."""
    disp = _FakeDispatcher()
    rig.r.set("proc:meeting:42:on", "1")
    rig.r.set("proc:meeting:42:cursor", "1-0")
    rig.r.xadd("proc:meeting:42", {"payload": "{}"})
    rig.r.xadd("tc:meeting:42", {"payload": "c-1"})              # a malformed feed entry is skipped
    rig.raw(_segment("42", "aaa-aaaa-aaa"))
    rig.verified_segment(42, "aaa-aaaa-aaa")
    rig.drain()
    assert disp.dispatched == []
    assert "42" in rig.live.by_uid and "unit_id" not in rig.live.by_uid["42"]
    rig.verified_end(42, "aaa-aaaa-aaa")
    rig.drain()
    assert rig.r.get("proc:meeting:42:on") == "1" and rig.r.get("proc:meeting:42:cursor") == "1-0"
    assert rig.r.xlen("proc:meeting:42") == 1


def test_live_entry_carries_the_row_id(rig):
    """The live registry entry carries the meetings-domain ROW id — the terminal's SSE and the by-id
    REST reads both key on it."""
    rig.raw(_segment("42", "aaa-aaaa-aaa"))
    rig.verified_segment(42, "aaa-aaaa-aaa")
    rig.drain()
    assert rig.live.by_uid["42"]["numeric_meeting_id"] == "42"
    assert rig.live.by_uid["42"]["meeting_id"] == "42"
    assert rig.live.by_uid["42"]["native_id"] == "aaa-aaaa-aaa"


def test_the_loop_reads_hints_and_acts_on_the_verified_feed(monkeypatch):
    """One turn of the real loop: the consumer group reads the raw entry, acks it, and the drain acts on
    the collector's feed — and on nothing else."""
    _reset_module_caches()
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server, decode_responses=True)
    import redis as redislib

    monkeypatch.setattr(redislib, "from_url", lambda *a, **k: fakeredis.FakeRedis(server=server,
                                                                               decode_responses=True))
    monkeypatch.setattr(w, "_resolve_native", lambda mid: None)
    live = _FakeLive()
    calls = {"n": 0}
    real_drain = w._drain

    def drain_then_stop(*a, **k):
        real_drain(*a, **k)
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SystemExit
    monkeypatch.setattr(w, "_drain", drain_then_stop)

    r.xgroup_create(SRC, w.GROUP, id="$", mkstream=True)
    r.xadd(SRC, {"payload": json.dumps(_segment("42", "aaa-aaaa-aaa"))})          # verified below
    r.xadd(SRC, {"payload": json.dumps(_segment("43", "bbb-bbbb-bbb"))})          # never admitted
    r.xadd("tc:meeting:42", {"payload": json.dumps({"type": "transcription", "session_uid": "aaa-aaaa-aaa",
                                                    "meeting_id": "aaa-aaaa-aaa", "segments": []})})
    with pytest.raises(SystemExit):
        w._run_arm("redis://unused", None, live, "u_live", {})
    assert set(live.by_uid) == {"42"}
    assert r.xpending(SRC, w.GROUP)["pending"] == 0                                # hints acked


