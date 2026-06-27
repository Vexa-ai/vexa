"""The in-process transcription watcher: keep DISTINCT meetings SEPARATE, and (the consumer-route
rewrite) CONSUME the meeting-api collector's canonical confirmed/pending output instead of re-deriving
the transcript from the raw stream.

Two collapse/loss bugs are guarded here:
  * Multi-meeting collapse — the numeric→native resolution must freeze ONE stable key per meeting (the
    gateway row lags the first segments); ``_handle`` + ``_resolve_native`` must never flip the key
    mid-stream (fork) or borrow another meeting's native.
  * Transcript LOSS — the watcher used to re-derive/dedup segments itself and dropped lines when the bot
    recycled a ``segment_id`` across utterances. It now RELAYS the collector's already-deduped feed
    (``:mutable`` confirmed/pending) and BACK-SEEDS from the collector store, so every distinct segment
    the collector emits is fanned faithfully (no drop) under its own unique id.
"""
from __future__ import annotations

import json

import agent_api.transcription_watcher as w


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: dict[str, list[dict]] = {}
        self.hashes: dict[str, dict] = {}
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

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key) or {})


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
    # Only meeting_id matters now (the watcher seeds/relays content from the collector, not this payload).
    return {"type": "transcription", "meeting_id": meeting_id, "segments": [
        {"text": "hi", "completed": True, "start": 0.0, "end": 1.0, "segment_id": "x"}]}


def _store_seg(r, numeric, segment_id, text, *, start=0.0, end=1.0, completed=True, **extra):
    """Write one canonical segment into the collector store the watcher back-seeds from."""
    seg = {"segment_id": segment_id, "text": text, "start": start, "end": end,
           "completed": completed, "speaker": "Speaker", "language": "en"}
    seg.update(extra)
    r.hset(f"meeting:{numeric}:segments", segment_id, json.dumps(seg))


def _fresh_state():
    return ({}, {}, {}, {}, set())  # last_arm, base, keymap, first_seen, seeded


def _reset_module_caches():
    w._native.clear()
    w._resolve_miss_at.clear()


# ── multi-meeting separation (resolution + seed) ──────────────────────────────────────────────────

def test_two_distinct_meetings_stay_separate(monkeypatch):
    """Two numeric ids → two natives → two separate feeds/copilots/live-rows. The collapse the bug
    produced must NOT happen — and each native feed is back-seeded only from its OWN store."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: {
        "42": ("aaa-aaaa-aaa", "google_meet"),
        "43": ("bbb-bbbb-bbb", "google_meet"),
    }.get(mid))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _store_seg(r, "42", "s-a", "hi from 42")
    _store_seg(r, "43", "s-b", "hi from 43")
    state = _fresh_state()
    for mid in ("42", "43", "42", "43"):
        w._handle(r, disp, live, "u_live", _payload(mid), *state)

    assert set(live.by_uid) == {"aaa-aaaa-aaa", "bbb-bbbb-bbb"}
    assert "tc:meeting:aaa-aaaa-aaa" in r.streams
    assert "tc:meeting:bbb-bbbb-bbb" in r.streams
    # back-seeded exactly once per meeting, each from its OWN store (never cross-contaminated)
    assert len(r.streams["tc:meeting:aaa-aaaa-aaa"]) == 1
    assert len(r.streams["tc:meeting:bbb-bbbb-bbb"]) == 1
    assert json.loads(r.streams["tc:meeting:aaa-aaaa-aaa"][0]["payload"])["segments"][0]["text"] == "hi from 42"


def test_late_native_resolution_does_not_fork_or_collapse(monkeypatch):
    """Meeting 43's gateway row lags: it resolves to None first, its native later. The key must NOT flip
    mid-stream (no fork), 43 must never borrow 42's native, and the feed is seeded only once it's keyed."""
    _reset_module_caches()
    state = {"43": None}

    def resolve(mid):
        if mid == "42":
            return ("aaa-aaaa-aaa", "google_meet")
        return state["43"]

    monkeypatch.setattr(w, "_resolve_native", resolve)
    monkeypatch.setattr(w, "RESOLVE_GRACE_SEC", 1e9)  # never fall back to numeric during the test

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _store_seg(r, "42", "s-a", "from 42")
    _store_seg(r, "43", "s-b", "from 43")
    st = _fresh_state()

    w._handle(r, disp, live, "u", _payload("42"), *st)
    w._handle(r, disp, live, "u", _payload("43"), *st)   # 43 unresolved, within grace → held
    assert set(live.by_uid) == {"aaa-aaaa-aaa"}
    assert "tc:meeting:43" not in r.streams               # never numeric-keyed under its native's nose
    assert "tc:meeting:bbb-bbbb-bbb" not in r.streams     # not seeded until 43 is keyed

    state["43"] = ("bbb-bbbb-bbb", "google_meet")
    w._handle(r, disp, live, "u", _payload("43"), *st)
    assert set(live.by_uid) == {"aaa-aaaa-aaa", "bbb-bbbb-bbb"}
    assert "tc:meeting:bbb-bbbb-bbb" in r.streams


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


def test_dispatch_carries_stream_tail_cursor(monkeypatch):
    """The copilot starts after the PRE-existing feed tail (before the back-seed), so the cursor reflects
    only the meeting's own history; the back-seed then appends to the feed."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _store_seg(r, "42", "s-a", "seeded line")
    r.streams["tc:meeting:aaa-aaaa-aaa"] = [{"payload": "old-1"}, {"payload": "old-2"}]
    r.set("proc:meeting:aaa-aaaa-aaa", "1")  # processing is opt-in — enable it so the copilot arms

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())

    meeting = disp.dispatched[0]["context"]["meeting"]
    assert meeting["transcript_start_id"] == "2-0"            # tail captured BEFORE the seed
    assert len(r.streams["tc:meeting:aaa-aaaa-aaa"]) == 3     # 2 pre-existing + 1 back-seeded


