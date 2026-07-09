"""P0 (release blocker) — the cross-tenant transcript leak + wrong-row hydration, encoded as red-then-green
regressions at the collector's READ + INGEST seams.

Root cause: ``native_meeting_id`` was used as BOTH the redis-stream key AND the transcript read key, but a
native id is NOT unique — it collides across DIFFERENT users of the same meeting link (a cross-tenant LEAK)
and across ONE user's repeated meeting rows (wrong-row hydration). The per-user meeting ROW id is the only
correct key. These tests assert isolation across BOTH the stream/carrier path (ingest → tc:meeting:{key})
and the REST read path (GET /transcripts/by-id/{row} vs the legacy native path).

Deterministic + offline: fakeredis + explicit ``ingest`` + a TestClient over the in-memory store.
"""
from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import create_app, ingest
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore

NATIVE = "abc-defg-hij"  # the SHARED meeting link both symptoms hinge on


@pytest.fixture
async def bus():
    client = fakeredis.aioredis.FakeRedis()
    b = FakeRedisBus(client)
    yield b
    await client.aclose()


def _seg(seg_id, text, *, start=1.0, speaker="Alice"):
    return {"segment_id": seg_id, "start": start, "end": start + 1.0, "text": text,
            "speaker": speaker, "language": "en", "completed": True}


def _message(meeting_id, segments, *, native=NATIVE):
    return {"payload": json.dumps({
        "type": "transcription", "meeting_id": str(meeting_id),
        "native_meeting_id": native, "platform": "google_meet", "segments": segments})}


async def _feed(client, key):
    """The transcript-carrier feed entries under tc:meeting:{key} (the exact key ingest wrote)."""
    rows = await client.xrange(f"tc:meeting:{key}")
    out = []
    for _id, fields in rows:
        raw = fields.get(b"payload") or fields.get("payload")
        out.append(json.loads(raw.decode() if isinstance(raw, bytes) else raw))
    return out


# ── SYMPTOM 1: CROSS-TENANT LEAK — two DIFFERENT users, SAME native, distinct rows ────────────────────

