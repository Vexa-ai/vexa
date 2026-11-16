"""`wait_for_signal` — a connector-only listener's one blocking read: lines that address Claude, or
quiet, or the meeting ended. Deterministic (a word match, no model); the wait re-reads the room's
transcript every `SIGNAL_POLL_S` until something answers or `timeout_sec` is over."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import app as collector_app
from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
HEAD = {"x-user-id": str(USER)}
URL = "/transcripts/google_meet/abc-defg-hij/signal"


def _seg(i, text, speaker="Anna"):
    return {"segment_id": f"s-{i}", "start": float(i), "end": i + 0.9, "text": text,
            "language": "en", "speaker": speaker, "completed": True}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(collector_app, "SIGNAL_POLL_S", 0.01)


def _store(segments, status="active"):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
                             status=status, segments=segments)
    return store, mid


def test_a_line_that_names_claude_is_returned_alone():
    store, _ = _store([_seg(0, "Morning all."), _seg(1, "Claude, what did we decide on pricing?"),
                       _seg(2, "Клод, запиши это."), _seg(3, "The claudette is a pastry.")])
    r = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD, params={"timeout_sec": 1})
    body = r.json()
    assert r.status_code == 200 and body["signal"] == "addressed"
    assert [line["index"] for line in body["lines"]] == [1, 2]
    assert body["lines"][0]["speaker"] == "Anna" and body["new_lines"] == 4 and body["next_index"] == 4


def test_since_index_skips_what_was_already_seen():
    store, _ = _store([_seg(0, "Claude, hi."), _seg(1, "Next item.")])
    body = TestClient(create_app(store, redis=None)).get(
        URL, headers=HEAD, params={"since_index": 1, "timeout_sec": 1}).json()
    assert body["signal"] == "quiet" and body["lines"] == [] and body["new_lines"] == 1
    assert body["next_index"] == 2


def test_quiet_on_timeout_with_a_count_of_new_lines():
    store, _ = _store([_seg(0, "Just talking."), _seg(1, "About claudication, medically.")])
    body = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD,
                                                         params={"timeout_sec": 1}).json()
    assert body["signal"] == "quiet" and body["lines"] == [] and body["new_lines"] == 2


def test_it_waits_and_answers_the_moment_claude_is_addressed():
    store, mid = _store([_seg(0, "Hello.")])
    reads = {"n": 0}
    read = store.get_transcript

    async def _later(user_id, platform, native):
        reads["n"] += 1
        if reads["n"] == 3:
            store._meetings[mid]["segments"]["s-1"] = _seg(1, "OK claude — summarise so far?")
        return await read(user_id, platform, native)

    store.get_transcript = _later
    body = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD,
                                                         params={"timeout_sec": 5}).json()
    assert body["signal"] == "addressed" and [l["index"] for l in body["lines"]] == [1]
    assert reads["n"] == 3


def test_it_returns_at_once_when_the_meeting_ends():
    store, mid = _store([_seg(0, "Bye everyone.")])
    reads = {"n": 0}
    read = store.get_transcript

    async def _ending(user_id, platform, native):
        reads["n"] += 1
        if reads["n"] == 2:
            store._meetings[mid]["status"] = "completed"
        return await read(user_id, platform, native)

    store.get_transcript = _ending
    body = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD,
                                                         params={"timeout_sec": 5}).json()
    assert body["signal"] == "ended" and body["status"] == "completed"


def test_the_wait_is_bounded_under_the_gateway_forward():
    store, _ = _store([])
    client = TestClient(create_app(store, redis=None))
    assert client.get(URL, headers=HEAD, params={"timeout_sec": 26}).status_code == 422
    assert collector_app.SIGNAL_WAIT_MAX_S < 30


def test_someone_elses_meeting_is_404():
    store, _ = _store([_seg(0, "Claude?")])
    r = TestClient(create_app(store, redis=None)).get(URL, headers={"x-user-id": "8"},
                                                      params={"timeout_sec": 1})
    assert r.status_code == 404


def test_a_listener_s_call_stamps_the_meeting_and_answers_in_text_for_a_script():
    """`vexa-listen` (a shell script) reads the text form, and each of its calls is the panel's
    «Listening» heartbeat: `listener_state` / `listener_ts` on the meeting's metadata."""
    store, mid = _store([_seg(0, "Morning all."), _seg(1, "Claude, read me the action items.", speaker="Bob")])
    r = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD,
                                                     params={"timeout_sec": 1, "listener": 1, "format": "text"})
    assert r.status_code == 200
    assert r.text.splitlines() == ["addressed 2", "Bob: Claude, read me the action items."]
    meta = store._meetings[mid]["data"]["metadata"]
    assert meta["listener_state"] == "listening" and meta["listener_mode"] == "script" and meta["listener_ts"] > 0


def test_when_the_meeting_ends_the_listener_stamp_says_off():
    store, mid = _store([_seg(0, "Bye.")], status="completed")
    r = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD, params={"timeout_sec": 1, "listener": 1, "format": "text"})
    assert r.text.splitlines() == ["ended 1"]
    assert store._meetings[mid]["data"]["metadata"]["listener_state"] == "off"


