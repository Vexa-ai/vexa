"""GET /link/events — vexa-link's one stream of the person's calls: state changes and new transcript
lines of running calls, one JSON object per line after `cursor <c>`. The first read starts at the end."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import create_app, link_events
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
HEAD = {"x-user-id": str(USER)}


def _seg(i, text, speaker="Anna"):
    return {"segment_id": f"s-{i}", "start": float(i), "end": i + 0.9, "text": text,
            "language": "en", "speaker": speaker, "completed": True}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(link_events, "LINK_POLL_S", 0.01)


def _read(client, cursor=""):
    r = client.get("/link/events", headers=HEAD, params={"cursor": cursor, "timeout_sec": 1})
    assert r.status_code == 200, r.text
    lines = r.text.splitlines()
    assert lines[0].startswith("cursor ")
    return lines[0].split(" ", 1)[1], [json.loads(x) for x in lines[1:]]


def test_first_read_reports_running_calls_and_starts_at_the_end_then_streams_new_lines():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="881", status="active",
                             segments=[_seg(0, "Earlier talk.")])
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="old-call-xyz",
                       status="completed", segments=[_seg(0, "Done.")])
    client = TestClient(create_app(store, redis=None))
    cursor, events = _read(client)
    assert [(e["type"], e.get("status")) for e in events] == [("system", "active")]
    assert events[0]["meeting"]["platform"] == "zoom" and events[0]["meeting"]["native_id"] == "881"
    assert store._meetings[mid]["data"]["metadata"]["listener_mode"] == "link"
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Claude, what's the price?", speaker="Bob")
    cursor, events = _read(client, cursor)
    assert events == [{"type": "transcript", "meeting": events[0]["meeting"], "index": 1,
                       "segment_id": "s-1", "start": 1.0, "absolute_start_time": None,
                       "speaker": "Bob", "text": "Claude, what's the price?"}]
    cursor, events = _read(client, cursor)
    assert events == [], "nothing new: the read waits, then answers empty"


def test_a_call_that_starts_and_ends_is_reported_with_its_lines_from_the_start():
    store = InMemoryTranscriptStore()
    client = TestClient(create_app(store, redis=None))
    cursor, events = _read(client)
    assert events == []
    mid = store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
                             status="active", segments=[_seg(0, "Hello.")])
    cursor, events = _read(client, cursor)
    assert [e["type"] for e in events] == ["system", "transcript"] and events[1]["text"] == "Hello."
    store._meetings[mid]["status"] = "completed"
    cursor, events = _read(client, cursor)
    assert [(e["type"], e["status"], e["previous"]) for e in events] == [("system", "completed", "active")]
    cursor, events = _read(client, cursor)
    assert events == [] and len(cursor) < 200, "an ended call is reported once and then forgotten"


def test_a_bad_cursor_starts_over_and_events_are_capped(monkeypatch):
    monkeypatch.setattr(link_events, "MAX_EVENTS", 3)
    store = InMemoryTranscriptStore()
    client = TestClient(create_app(store, redis=None))
    cursor, _ = _read(client, "not-a-cursor")
    mid = store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="9", status="active",
                             segments=[_seg(i, f"line {i}") for i in range(5)])
    cursor, events = _read(client, cursor)
    assert len(events) == 3
    cursor, events = _read(client, cursor)
    assert [e["index"] for e in events] == [2, 3, 4], "what did not fit comes in the next answer"


def _draft(i, text, speaker="Anna"):
    return {**_seg(i, text, speaker), "completed": False}


def _live_call(store):
    mid = store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="dwz-pacf-jmf",
                             status="active", segments=[_seg(0, "Так, окей, слушатель работает.")])
    client = TestClient(create_app(store, redis=None))
    cursor, _ = _read(client)
    return mid, client, cursor


def test_a_draft_is_not_sent_and_the_finished_words_arrive_once_with_their_segment_id():
    """Call 284, 3 Nov 12:46Z: the listener got the first draft of each line and never the words."""
    store = InMemoryTranscriptStore()
    mid, client, cursor = _live_call(store)
    store._meetings[mid]["segments"]["s-1"] = _draft(1, "Слышишь, можно")
    cursor, events = _read(client, cursor)
    assert events == [], "a draft is not what was said yet"
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Обновить ноут с этого звонка.")
    cursor, events = _read(client, cursor)
    assert [(e["segment_id"], e["index"], e["text"]) for e in events] == [
        ("s-1", 1, "Обновить ноут с этого звонка.")]
    cursor, events = _read(client, cursor)
    assert events == []


def test_a_line_finished_after_a_later_one_is_still_sent():
    store = InMemoryTranscriptStore()
    mid, client, cursor = _live_call(store)
    store._meetings[mid]["segments"]["s-1"] = _draft(1, "Обнови")
    store._meetings[mid]["segments"]["s-2"] = _seg(2, "Новый ноут звонка.")
    cursor, events = _read(client, cursor)
    assert [e["segment_id"] for e in events] == ["s-2"]
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Ну обнови.")
    cursor, events = _read(client, cursor)
    assert [(e["segment_id"], e["text"]) for e in events] == [("s-1", "Ну обнови.")]


def test_a_finished_line_reworded_later_goes_out_again_under_its_segment_id():
    store = InMemoryTranscriptStore()
    mid, client, cursor = _live_call(store)
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Обновить нутс.")
    cursor, events = _read(client, cursor)
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Обновить ноут с этого звонка.")
    cursor, events = _read(client, cursor)
    assert [(e["segment_id"], e["text"]) for e in events] == [("s-1", "Обновить ноут с этого звонка.")]


def test_the_cursor_stays_small_on_a_long_call():
    store = InMemoryTranscriptStore()
    mid, client, cursor = _live_call(store)
    for i in range(1, 600):
        store._meetings[mid]["segments"][f"s-{i}"] = _seg(i, f"line {i}")
    for _ in range(5):
        cursor, events = _read(client, cursor)
    assert len(cursor) < 1200, len(cursor)


# ── shared listening (policy P12, design mvp1-shared-listen) ─────────────────────────────────────
# Anyone a call is shared with listens from their own account, by the finished-transcript rule:
# owner · a person it was shared with (transcript_viewers) · a member of its bound workspace.
import threading
import time as _time

OWNER = 8


class _Log:
    def __init__(self):
        self.lines = []

    def __call__(self, event, **kw):
        self.lines.append((event, kw))
        return {}

    def named(self, event):
        return [kw for e, kw in self.lines if e == event]


def _app(store, log=None, shared_turn=False):
    kw = {"log_event": log} if log is not None else {}
    app = create_app(store, redis=None, **kw)
    if shared_turn:
        @app.middleware("http")
        async def _shared(request, call_next):   # what regime.refuse_shared marks for a shared turn
            request.state.shared_turn = True
            return await call_next(request)
    return TestClient(app)


def _read_as(client, headers, cursor="", timeout=1):
    r = client.get("/link/events", headers=headers, params={"cursor": cursor, "timeout_sec": timeout})
    assert r.status_code == 200, r.text
    lines = r.text.splitlines()
    return lines[0].split(" ", 1)[1], [json.loads(x) for x in lines[1:]]


def _shared_call(store, *, viewers=(USER,), workspace=None, native="shr-call-abc", segments=None):
    data = {"transcript_viewers": list(viewers), "title": "Their call"}
    if workspace:
        data["workspace_id"] = workspace
    return store.seed_meeting(user_id=OWNER, platform="google_meet", native_meeting_id=native,
                              status="active", data=data, segments=segments or [_seg(0, "Hi all.")])


def test_a_call_shared_with_me_streams_to_me_as_a_viewer_and_is_not_stamped():
    store, log = InMemoryTranscriptStore(), _Log()
    mid = _shared_call(store)
    client = _app(store, log)
    cursor, events = _read_as(client, HEAD)
    assert [(e["type"], e.get("status"), e["meeting"]["access"]) for e in events] == [
        ("system", "active", "viewer")]
    assert events[0]["meeting"] == {"id": mid, "platform": "google_meet", "native_id": "shr-call-abc",
                                    "title": "Their call", "access": "viewer"}
    assert "metadata" not in store._meetings[mid]["data"], "a shared call is never stamped as listened to"
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Claude, the price?", speaker="Bob")
    cursor, events = _read_as(client, HEAD, cursor)
    assert [(e["type"], e["text"], e["meeting"]["access"]) for e in events] == [
        ("transcript", "Claude, the price?", "viewer")]
    started = log.named("listen_shared")
    assert len(started) == 1 and started[0]["user_id"] == USER and started[0]["meeting_id"] == str(mid)
    assert started[0]["fields"] == {"access": "viewer", "owner_id": OWNER}
    _read_as(client, HEAD, cursor)
    assert len(log.named("listen_shared")) == 1, "audited once per (listener, call)"


def test_a_workspace_bound_call_streams_to_its_members_only():
    store = InMemoryTranscriptStore()
    _shared_call(store, viewers=(), workspace="ws_team")
    _, events = _read_as(_app(store), {**HEAD, "x-user-workspaces": "ws_other,ws_team"})
    assert [(e["status"], e["meeting"]["access"]) for e in events] == [("active", "workspace")]
    _, events = _read_as(_app(store), {**HEAD, "x-user-workspaces": "ws_other"})
    assert events == [], "not a member: nothing"


def test_a_viewer_of_one_call_hears_nothing_of_the_owners_other_call():
    store = InMemoryTranscriptStore()
    _shared_call(store, native="call-a")
    b = store.seed_meeting(user_id=OWNER, platform="zoom", native_meeting_id="call-b", status="active",
                           data={"title": "Private board call"}, segments=[_seg(0, "Secret.")])
    client = _app(store)
    cursor, events = _read_as(client, HEAD)
    store._meetings[b]["segments"]["s-1"] = _seg(1, "Claude, more secrets.")
    cursor, more = _read_as(client, HEAD, cursor)
    assert all(e["meeting"]["native_id"] == "call-a" for e in events + more)
    assert "Private board call" not in json.dumps(events + more) and "Secret" not in json.dumps(events + more)


def test_the_owners_own_call_is_still_marked_owner_and_stamped():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="mine", status="active",
                             data={"transcript_viewers": [OWNER]})
    _, events = _read_as(_app(store), HEAD)
    assert events[0]["meeting"]["access"] == "owner"
    assert store._meetings[mid]["data"]["metadata"]["listener_mode"] == "link"


def _held_read(client, headers, cursor, timeout):
    """A long-poll held open on a thread, so the test can change the call while it waits."""
    box = {}

    def run():
        t0 = _time.monotonic()
        box["result"] = _read_as(client, headers, cursor, timeout)
        box["elapsed"] = _time.monotonic() - t0
    th = threading.Thread(target=run)
    th.start()
    return th, box


def test_a_person_removed_mid_call_is_cut_within_five_seconds(monkeypatch):
    """Design §4: the next tick's access check refuses (N = 5 s: a 2 s tick and one read)."""
    monkeypatch.setattr(link_events, "LINK_POLL_S", 2.0)
    from meeting_api.collector import share_access
    store, log = InMemoryTranscriptStore(), _Log()
    mid = _shared_call(store)
    client = _app(store, log)
    cursor, _ = _read_as(client, HEAD)
    th, box = _held_read(client, HEAD, cursor, 20)
    _time.sleep(0.5)
    removed_at = _time.monotonic()
    assert share_access.remove_viewer(store._meetings[mid]["data"], USER)
    store._meetings[mid]["segments"]["s-9"] = _seg(9, "Said after the removal.")
    th.join(25)
    cursor, events = box["result"]
    assert _time.monotonic() - removed_at <= 5.0, box["elapsed"]
    assert events == [{"type": "system", "event": "access_ended", "meeting": {"id": mid}}]
    assert log.named("listen_shared_end")[0]["fields"]["reason"] == "revoked"
    monkeypatch.setattr(link_events, "LINK_POLL_S", 0.01)
    cursor, events = _read_as(client, HEAD, cursor)
    assert events == [], "nothing more of that call, ever"