async def test_cross_tenant_carrier_isolation(bus):
    """User A (row 10) and user B (row 20) each ran a bot in the SAME meeting link. A produces
    transcript content; B's meeting must read EMPTY of A's data on the STREAM/carrier path. On the
    OLD native-keyed carrier both wrote/read tc:meeting:{NATIVE} → B saw A's transcript (the LEAK).
    Now each keys on its ROW id, so the carriers are disjoint."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=10)
    store.seed_meeting(user_id=200, platform="google_meet", native_meeting_id=NATIVE, meeting_id=20)

    # Only user A's bot (row 10) produces segments.
    await ingest(store, bus, _message(10, [_seg("a1", "A secret revenue number")]))

    # A's ROW-keyed carrier has A's content; B's ROW-keyed carrier is EMPTY; the shared NATIVE key is
    # NEVER written (the leak channel is gone).
    assert [e["segments"][0]["text"] for e in await _feed(bus._client, 10)] == ["A secret revenue number"]
    assert await _feed(bus._client, 20) == []
    assert await _feed(bus._client, NATIVE) == []


async def test_cross_tenant_rest_isolation():
    """REST read path: user B, fetching THEIR OWN row (20) on the same native, reads NONE of user A's
    (row 10) transcript OR processed notes — via BOTH the by-id path and the native path. A row owned by
    another user is 404 on the by-id path (never another tenant's data)."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=10,
        segments=[dict(_seg("a1", "A confidential line"))],
        data={"processed": {"views": [{"id": "copilot-notes", "doc": {"notes": [
            {"id": "a1", "text": "A private note"}]}}]}},
    )
    store.seed_meeting(user_id=200, platform="google_meet", native_meeting_id=NATIVE, meeting_id=20)
    client = TestClient(create_app(store, redis=None))

    # B reads THEIR row (20) by id → empty of A's data.
    r = client.get("/transcripts/by-id/20", headers={"x-user-id": "200"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segments"] == []
    notes = (body.get("data") or {}).get("processed", {}).get("views", [])
    assert notes == [] or all(not v.get("doc", {}).get("notes") for v in notes)

    # B reads the native path → resolves to B's OWN newest row (20), still empty of A's data.
    r = client.get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": "200"})
    assert r.status_code == 200, r.text
    assert r.json()["segments"] == []

    # B CANNOT read A's row by id — owner-scoped 404 (not a leak).
    r = client.get("/transcripts/by-id/10", headers={"x-user-id": "200"})
    assert r.status_code == 404

    # A reads A's row → sees A's own content (sanity: not over-filtered).
    r = client.get("/transcripts/by-id/10", headers={"x-user-id": "100"})
    assert r.status_code == 200
    assert [s["text"] for s in r.json()["segments"]] == ["A confidential line"]


# ── SYMPTOM 2: WRONG-ROW HYDRATION — ONE user, TWO rows, SAME native ──────────────────────────────────

async def test_wrong_row_carrier_isolation(bus):
    """One user re-sent the bot on the same link: row 1 (older) and row 2 (newer). Each keys on its row
    id, so a segment for row 1 never lands on row 2's carrier (the collapse the old native key caused)."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=1)
    store.seed_meeting(user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=2)

    await ingest(store, bus, _message(1, [_seg("s1", "row-1 line")]))

    assert [e["segments"][0]["text"] for e in await _feed(bus._client, 1)] == ["row-1 line"]
    assert await _feed(bus._client, 2) == []


def test_wrong_row_rest_by_id_addresses_the_exact_row():
    """One user, two rows on the SAME native: row 1 has notes+segments, row 2 is empty. The by-id read
    returns EXACTLY the addressed row (row 1 → its notes; row 2 → empty), so the terminal never collapses
    to the newest. The native path (legacy) resolves to the NEWEST row (2) — documented behaviour."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=1,
        created_at="2026-06-20T08:00:00Z",
        segments=[dict(_seg("s1", "row-1 transcript"))],
        data={"processed": {"views": [{"id": "copilot-notes", "doc": {"notes": [
            {"id": "s1", "text": "row-1 processed note"}]}}]}},
    )
    store.seed_meeting(
        user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=2,
        created_at="2026-06-20T09:00:00Z",  # newer
    )
    client = TestClient(create_app(store, redis=None))

    # by-id row 1 → row 1's own notes + segments (never collapsed to the newest/empty row 2).
    b1 = client.get("/transcripts/by-id/1", headers={"x-user-id": "100"}).json()
    assert [s["text"] for s in b1["segments"]] == ["row-1 transcript"]
    v1 = b1["data"]["processed"]["views"][0]["doc"]["notes"]
    assert [n["text"] for n in v1] == ["row-1 processed note"]

    # by-id row 2 → empty (its own state), NOT row 1's notes.
    b2 = client.get("/transcripts/by-id/2", headers={"x-user-id": "100"}).json()
    assert b2["segments"] == []

    # native path → the NEWEST row (2), empty — the exact symptom-2 ambiguity the by-id path avoids.
    nat = client.get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": "100"}).json()
    assert nat["id"] == 2 and nat["segments"] == []


# ── LEGIT CASE PRESERVED — two users each own a row in the SAME real meeting ───────────────────────────

async def test_two_tenants_same_meeting_keep_separate_correct_transcripts(bus):
    """Two users each run their OWN bot in the same real meeting → SEPARATE, CORRECT transcripts (not
    merged, not blocked). Each row's carrier + REST read carries only that user's content."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=100, platform="google_meet", native_meeting_id=NATIVE, meeting_id=10)
    store.seed_meeting(user_id=200, platform="google_meet", native_meeting_id=NATIVE, meeting_id=20)

    await ingest(store, bus, _message(10, [_seg("a", "hello from A", speaker="A")]))
    await ingest(store, bus, _message(20, [_seg("b", "hello from B", speaker="B")]))

    assert [e["segments"][0]["text"] for e in await _feed(bus._client, 10)] == ["hello from A"]
    assert [e["segments"][0]["text"] for e in await _feed(bus._client, 20)] == ["hello from B"]

    client = TestClient(create_app(store, redis=None))
    a = client.get("/transcripts/by-id/10", headers={"x-user-id": "100"}).json()
    b = client.get("/transcripts/by-id/20", headers={"x-user-id": "200"}).json()
    assert [s["text"] for s in a["segments"]] == ["hello from A"]
    assert [s["text"] for s in b["segments"]] == ["hello from B"]