def test_notes_wake_on_what_the_meeting_page_keeps_with_its_kinds():
    """`notes=1` (founder, 2026-10-25): the listener also keeps the meeting page and its entities
    current during the call, so decisions, commitments, prices, dates and new names wake it too."""
    store, mid = _store([
        _seg(0, "Morning all."),
        _seg(1, "We decided to launch on the 14th."),
        _seg(2, "I'll send the contract to Northwind by Friday.", speaker="Bob"),
        _seg(3, "The price is $4,000 a month."),
        _seg(4, "Договорились, пришлю до пятницы.", speaker="Ира"),
        _seg(5, "Sounds good."),
    ])
    r = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD, params={"timeout_sec": 1, "notes": 1})
    body = r.json()
    assert body["signal"] == "noted" and body["next_index"] == 6
    kinds = {n["index"]: n["kinds"] for n in body["noted"]}
    assert set(kinds) == {1, 2, 3, 4}
    assert "decision" in kinds[1] and "date" in kinds[1]
    assert "commitment" in kinds[2] and "new" in kinds[2] and "date" in kinds[2]
    assert kinds[3] == ["price"]
    assert "decision" in kinds[4] and "commitment" in kinds[4]
    meta = store._meetings[mid]["data"]["metadata"]
    assert meta["listener_noted_index"] == 6 and meta["listener_noted_ts"] > 0


def test_noted_comes_at_most_once_a_minute_and_batches_what_waited():
    store, mid = _store([_seg(0, "We agreed on the budget."), _seg(1, "Deadline is next week.")])
    client = TestClient(create_app(store, redis=None))
    assert client.get(URL, headers=HEAD, params={"timeout_sec": 1, "notes": 1}).json()["signal"] == "noted"
    store._meetings[mid]["segments"]["s-2"] = _seg(2, "Let's go with the annual plan.")
    second = client.get(URL, headers=HEAD, params={"timeout_sec": 1, "notes": 1, "since_index": 2}).json()
    assert second["signal"] == "quiet", "a second noted answer within the minute waits"
    store._meetings[mid]["data"]["metadata"]["listener_noted_ts"] -= 61_000
    store._meetings[mid]["segments"]["s-3"] = _seg(3, "Approved at 20% off.")
    third = client.get(URL, headers=HEAD, params={"timeout_sec": 1, "notes": 1, "since_index": 4}).json()
    assert third["signal"] == "noted" and [n["index"] for n in third["noted"]] == [2, 3]


def test_a_name_heard_before_is_not_new_and_claude_outranks_noted():
    segs = [_seg(0, "I talked to Northwind."), _seg(1, "Northwind liked it, Claude, what next?")]
    store, _ = _store(segs)
    body = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD, params={"timeout_sec": 1, "notes": 1}).json()
    assert body["signal"] == "addressed" and body["noted"] == []
    from meeting_api.collector.app import noted_lines
    assert [n["index"] for n in noted_lines(segs, 0)] == [0]


def test_noted_in_text_for_the_script_and_off_without_notes():
    store, _ = _store([_seg(0, "We decided on Tuesday.")])
    client = TestClient(create_app(store, redis=None))
    assert client.get(URL, headers=HEAD, params={"timeout_sec": 1}).json()["signal"] == "quiet"
    r = client.get(URL, headers=HEAD, params={"timeout_sec": 1, "notes": 1, "listener": 1, "format": "text"})
    assert r.text.splitlines() == ["noted 1", "[decision,date] Anna: We decided on Tuesday."]


# ── shared listening (policy P12): a person a call is shared with listens; only the owner stamps ──
import threading
import time as _time

OWNER = 8


def _shared(segments, viewers=(USER,)):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform="google_meet", native_meeting_id="abc-defg-hij",
                             status="active", data={"transcript_viewers": list(viewers)},
                             segments=segments)
    return store, mid


def test_a_person_the_call_is_shared_with_is_signalled_and_stamps_nothing():
    store, mid = _shared([_seg(0, "Morning."), _seg(1, "Claude, what did we decide?")])
    r = TestClient(create_app(store, redis=None)).get(
        URL, headers=HEAD, params={"timeout_sec": 1, "listener": True, "notes": True})
    assert r.status_code == 200 and r.json()["signal"] == "addressed", r.text
    assert "metadata" not in store._meetings[mid]["data"], "listener/notes write only for the owner"


def test_a_stranger_is_not_signalled():
    store, _ = _shared([_seg(0, "Claude, hi.")], viewers=())
    r = TestClient(create_app(store, redis=None)).get(URL, headers=HEAD, params={"timeout_sec": 1})
    assert r.status_code == 404


def test_a_person_removed_mid_wait_gets_404_within_five_seconds(monkeypatch):
    """Design §4: the wait re-reads through the access rule each tick (N = 5 s)."""
    monkeypatch.setattr(collector_app, "SIGNAL_POLL_S", 2.0)
    from meeting_api.collector import share_access
    store, mid = _shared([_seg(0, "Quiet talk.")])
    client = TestClient(create_app(store, redis=None))
    box = {}
    th = threading.Thread(target=lambda: box.update(r=client.get(
        URL, headers=HEAD, params={"timeout_sec": 20, "since_index": 1})))
    th.start()
    _time.sleep(0.5)
    removed_at = _time.monotonic()
    share_access.remove_viewer(store._meetings[mid]["data"], USER)
    th.join(25)
    assert _time.monotonic() - removed_at <= 5.0
    assert box["r"].status_code == 404, box["r"].text