def test_a_revoked_link_and_a_deleted_transcript_end_access_too():
    from meeting_api.collector import share_access
    store = InMemoryTranscriptStore()
    mid = _shared_call(store, viewers=())
    data = store._meetings[mid]["data"]
    data["share_grants"] = [{"id": "g1", "mode": "open"}]
    data["transcript_viewers"], data["share_viewers"] = [USER], [{"user_id": USER, "grant_id": "g1"}]
    client = _app(store)
    cursor, events = _read_as(client, HEAD)
    assert events and events[0]["meeting"]["access"] == "viewer"
    assert share_access.revoke_grant(data, "g1")
    cursor, events = _read_as(client, HEAD, cursor)
    assert [e["event"] for e in events] == ["access_ended"]
    # the owner deletes the transcript of their own call: their own stream lets it go too
    own = store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="mine2", status="active")
    cursor, events = _read_as(client, HEAD, cursor)
    store._meetings[own]["data"]["artifact_deletion"] = {"state": "completed"}
    cursor, events = _read_as(client, HEAD, cursor)
    assert [(e["event"], e["meeting"]["id"]) for e in events] == [("access_ended", own)]


def test_a_member_removed_from_the_workspace_keeps_the_held_read_then_sees_nothing():
    """Design §4: membership is the header the read opened with, so it holds until that read ends
    (N = 25 s, LINK_WAIT_MAX_S); the next read, without the workspace, sees the call end."""
    assert link_events.LINK_WAIT_MAX_S == 25
    store = InMemoryTranscriptStore()
    mid = _shared_call(store, viewers=(), workspace="ws_team")
    member = {**HEAD, "x-user-workspaces": "ws_team"}
    client = _app(store)
    cursor, events = _read_as(client, member)
    assert events[0]["meeting"]["access"] == "workspace"
    th, box = _held_read(client, member, cursor, 2)
    _time.sleep(0.2)
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Still a member for this read.")
    th.join(10)
    cursor, events = box["result"]
    assert [e.get("text") for e in events] == ["Still a member for this read."]
    removed = {**HEAD, "x-user-workspaces": ""}
    cursor, events = _read_as(client, removed, cursor)
    assert [e["event"] for e in events] == ["access_ended"]
    store._meetings[mid]["segments"]["s-2"] = _seg(2, "After the removal.")
    cursor, events = _read_as(client, removed, cursor)
    assert events == []


