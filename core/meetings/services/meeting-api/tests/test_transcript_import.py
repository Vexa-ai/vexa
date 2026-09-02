"""Transcript IMPORT — a meeting completed from words it already has.

The feature: bring a transcript Vexa did not record (a Zoom export, a TSC recording) into a
meeting row so the product treats it exactly like a captured one. The same route is the rehearsal
rig's capture double (`source="seed"`), which is why the audit's V4/N5 shell-out into this
service's database has somewhere to go.

Offline over the in-memory fake — no docker, no postgres:
  * import → the row is `completed`, with the occurrence window the CALLER stated (the one fact a
    bot run cannot express: it stamps start/end from `now()`);
  * the segments read back through `GET /transcripts/by-id/{id}`, in order;
  * a second import of the same source is a NO-OP — nothing rewritten, `imported: false`;
  * a non-owner is refused, and refused identically to an unknown row (no existence oracle);
  * a row with a bot in flight is refused 409 — the FSM is never fought;
  * malformed bodies are refused whole (an import is one artifact; a hole a caller cannot see is
    worse than a rejection they can read).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

OWNER, STRANGER = 11, 12
PLAT, NID = "jitsi", "dna-tsc-2026-08-03"
WHEN = "2026-08-03T14:00:00Z"

SEGS = [
    {"start": 0.0, "end": 4.5, "speaker": "Larry", "text": "Welcome to the TSC call."},
    {"start": 4.5, "end": 9.25, "speaker": "Marvin", "text": "Two items on the agenda today."},
    {"start": 9.25, "end": 15.0, "speaker": "Larry", "text": "Let us start with the first."},
]


def _client(status="scheduled", user_id=OWNER):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=user_id, platform=PLAT, native_meeting_id=NID,
                             status=status, start_time=None, end_time=None)
    return TestClient(create_app(store, redis=None)), store, mid


def _import(client, mid, uid=OWNER, **over):
    body = {"segments": SEGS, "started_at": WHEN, "source": "import"}
    body.update(over)
    return client.post(f"/meetings/{mid}/transcript-import", json=body,
                       headers={"x-user-id": str(uid)})


def test_import_completes_the_row_with_the_stated_occurrence_window():
    client, _store, mid = _client()
    r = _import(client, mid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] is True
    assert body["status"] == "completed"
    assert body["segments_imported"] == 3
    # The window the CALLER stated, not `now()` — a meeting that happened last Tuesday is
    # expressible, which is the whole reason the rig used to UPDATE the columns by hand.
    assert body["start_time"].startswith("2026-08-03T14:00:00")
    # No `ended_at` given → the transcript's own length (last segment's end = 15s).
    assert body["end_time"].startswith("2026-08-03T14:00:15")
    assert body["session_uid"] == f"import-import-{mid}"


def test_explicit_ended_at_wins_over_the_transcripts_length():
    client, _store, mid = _client()
    r = _import(client, mid, ended_at="2026-08-03T15:30:00Z")
    assert r.status_code == 200
    assert r.json()["end_time"].startswith("2026-08-03T15:30:00")


def test_imported_segments_read_back_through_the_by_id_transcript():
    """The product read a terminal canvas uses — an imported meeting must look like a recorded one."""
    client, _store, mid = _client()
    _import(client, mid)
    doc = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)})
    assert doc.status_code == 200
    body = doc.json()
    assert body["status"] == "completed"
    assert body["start_time"].startswith("2026-08-03T14:00:00")
    texts = [s["text"] for s in body["segments"]]
    assert texts == [s["text"] for s in SEGS]           # in order
    assert body["segments"][1]["speaker"] == "Marvin"   # speakers survive


def test_a_second_import_of_the_same_source_writes_nothing():
    client, store, mid = _client()
    first = _import(client, mid).json()
    doc_before = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)}).json()

    again = _import(client, mid)
    assert again.status_code == 200
    assert again.json()["imported"] is False
    assert again.json()["segments_imported"] == first["segments_imported"]
    assert again.json()["imported_at"] == first["imported_at"]  # the FIRST import's stamp

    doc_after = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)}).json()
    assert doc_after["segments"] == doc_before["segments"]      # not doubled, not rewritten
    assert len(doc_after["segments"]) == 3


def test_a_changed_transcript_from_the_same_source_is_still_one_import():
    """Idempotency is on the SOURCE, not on the bytes: re-sending different words for the same
    (source, meeting) is the same import, and does not silently rewrite the meeting."""
    client, _store, mid = _client()
    _import(client, mid)
    again = _import(client, mid, segments=[{"start": 0.0, "end": 1.0, "speaker": "X", "text": "different"}])
    assert again.status_code == 200 and again.json()["imported"] is False
    doc = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)}).json()
    assert [s["text"] for s in doc["segments"]] == [s["text"] for s in SEGS]


def test_a_different_source_is_a_different_import():
    """`seed` and `import` derive different session uids, so the double and a real import of the
    same meeting are distinguishable — and neither is mistaken for the other's replay."""
    client, _store, mid = _client()
    assert _import(client, mid, source="seed").json()["session_uid"] == f"import-seed-{mid}"
    assert _import(client, mid, source="seed").json()["imported"] is False


