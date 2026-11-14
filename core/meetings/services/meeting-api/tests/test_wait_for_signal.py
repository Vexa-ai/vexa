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