def test_an_ended_shared_call_is_reported_once_and_audited_as_ended():
    store, log = InMemoryTranscriptStore(), _Log()
    mid = _shared_call(store)
    client = _app(store, log)
    cursor, _ = _read_as(client, HEAD)
    store._meetings[mid]["status"] = "completed"
    cursor, events = _read_as(client, HEAD, cursor)
    assert [(e["event"], e["status"], e["meeting"]["access"]) for e in events] == [
        ("status", "completed", "viewer")]
    assert [x["fields"]["reason"] for x in log.named("listen_shared_end")] == ["ended"]
    cursor, events = _read_as(client, HEAD, cursor)
    assert events == []


def test_my_running_call_is_found_however_many_newer_calls_a_workspace_has():
    """Design §2(5): running calls are read by their own status filter, not from the 20 newest rows,
    so a busy workspace cannot push a listener's own running call out."""
    store = InMemoryTranscriptStore()
    mine = store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="mine-old", status="active",
                              created_at="2026-01-01T00:00:00Z")
    for i in range(40):
        store.seed_meeting(user_id=OWNER, platform="zoom", native_meeting_id=f"ws-{i}", status="completed",
                           data={"workspace_id": "ws_busy"}, created_at=f"2026-06-{10 + i % 18:02d}T00:00:00Z")
    _, events = _read_as(_app(store), {**HEAD, "x-user-workspaces": "ws_busy"})
    assert [e["meeting"]["id"] for e in events] == [mine]


