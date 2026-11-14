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
