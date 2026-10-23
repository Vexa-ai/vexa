"""Transcript gaps — audio the bot never transcribed is a RECORD on the meeting (prod-cutover R4).

The bot reports every stretch of captured audio its lanes will never transcribe on the TERMINAL
lifecycle.v1 event as ``transcript_gaps`` (additive, like ``stt_fault``). This pins the control
plane's half:

  * the receiver persists it at ``meeting.data.transcript_gaps`` (the queryable record);
  * NO existing response changes shape — the key is dropped from every ``data`` projection, the
    webhook payload, and the transcript document, so a 0.12 client sees byte-identical responses;
  * a reader that ASKS (``?include_gaps=true`` on the transcript routes) gets a top-level ``gaps``
    list placed on the meeting's own clock, with the "gap mm:ss–mm:ss (not transcribed)" label;
  * the receiver counts it (``transcript_gaps_recorded`` logevent: gap count + seconds).
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.projection import project_list_data, project_response_data
from meeting_api.lifecycle import MeetingStore
from meeting_api.lifecycle.receiver import conforms, create_app as create_receiver
from meeting_api.webhooks.delivery import clean_meeting_data

USER = 7
START = "2026-06-20T09:00:00Z"
START_EPOCH = 1781946000.0  # 2026-06-20T09:00:00Z

GAPS = [
    {"start": START_EPOCH + 75.0, "end": START_EPOCH + 111.0,
     "absolute_start_time": "2026-06-20T09:01:15.000Z", "absolute_end_time": "2026-06-20T09:01:51.000Z",
     "speaker": "Anna", "reason": "stt refused the window after 4 attempt(s) (unavailable HTTP 503)"},
]

JOINING = {"connection_id": "sess-gaps", "container_id": "mtg-1-bot", "status": "joining"}
ACTIVE = {"connection_id": "sess-gaps", "status": "active"}


def _drive(client, *events):
    out = []
    for ev in events:
        r = client.post("/bots/internal/callback/lifecycle", json=ev)
        assert r.status_code == 200, r.text
        out.append(r.json())
    return out


def _receiver():
    deliveries: list = []
    app = create_receiver(store=MeetingStore(), on_status_change=lambda e: deliveries.append(e))
    return TestClient(app), deliveries


# ── the receiver: persisted on a terminal, ignored elsewhere ─────────────────────────────────────

def test_terminal_event_with_gaps_conforms_and_is_persisted():
    terminal = {"connection_id": "sess-gaps", "status": "completed", "exit_code": 0,
                "completion_reason": "stopped", "transcript_gaps": GAPS}
    conforms(terminal, "LifecycleEvent")  # additive on the sealed contract
    client, deliveries = _receiver()
    final = _drive(client, JOINING, ACTIVE, terminal)[-1]
    assert final["meeting_status"] == "completed"
    persisted = final["data"]["transcript_gaps"]
    assert persisted == GAPS


def test_a_healthy_meeting_carries_no_gaps_key():
    client, _ = _receiver()
    final = _drive(client, JOINING, ACTIVE, {
        "connection_id": "sess-gaps", "status": "completed", "exit_code": 0, "completion_reason": "stopped",
    })[-1]
    assert "transcript_gaps" not in final["data"]


def test_gaps_on_a_non_terminal_event_are_ignored():
    client, _ = _receiver()
    final = _drive(client, JOINING, {**ACTIVE, "transcript_gaps": GAPS})[-1]
    assert "transcript_gaps" not in final.get("data", {})


def test_malformed_gaps_are_dropped_not_persisted():
    client, _ = _receiver()
    bad = [{"start": "x"}, "nope", {"start": 10, "end": 5}, GAPS[0]]
    final = _drive(client, JOINING, ACTIVE, {
        "connection_id": "sess-gaps", "status": "completed", "exit_code": 0,
        "completion_reason": "stopped", "transcript_gaps": bad,
    })[-1]
    assert final["data"]["transcript_gaps"] == GAPS


def test_the_status_change_webhook_does_not_carry_gaps():
    client, deliveries = _receiver()
    _drive(client, JOINING, ACTIVE, {"connection_id": "sess-gaps", "status": "completed", "exit_code": 0,
                                     "completion_reason": "stopped", "transcript_gaps": GAPS})
    hooks = [d for d in deliveries if d["event_type"] == "meeting.status_change"]
    assert hooks and all("transcript_gaps" not in (h["data"]["meeting"].get("data") or {}) for h in hooks)
    assert "transcript_gaps" not in clean_meeting_data({"transcript_gaps": GAPS, "title": "t"})


def test_the_receiver_counts_recorded_gaps(capsys):
    client, _ = _receiver()
    _drive(client, JOINING, ACTIVE, {"connection_id": "sess-gaps", "status": "completed", "exit_code": 0,
                                     "completion_reason": "stopped", "transcript_gaps": GAPS})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    counted = [e for e in lines if e.get("event") == "transcript_gaps_recorded"]
    assert counted, lines
    fields = counted[-1].get("fields") or {}
    assert fields.get("gap_count") == 1
    assert abs(fields.get("gap_seconds") - 36.0) < 1e-6


# ── every existing response is byte-identical ────────────────────────────────────────────────────

def test_projections_never_carry_gaps():
    data = {"title": "t", "transcript_gaps": GAPS}
    assert "transcript_gaps" not in project_response_data(data, viewer_is_owner=True)
    assert "transcript_gaps" not in project_list_data(data, viewer_is_owner=True)


def _client(store):
    from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
    from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher
    return TestClient(create_app(
        transcript_store=store, meeting_repo=InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(), command_publisher=InMemoryCommandPublisher(),
    ))


def _seed(store, *, gaps, native):
    return store.seed_meeting(
        user_id=USER, platform="jitsi", native_meeting_id=native, status="completed",
        start_time=START, data={"title": "R4", **({"transcript_gaps": gaps} if gaps is not None else {})},
        segments=[{"segment_id": "turn:0:0", "start": START_EPOCH + 2.0, "end": START_EPOCH + 5.0,
                   "text": "The database is the bottleneck.", "language": "en", "speaker": "Anna",
                   "completed": True}],
    )


def test_transcript_responses_are_byte_identical_with_or_without_gaps():
    store = InMemoryTranscriptStore()
    with_gaps = _seed(store, gaps=GAPS, native="room-a")
    without = _seed(store, gaps=None, native="room-b")
    c = _client(store)
    h = {"X-User-Id": str(USER)}
    for path_a, path_b in (("/transcripts/jitsi/room-a", "/transcripts/jitsi/room-b"),
                           (f"/transcripts/by-id/{with_gaps}", f"/transcripts/by-id/{without}")):
        a, b = c.get(path_a, headers=h), c.get(path_b, headers=h)
        assert a.status_code == b.status_code == 200, (a.text, b.text)
        ja, jb = a.json(), b.json()
        for k in ("id", "native_meeting_id", "constructed_meeting_url"):
            ja.pop(k, None)
            jb.pop(k, None)
        assert ja == jb, "a 0.12 reader must see the same document whether or not the meeting lost audio"
        assert "gaps" not in a.json() and "transcript_gaps" not in a.json().get("data", {})
    detail = c.get(f"/meetings/{with_gaps}", headers=h)
    if detail.status_code == 200:
        assert "transcript_gaps" not in (detail.json().get("data") or {})
    listing = c.get("/meetings", headers=h)
    assert listing.status_code == 200
    assert all("transcript_gaps" not in (m.get("data") or {}) for m in listing.json().get("meetings", []))


def test_a_reader_that_asks_gets_the_gaps_on_the_meeting_clock():
    store = InMemoryTranscriptStore()
    mid = _seed(store, gaps=GAPS, native="room-a")
    c = _client(store)
    h = {"X-User-Id": str(USER)}
    for path in ("/transcripts/jitsi/room-a?include_gaps=true", f"/transcripts/by-id/{mid}?include_gaps=true"):
        r = c.get(path, headers=h)
        assert r.status_code == 200, r.text
        gaps = r.json()["gaps"]
        assert len(gaps) == 1
        g = gaps[0]
        assert g["start"] == GAPS[0]["start"] and g["end"] == GAPS[0]["end"]
        assert g["absolute_start_time"] == GAPS[0]["absolute_start_time"]
        assert g["start_offset_s"] == 75.0 and g["end_offset_s"] == 111.0
        assert g["label"] == "gap 01:15–01:51 (not transcribed)"
        assert g["reason"].startswith("stt refused")
        # segments are untouched
        assert [s["text"] for s in r.json()["segments"]] == ["The database is the bottleneck."]


def test_asking_on_a_meeting_without_gaps_returns_an_empty_list():
    store = InMemoryTranscriptStore()
    mid = _seed(store, gaps=None, native="room-b")
    r = _client(store).get(f"/transcripts/by-id/{mid}?include_gaps=true", headers={"X-User-Id": str(USER)})
    assert r.status_code == 200 and r.json()["gaps"] == []