def test_a_transcript_is_read_only_when_its_feed_moved():
    """Design §3: a tick reads the narrow access row; the transcript only when its mark moved."""
    store = InMemoryTranscriptStore()
    mid = _shared_call(store)
    reads = []
    real = store.get_transcript_by_id

    async def counted(*a, **kw):
        reads.append(a[1])
        return await real(*a, **kw)
    store.get_transcript_by_id = counted
    client = _app(store)
    cursor, _ = _read_as(client, HEAD)
    n = len(reads)
    for _ in range(3):
        cursor, events = _read_as(client, HEAD, cursor)
        assert events == []
    assert len(reads) == n, "nothing moved: no transcript read in three quiet reads"
    store._meetings[mid]["segments"]["s-1"] = _draft(1, "Clau")
    cursor, events = _read_as(client, HEAD, cursor)
    assert events == [] and len(reads) > n
    n = len(reads)
    store._meetings[mid]["segments"]["s-1"] = _seg(1, "Claude, settle this.")   # settles in place
    cursor, events = _read_as(client, HEAD, cursor)
    assert [e["text"] for e in events] == ["Claude, settle this."] and len(reads) > n


def test_a_shared_turn_hears_only_calls_bound_to_its_ceilings_workspaces():
    """Design §2(2): a turn other people read is not refused the stream; its calls are FILTERED to
    those `regime.shared_meeting_readable` admits (bound to a workspace inside the ceiling)."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="private", status="active")
    _shared_call(store, native="shared-to-me")
    store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="team-call", status="active",
                       data={"workspace_id": "ws_1"})
    turn = {**HEAD, "x-user-workspaces": "ws_1", "x-user-delegation-workspaces": "ws_1"}
    _, events = _read_as(_app(store, shared_turn=True), turn)
    assert [e["meeting"]["native_id"] for e in events] == ["team-call"]
    _, events = _read_as(_app(store, shared_turn=True), {**turn, "x-user-delegation-workspaces": "*"})
    assert events == [], "an unbounded ceiling admits nothing (the shared_meeting_readable rule)"


# ── code review (non-author) H1 · M1 · M2 ───────────────────────────────────────────────────────────
import asyncio as _asyncio

import httpx as _httpx


def _forged(ids):
    return link_events.encode_cursor({"s": {str(i): "active" for i in ids},
                                      "m": {str(i): {"n": 0, "h": {}, "a": "v"} for i in ids}})


def test_a_forged_cursor_naming_other_peoples_calls_learns_their_ids_back_and_nothing_else():
    """H1: user 7 forges a cursor holding user 99's calls. Each comes back as access_ended with its id
    alone — no platform, native id, title, owner or status — exactly as an id that never existed."""
    store = InMemoryTranscriptStore()
    theirs = [store.seed_meeting(user_id=99, platform="google_meet", native_meeting_id=f"zzz-secr-et{i}",
                                 status=s, data={"title": f"Board {i}", "workspace_id": "ws_99"},
                                 segments=[_seg(0, "Confidential.")])
              for i, s in enumerate(("active", "completed", "awaiting_admission"))]
    client = _app(store)
    _, events = _read_as(client, HEAD, _forged(theirs + [424242]))
    assert events == [{"type": "system", "event": "access_ended", "meeting": {"id": i}}
                       for i in sorted(theirs + [424242])]
    assert not any(x in json.dumps(events) for x in ("zzz-secr", "Board", "google_meet", "99", "Confidential"))


def test_enumerating_350_ids_with_forged_cursors_reveals_nothing():
    store = InMemoryTranscriptStore()
    for i in range(350):
        store.seed_meeting(user_id=1000 + i % 7, platform=("zoom", "google_meet", "teams")[i % 3],
                           native_meeting_id=f"nat-{i:04d}-x", status=("active", "completed")[i % 2],
                           data={"title": f"T{i}", "transcript_viewers": [5]})
    client = _app(store)
    seen_ids, leaked = [], []
    for lo in range(1, 351, link_events.MAX_HELD):
        _, events = _read_as(client, HEAD, _forged(range(lo, min(lo + link_events.MAX_HELD, 351))))
        for e in events:
            assert e["event"] == "access_ended" and set(e) == {"type", "event", "meeting"}, e
            assert set(e["meeting"]) == {"id"}, e
            seen_ids.append(e["meeting"]["id"])
        leaked += [x for x in ("nat-", "T1", "zoom", "google_meet", "teams") if x in json.dumps(events)]
    assert sorted(seen_ids) == list(range(1, 351)) and leaked == []


def test_a_forged_cursor_is_cut_to_max_held_ids():
    store = InMemoryTranscriptStore()
    cursor = link_events.encode_cursor({"s": {str(i): "active" for i in range(1, 120)}})
    _, events = _read_as(_app(store), HEAD, cursor)
    assert len(events) == link_events.MAX_HELD


def test_eight_calls_with_long_transcripts_keep_the_cursor_under_the_cap_and_lose_no_line():
    """M1: a cursor past its budget remembers fewer recent lines per call instead of answering 422."""
    store = InMemoryTranscriptStore()
    mids = [store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id=f"c{k}", status="active",
                               segments=[_seg(i, f"call {k} line {i}") for i in range(60)])
            for k in range(8)]
    client = _app(store)
    cursor, _ = _read_as(client, HEAD)
    assert len(cursor) <= link_events.CURSOR_BUDGET
    got = []
    for step in range(60, 66):
        for k, mid in enumerate(mids):
            store._meetings[mid]["segments"][f"s-{step}"] = _seg(step, f"call {k} line {step}")
        cursor, events = _read_as(client, HEAD, cursor)
        assert len(cursor) <= link_events.CURSOR_BUDGET < 4096, len(cursor)
        got += [e["text"] for e in events if e["type"] == "transcript"]
    assert sorted(got) == sorted(f"call {k} line {i}" for k in range(8) for i in range(60, 66)), \
        "every new line once, none again"
    # a late re-wording inside the narrowed window still goes out
    store._meetings[mids[0]]["segments"]["s-65"] = _seg(65, "call 0 line 65, reworded")
    cursor, events = _read_as(client, HEAD, cursor)
    assert [e["text"] for e in events] == ["call 0 line 65, reworded"]


def test_fit_cursor_shrinks_windows_newest_kept():
    state = {"s": {}, "m": {str(i): {"n": 50, "a": "o", "h": {f"k{j:02d}": "x" * 8 for j in range(24)}}
                            for i in range(8)}}
    out = link_events.fit_cursor(state, budget=1500)
    back = link_events.decode_cursor(out)
    assert len(out) <= 1500
    for m in back["m"].values():
        assert list(m["h"]) == [f"k{j:02d}" for j in range(24 - m["w"], 24)]


class _Counter:
    """The open-read count's Redis: incr/expire/decr, optionally failing."""
    def __init__(self, broken=False):
        self.n, self.broken = {}, broken

    async def incr(self, k):
        if self.broken:
            raise ConnectionError("redis down")
        self.n[k] = self.n.get(k, 0) + 1
        return self.n[k]

    async def expire(self, k, s):
        return True

    async def decr(self, k):
        self.n[k] -= 1
        return self.n[k]