def test_non_owner_is_refused_exactly_like_an_unknown_row():
    client, _store, mid = _client()
    mine = _import(client, mid, uid=STRANGER)
    unknown = _import(client, 987654, uid=STRANGER)
    assert mine.status_code == 404 and unknown.status_code == 404
    assert mine.json()["detail"] == f"Meeting {mid} not found"
    assert unknown.json()["detail"] == "Meeting 987654 not found"
    # and nothing was written
    doc = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)}).json()
    assert doc["status"] == "scheduled" and doc["segments"] == []


def test_a_row_with_a_bot_in_flight_is_refused():
    for live in ("joining", "active", "awaiting_admission", "stopping"):
        client, _store, mid = _client(status=live)
        r = _import(client, mid)
        assert r.status_code == 409, f"{live} → {r.status_code}"
        assert live in r.json()["detail"]


def test_an_already_completed_row_may_be_imported_into():
    """A meeting whose bot ran and completed is terminal, not in flight — importing an external
    transcript onto it is a legitimate act (a better recording of the same call)."""
    client, _store, mid = _client(status="completed")
    assert _import(client, mid).status_code == 200


def test_malformed_bodies_are_refused_whole():
    client, _store, mid = _client()
    cases = [
        ({"segments": [], "started_at": WHEN}, "non-empty"),
        ({"segments": SEGS}, "started_at"),
        ({"segments": SEGS, "started_at": "yesterday"}, "started_at"),
        ({"segments": SEGS, "started_at": WHEN, "ended_at": "2026-08-03T13:00:00Z"}, "before"),
        ({"segments": SEGS, "started_at": WHEN, "source": "recording"}, "source"),
        ({"segments": [{"start": "x", "end": 1}], "started_at": WHEN}, "segment 0"),
        ({"segments": [{"start": 0, "end": 1, "text": 5}], "started_at": WHEN}, "segment 0"),
    ]
    for body, needle in cases:
        r = client.post(f"/meetings/{mid}/transcript-import", json=body,
                        headers={"x-user-id": str(OWNER)})
        assert r.status_code == 422, f"{body} → {r.status_code}"
        assert needle in r.json()["detail"], f"{body} → {r.json()['detail']}"
    # the row is untouched by every one of them
    doc = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OWNER)}).json()
    assert doc["status"] == "scheduled" and doc["segments"] == []


def test_identity_is_required():
    client, _store, mid = _client()
    r = client.post(f"/meetings/{mid}/transcript-import",
                    json={"segments": SEGS, "started_at": WHEN})
    assert r.status_code == 401


def test_the_import_route_does_not_shadow_the_share_pair_route():
    """Three segments against four — and a non-numeric id is refused by validation here rather than
    resolved as a platform name. The property the by-id share mint relies on, asserted again."""
    client, _store, mid = _client()
    r = client.post("/meetings/google_meet/transcript-import",
                    json={"segments": SEGS, "started_at": WHEN}, headers={"x-user-id": str(OWNER)})
    assert r.status_code == 422
    ok = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"},
                     headers={"x-user-id": str(OWNER)})
    assert ok.status_code == 200


def test_epoch_seconds_are_accepted_for_the_window():
    client, _store, mid = _client()
    r = _import(client, mid, started_at=1785765600)   # 2026-08-03T14:00:00Z
    assert r.status_code == 200
    assert r.json()["start_time"].startswith("2026-08-03T14:00:00")