def test_copilot_processing_is_opt_in(monkeypatch):
    """Processing is OPT-IN: with no proc:meeting flag the copilot is NOT dispatched (no processing),
    yet the meeting still registers and the raw transcript still seeds. Flipping the flag arms it."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))
    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    _store_seg(r, "42", "s-a", "hi")

    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())
    assert disp.dispatched == []                    # OFF → no copilot, no processing
    assert "aaa-aaaa-aaa" in live.by_uid            # …but the meeting still registers
    assert "tc:meeting:aaa-aaaa-aaa" in r.streams   # …and the raw transcript still seeds

    r.set("proc:meeting:aaa-aaaa-aaa", "1")         # user enables processing → now it arms
    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())
    assert len(disp.dispatched) == 1


def test_back_seed_carries_absolute_timestamp(monkeypatch):
    """The canonical ABSOLUTE wall-clock survives the seed seam: the collector stores epoch-seconds
    `start`; the seeded ``tc:meeting:*`` segment must carry ``abs_start_ms`` == start*1000 (relative
    `start` is meeting-relative, 0.0 for the first seg)."""
    _reset_module_caches()
    monkeypatch.setattr(w, "_resolve_native", lambda mid: ("aaa-aaaa-aaa", "google_meet"))

    r, disp, live = _FakeRedis(), _FakeDispatcher(), _FakeLive()
    epoch = 1_700_000_123.4
    _store_seg(r, "42", "s1", "hello", start=epoch, end=epoch + 1.0,
               absolute_start_time="2023-11-14T22:15:23.400Z")
    w._handle(r, disp, live, "u_live", _payload("42"), *_fresh_state())

    seg = json.loads(r.streams["tc:meeting:aaa-aaaa-aaa"][0]["payload"])["segments"][0]
    assert seg["abs_start_ms"] == round(epoch * 1000) == 1_700_000_123_400
    assert seg["absolute_start_time"] == "2023-11-14T22:15:23.400Z"
    assert seg["start"] == 0.0
    assert seg["segment_id"] == "s1"


# ── the consumer route: relay the collector's :mutable canonical feed ──────────────────────────────

def test_relay_fans_confirmed_and_pending():
    """One :mutable delta → both the confirmed final and the pending draft are fanned, meeting-relative
    (earliest start becomes the anchor)."""
    r = _FakeRedis()
    keymap, base = {"64": "nat-xyz"}, {}
    data = {"type": "transcript", "meeting": {"id": 64},
            "confirmed": [{"segment_id": "c1", "text": "final one", "start": 10.0, "end": 12.0, "completed": True}],
            "pending": [{"segment_id": "p1", "text": "draft two", "start": 13.0, "end": 13.0, "completed": False}]}
    w._relay_message(r, keymap, base, data)

    segs = {json.loads(e["payload"])["segments"][0]["segment_id"]: json.loads(e["payload"])["segments"][0]
            for e in r.streams["tc:meeting:nat-xyz"]}
    assert segs["c1"]["completed"] is True and segs["c1"]["text"] == "final one"
    assert segs["p1"]["completed"] is False
    assert segs["c1"]["start"] == 0.0 and segs["p1"]["start"] == 3.0   # anchored at earliest (10.0)


def test_relay_skips_meeting_not_yet_keyed():
    """A :mutable for a meeting the arm thread hasn't frozen yet is skipped (the store-seed back-fills it)
    — never fanned to a numeric key that would diverge from the terminal's native key."""
    r = _FakeRedis()
    data = {"meeting": {"id": 99},
            "confirmed": [{"segment_id": "x", "text": "hi", "start": 0.0, "end": 1.0, "completed": True}]}
    w._relay_message(r, {}, {}, data)
    assert r.streams == {}


def test_recycled_seq_distinct_utterances_both_survive():
    """THE loss regression: the bot recycles the ``ch:seq`` slot across utterances; the collector finalizes
    each under a UNIQUE full id (distinct abs-ms). The relay must fan BOTH faithfully — the old re-derive
    path overwrote the first. No line is lost."""
    r = _FakeRedis()
    keymap, base = {"64": "nat"}, {}
    w._relay_message(r, keymap, base, {"meeting": {"id": 64}, "confirmed": [
        {"segment_id": "ch-0:4:1000", "text": "So I dont think anyone knows", "start": 100.0, "end": 104.0, "completed": True}]})
    w._relay_message(r, keymap, base, {"meeting": {"id": 64}, "confirmed": [
        {"segment_id": "ch-0:4:2000", "text": "TeraFab faster than other fabs", "start": 110.0, "end": 114.0, "completed": True}]})

    fanned = [json.loads(e["payload"])["segments"][0] for e in r.streams["tc:meeting:nat"]]
    assert {s["segment_id"] for s in fanned} == {"ch-0:4:1000", "ch-0:4:2000"}
    assert {s["text"] for s in fanned} == {"So I dont think anyone knows", "TeraFab faster than other fabs"}


def test_seed_fans_store_in_start_order():
    """The back-seed emits the stored transcript in ascending start order (the store is unordered)."""
    r = _FakeRedis()
    _store_seg(r, "7", "b", "second", start=20.0)
    _store_seg(r, "7", "a", "first", start=10.0)
    w._seed_from_store(r, "7", "natty", {})
    order = [json.loads(e["payload"])["segments"][0]["text"] for e in r.streams["tc:meeting:natty"]]
    assert order == ["first", "second"]