def _async_app(store, counter, log=None):
    from fastapi import APIRouter, FastAPI
    router = APIRouter()
    link_events.install(router, store=store, resolve_user=lambda h: int(h), log_event=log or _Log(),
                        counter=counter)
    app = FastAPI()
    app.include_router(router)
    return _httpx.AsyncClient(transport=_httpx.ASGITransport(app=app), base_url="http://m")


def test_a_person_holds_at_most_two_open_reads_counted_in_redis(monkeypatch):
    """M2: the third concurrent read of one person is 429 (the count is in Redis, so it holds across
    replicas); another person is not affected; a read that ends gives its place back."""
    monkeypatch.setattr(link_events, "LINK_POLL_S", 0.05)
    store, counter = InMemoryTranscriptStore(), _Counter()

    async def go():
        async with _async_app(store, counter) as c:
            get = lambda uid, t: c.get("/link/events", params={"timeout_sec": t}, headers={"x-user-id": str(uid)})  # noqa: E731
            held = [_asyncio.create_task(get(USER, 2)) for _ in range(2)]
            await _asyncio.sleep(0.3)
            third, other = await get(USER, 1), await get(OWNER, 1)
            done = await _asyncio.gather(*held)
            after = await get(USER, 1)
            return third, other, done, after
    third, other, done, after = _asyncio.run(go())
    assert third.status_code == 429 and third.headers.get("retry-after")
    assert other.status_code == 200 and [r.status_code for r in done] == [200, 200]
    assert after.status_code == 200 and counter.n == {f"link:open:{USER}": 0, f"link:open:{OWNER}": 0}


