"""#584 — the meetings list omits per-row ``data`` and is bounded by a default page size.

The list endpoints (``GET /bots``, ``GET /meetings``) used to embed each meeting's full ``data``
JSONB per row — 4.6 MB on a real 583-meeting account — which wedged the meeting-api event loop under
morning load (the 2026-07-15 hosted read outage). ``data`` is not part of the sealed api.v1
``MeetingResponse`` schema; the list now returns only the sealed scalar metadata, bounded by a
default page size, and a caller fetches one meeting's ``data`` on demand (``GET /meetings/{id}``).

The projection is gated per caller, on what that caller actually renders. ``list_view`` marks the
paginated list endpoints; ``slim`` marks a non-list caller that renders none of the heavy keys either
(``GET /bots/status`` — #803, where materializing full ``data`` for a running-bots badge cost 180 MB
on one production account and OOM-killed the pod). Only a caller that genuinely needs every key —
``GET /meetings/{id}``, calendar sync, reconciliation — leaves both off. These tests pin each half.

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


# ── #803 · /bots/status is filtered and projected IN THE STORE, not in the route ─────────────────
# The running-bots badge used to read the caller's ENTIRE history with full `data` and filter in
# Python. On a production account that is 4,896 meetings / 180 MB — 144 MB of it `bot_logs`, which
# no endpoint renders. Four concurrent polls demanded ~740 MB transiently; the pod OOM-killed at
# 1 Gi. Both halves of the fix are load-bearing: the status filter bounds the ROWS, the projection
# bounds each row, and stuck non-terminal rows (98 in production) mean the filter alone is not a cap.


_TERMINAL = ("completed", "failed")
_RUNNING = ("requested", "joining", "awaiting_admission", "active", "stopping")


def _seed_history(store, *, running=_RUNNING, terminal_count=40):
    """A realistic account: a few live meetings buried in a long terminal history, every row heavy."""
    for i in range(terminal_count):
        store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id=f"old-{i:04d}",
            status=_TERMINAL[i % len(_TERMINAL)],
            created_at=f"2026-06-01T{i // 60:02d}:{i % 60:02d}:00Z", data=dict(HEAVY_DATA),
        )
    for i, st in enumerate(running):
        store.seed_meeting(
            user_id=USER, platform="google_meet", native_meeting_id=f"live-{i}", status=st,
            created_at=f"2026-06-20T00:{i:02d}:00Z", data=dict(HEAVY_DATA),
        )


def test_bots_status_returns_only_running_and_never_reads_terminal_history():
    store = InMemoryTranscriptStore()
    _seed_history(store)
    r = _client(store).get("/bots/status", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(_RUNNING)
    assert {m["status"] for m in body["running"]} == set(_RUNNING)
    assert all(m["native_meeting_id"].startswith("live-") for m in body["running"]), (
        "a terminal meeting reached the running-bots badge"
    )


def test_bots_status_rows_carry_no_heavy_data():
    """The badge renders a count and a status. Shipping `bot_logs`/`speaker_events` with it was pure
    cost — the bytes are never rendered by any caller of this endpoint."""
    store = InMemoryTranscriptStore()
    _seed_history(store)
    r = _client(store).get("/bots/status", headers=HEADERS)
    for row in r.json()["running"]:
        for heavy in HEAVY_KEYS:
            assert heavy not in row["data"], f"/bots/status still ships heavy key {heavy!r}"
        # the light metadata a caller may legitimately show survives
        assert row["data"].get("title") == "Weekly sync"
    assert len(r.content) < 20_000, f"/bots/status response too large: {len(r.content)} bytes"


def test_bots_status_asks_the_store_for_the_filter_rather_than_filtering_after():
    """The point of introduction: the ROUTE must not receive the terminal rows at all. Pinning the
    store call is what keeps the filter from silently drifting back into Python, where it would
    still produce a correct response while reading the whole account."""
    store = InMemoryTranscriptStore()
    _seed_history(store)
    seen = {}
    original = store.list_meetings

    async def spy(user_id, **kw):
        seen.update(kw)
        rows = await original(user_id, **kw)
        seen["returned"] = len(rows)
        return rows

    store.list_meetings = spy
    r = _client(store).get("/bots/status", headers=HEADERS)
    assert r.status_code == 200
    assert set(seen.get("status") or ()) == set(_RUNNING), (
        f"the status filter did not reach the store: {seen.get('status')!r}"
    )
    assert seen.get("slim") is True, "the projection did not reach the store"
    assert seen["returned"] == len(_RUNNING), (
        f"the store handed the route {seen['returned']} rows for {len(_RUNNING)} running bots — "
        f"the terminal history is still being read"
    )


def test_get_meeting_by_id_is_constrained_in_the_store():
    """Same defect, smaller blast radius: the detail route enumerated the whole account to find one
    row. It must ask for the row — while still returning FULL data, and still refusing a non-owner."""
    store = InMemoryTranscriptStore()
    _seed_history(store)
    mid = _seed_heavy(store, nid="detail-1")
    seen = {}
    original = store.list_meetings

    async def spy(user_id, **kw):
        seen.update(kw)
        rows = await original(user_id, **kw)
        seen["returned"] = len(rows)
        return rows

    store.list_meetings = spy
    r = _client(store).get(f"/meetings/{mid}", headers=HEADERS)
    assert r.status_code == 200
    assert seen.get("meeting_id") == mid, "the id filter did not reach the store"
    assert seen["returned"] == 1, f"the detail route read {seen['returned']} rows to return one"
    # the detail view is exactly where full `data` still belongs
    assert "speaker_events" in r.json()["data"] and "bot_logs" in r.json()["data"]


def test_get_meeting_by_id_still_refuses_a_non_owner():
    """No-regression on the security property: constraining by id must not bypass the access union."""
    store = InMemoryTranscriptStore()
    mid = _seed_heavy(store, nid="private-1")
    r = _client(store).get(f"/meetings/{mid}", headers={"x-user-id": "999"})
    assert r.status_code == 404, "a non-owner must not read another user's meeting"


# ── the sealed api.v1 MeetingResponse hoists completion_reason/failure_stage from `data` ────────────
# MeetingResponse declares `completion_reason` and `failure_stage` at TOP LEVEL, but their values
# live in the `data` jsonb (the lifecycle FSM writes them there). The list projection strips `data`
# down to light keys — so unless the row hoists them like `_meeting_projection_from_row` does, the
# sealed fields are dead. This pins the hoist on both the list row and the detail row.

def test_list_row_hoists_completion_reason_and_failure_stage():
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="term-1", status="completed",
        data={"completion_reason": "left_alone", "failure_stage": "post_active"},
    )
    r = _client(store).get("/meetings", headers=HEADERS)
    assert r.status_code == 200
    (row,) = r.json()["meetings"]
    # the sealed top-level fields carry the values that live in `data`…
    assert row["completion_reason"] == "left_alone"
    assert row["failure_stage"] == "post_active"


def test_detail_row_hoists_completion_reason_and_failure_stage():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="term-2", status="failed",
        data={"completion_reason": "left_alone", "failure_stage": "joining"},
    )
    r = _client(store).get(f"/meetings/{mid}", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["completion_reason"] == "left_alone"
    assert body["failure_stage"] == "joining"


def test_row_completion_fields_are_none_when_absent_from_data():
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="term-3", status="active",
        data={"title": "no terminal fields here"},
    )
    (row,) = _client(store).get("/meetings", headers=HEADERS).json()["meetings"]
    assert row["completion_reason"] is None
    assert row["failure_stage"] is None
