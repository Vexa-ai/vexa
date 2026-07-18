"""#584 — the meetings list omits per-row ``data`` and is bounded by a default page size.

The list endpoints (``GET /bots``, ``GET /meetings``) used to embed each meeting's full ``data``
JSONB per row — 4.6 MB on a real 583-meeting account — which wedged the meeting-api event loop under
morning load (the 2026-07-15 hosted read outage). ``data`` is not part of the sealed api.v1
``MeetingResponse`` schema; the list now returns only the sealed scalar metadata, bounded by a
default page size, and a caller fetches one meeting's ``data`` on demand (``GET /meetings/{id}``).

The fix is gated on a ``list_view`` flag so the internal callers that REUSE ``list_meetings`` to
enumerate a user's meetings (``GET /meetings/{id}`` filter, ``GET /bots/status``, calendar sync) are
unchanged — full ``data``, no default cap. These tests pin both halves.

Drives the SHIPPED meeting-api handlers over the in-memory fakes (TestClient, offline) and the store
directly. The fake mirrors the real ``SqlAlchemyTranscriptStore`` (both share
``collector/projection.py``), so the list-shape behaviour proven here is the shipped behaviour.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.projection import DEFAULT_LIST_LIMIT
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

USER = 7
HEADERS = {"x-user-id": str(USER)}

# A meeting whose ``data`` carries the heavy detail keys the list must never ship (the outage cause),
# plus the light ``constructed_meeting_url`` the list promotes to a top-level scalar.
HEAVY_DATA = {
    # light keys the LIST renders — must survive the projection
    "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
    "title": "Weekly sync",
    "docs": [{"workspace": "u", "path": "notes.md"}],
    # heavy detail keys the LIST must never ship (the outage cause)
    "speaker_events": [{"i": i, "t": "x" * 64} for i in range(2000)],   # the ~3 MB-class key
    "bot_logs": ["log-line " * 8] * 2000,
    "recordings": [{"id": "r1", "url": "s3://…"}],
    "status_transition": [{"to": "active"}],
    "chat_messages": [{"m": "hi"}],
    "last_error": {"trace": "x" * 5000},
}

HEAVY_KEYS = ("speaker_events", "bot_logs", "recordings", "status_transition",
              "chat_messages", "error_details", "last_error")


def _client(store):
    return TestClient(create_app(
        transcript_store=store,
        meeting_repo=InMemoryMeetingRepo(),
        runtime=FakeRuntimeClient(),
        command_publisher=InMemoryCommandPublisher(),
    ))


def _seed_heavy(store, nid="abc-defg-hij"):
    return store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id=nid, status="active",
        constructed_meeting_url="https://meet.google.com/abc-defg-hij", data=dict(HEAVY_DATA),
    )


def _seed_n(store, n):
    for i in range(n):
        store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id=f"m-{i:04d}", status="active",
            created_at=f"2026-06-20T{i // 60:02d}:{i % 60:02d}:00Z", data=dict(HEAVY_DATA),
        )


# ── C1 · the LIST omits `data` (route-level, both endpoints) ───────────────────────────────────────

@pytest.mark.parametrize("path", ["/bots", "/meetings"])
def test_list_row_drops_heavy_data_keeps_light(path):
    store = InMemoryTranscriptStore()
    _seed_heavy(store)
    r = _client(store).get(path, headers=HEADERS)
    assert r.status_code == 200
    (row,) = r.json()["meetings"]
    # #584: the heavy detail keys (the 4.6 MB / event-loop-wedge cause) are gone from the list row…
    for heavy in HEAVY_KEYS:
        assert heavy not in row["data"], f"list row still ships heavy key {heavy!r}"
    # …but the light metadata the list actually renders survives.
    assert row["data"].get("title") == "Weekly sync"
    assert row["data"].get("docs") == [{"workspace": "u", "path": "notes.md"}]
    assert row["constructed_meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert row["status"] == "active" and row["native_meeting_id"] == "abc-defg-hij"
    # the whole list response is a few KB, not the multi-MB the stored data would make.
    assert len(r.content) < 20_000, f"list response too large: {len(r.content)} bytes"


def test_get_meeting_by_id_still_returns_full_data():
    """A3 — the detail path (GET /meetings/{id}) reuses list_meetings on the INTERNAL path, so it
    still returns the full `data`; only the LIST drops it."""
    store = InMemoryTranscriptStore()
    mid = _seed_heavy(store)
    c = _client(store)
    # list row: heavy keys dropped
    list_row = c.get("/bots", headers=HEADERS).json()["meetings"][0]
    assert "speaker_events" not in list_row["data"] and "recordings" not in list_row["data"]
    # detail row (GET /meetings/{id}, internal path): full data, heavy keys present
    detail = c.get(f"/meetings/{mid}", headers=HEADERS)
    assert detail.status_code == 200
    body = detail.json()
    assert "data" in body and "speaker_events" in body["data"] and "recordings" in body["data"]


# ── C2 · default page size + honest has_more ───────────────────────────────────────────────────────

def test_bots_has_more_reflects_more_not_hardcoded_false():
    store = InMemoryTranscriptStore()
    _seed_n(store, 2)
    c = _client(store)
    # one-per-page over two meetings → there IS more (was hardcoded `false` before #584)
    r1 = c.get("/bots", headers=HEADERS, params={"limit": 1})
    assert r1.status_code == 200 and len(r1.json()["meetings"]) == 1
    assert r1.json()["has_more"] is True
    # the whole (small) set on one page → no more
    r2 = c.get("/bots", headers=HEADERS, params={"limit": 100})
    assert r2.json()["has_more"] is False


async def test_list_view_applies_default_limit_and_has_more():
    """The store's list-view path caps an unbounded request at DEFAULT_LIST_LIMIT and reports more."""
    store = InMemoryTranscriptStore()
    _seed_n(store, DEFAULT_LIST_LIMIT + 10)   # 60
    rows, has_more = await store.list_meetings(USER, list_view=True)   # no explicit limit
    assert len(rows) == DEFAULT_LIST_LIMIT and has_more is True
    # an explicit limit still wins and its has_more is honest
    rows2, more2 = await store.list_meetings(USER, list_view=True, limit=100)
    assert len(rows2) == DEFAULT_LIST_LIMIT + 10 and more2 is False


# ── the internal path is UNCHANGED — no default cap, full data (protects get-by-id / status / sync) ─

async def test_internal_path_is_unbounded_and_keeps_data():
    store = InMemoryTranscriptStore()
    _seed_n(store, DEFAULT_LIST_LIMIT + 10)   # 60
    rows = await store.list_meetings(USER)    # list_view=False (default) → plain list, no cap
    assert isinstance(rows, list) and len(rows) == DEFAULT_LIST_LIMIT + 10   # NOT capped to 50
    assert all("data" in r for r in rows)     # full data retained for internal reuse