def test_a_redis_that_cannot_count_refuses_the_read():
    async def go():
        async with _async_app(InMemoryTranscriptStore(), _Counter(broken=True)) as c:
            return await c.get("/link/events", params={"timeout_sec": 1}, headers={"x-user-id": "7"})
    assert _asyncio.run(go()).status_code == 429


def test_tick_database_work_is_gated_per_process(monkeypatch):
    """M2: at most LINK_DB_CONCURRENCY reads' store calls run at once (here 2, for 8 listeners)."""
    monkeypatch.setattr(link_events, "LINK_DB_CONCURRENCY", 2)
    monkeypatch.setattr(link_events, "LINK_POLL_S", 0.05)
    store = InMemoryTranscriptStore()
    for u in range(8):
        store.seed_meeting(user_id=100 + u, platform="zoom", native_meeting_id=f"g{u}", status="active",
                           segments=[_seg(0, "x")])
    state = {"now": 0, "peak": 0}
    real = store.listen_calls

    async def slow(*a, **kw):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await _asyncio.sleep(0.05)
        try:
            return await real(*a, **kw)
        finally:
            state["now"] -= 1
    store.listen_calls = slow

    async def go():
        async with _async_app(store, _Counter()) as c:
            return await _asyncio.gather(*[c.get("/link/events", params={"timeout_sec": 1},
                                                 headers={"x-user-id": str(100 + u)}) for u in range(8)])
    rs = _asyncio.run(go())
    assert all(r.status_code == 200 for r in rs) and state["peak"] == 2
