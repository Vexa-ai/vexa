"""POST/PATCH/DELETE /meetings — PLANNED meetings (created ahead of time, NO bot spawned).

A planned meeting is a normal ``meetings`` row born in an INTENT status (`scheduled` when it has a
time, else `idle`). It can be created from a pasted link (platform/native parsed server-side) or
link-less (platform='unknown', NULL native — addressed by ROW id). It carries `data.title`,
`data.scheduled_at`, `data.workspace_id` (the sharing bind), and `data.auto_join`. PATCH/DELETE are
refused (409) once the bot FSM owns the row. Members of the bound workspace see the row via
GET /meetings (x-user-workspaces) — sharing needs no new authz.

Drives the collector ``create_app`` over the in-memory fake, OFFLINE (TestClient, no docker/DB).
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.meeting_link import find_meeting_link, parse_meeting_url

USER = 7
H = {"x-user-id": str(USER)}
# Binding a planned meeting into a workspace takes write access there (meeting_api.workspace_write).
H = {**H, "x-user-writable-workspaces": "ws-1,ws-9,ws-42"}
AT = "2026-07-10T15:00:00Z"
URL = "https://meet.google.com/abc-defg-hij"


class _CaptureRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, data):
        self.published.append((channel, data))


def _client():
    store = InMemoryTranscriptStore()
    redis = _CaptureRedis()
    return TestClient(create_app(store, redis=redis)), store, redis


# ---- create -------------------------------------------------------------------------

def test_create_with_link_and_time_is_scheduled():
    client, store, redis = _client()
    r = client.post("/meetings", json={
        "title": "Q3 kickoff", "scheduled_at": AT, "meeting_url": URL,
        "workspace_id": "ws-1",
    }, headers=H)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "scheduled"
    assert row["platform"] == "google_meet"
    assert row["native_meeting_id"] == "abc-defg-hij"
    assert row["data"]["title"] == "Q3 kickoff"
    assert row["data"]["scheduled_at"] == AT
    assert row["data"]["workspace_id"] == "ws-1"
    assert row["data"]["auto_join"] is True  # default ON
    # the frame fans out to the user channel so the list refreshes live
    channel, raw = redis.published[0]
    assert channel == f"u:{USER}:meetings"
    frame = json.loads(raw)
    assert frame["status"] == "scheduled" and frame["when"] == AT


def test_create_linkless_and_timeless_is_idle_unknown():
    client, _store, _redis = _client()
    r = client.post("/meetings", json={"title": "Someday sync"}, headers=H)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "idle"
    assert row["platform"] == "unknown"
    assert row["native_meeting_id"] is None


def test_create_auto_join_opt_out():
    client, _store, _redis = _client()
    r = client.post("/meetings", json={"scheduled_at": AT, "auto_join": False}, headers=H)
    assert r.status_code == 201
    assert r.json()["data"]["auto_join"] is False


def test_create_unparseable_url_422():
    client, _store, _redis = _client()
    r = client.post("/meetings", json={"meeting_url": "https://example.com/nope"}, headers=H)
    assert r.status_code == 422


def test_create_duplicate_active_native_409():
    client, store, _redis = _client()
    store.seed_meeting(user_id=USER, platform="google_meet",
                       native_meeting_id="abc-defg-hij", status="active")
    r = client.post("/meetings", json={"meeting_url": URL}, headers=H)
    assert r.status_code == 409


def test_create_after_terminal_row_is_allowed():
    client, store, _redis = _client()
    store.seed_meeting(user_id=USER, platform="google_meet",
                       native_meeting_id="abc-defg-hij", status="completed")
    r = client.post("/meetings", json={"meeting_url": URL}, headers=H)
    assert r.status_code == 201


def test_create_two_linkless_plans_allowed():
    client, _store, _redis = _client()
    assert client.post("/meetings", json={"title": "a"}, headers=H).status_code == 201
    assert client.post("/meetings", json={"title": "b"}, headers=H).status_code == 201


def test_create_requires_identity():
    client, _store, _redis = _client()
    assert client.post("/meetings", json={"title": "x"}).status_code == 401


# ---- patch --------------------------------------------------------------------------

def test_patch_title_and_time():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"title": "old"}, headers=H).json()["id"]
    r = client.patch(f"/meetings/{mid}", json={"title": "new", "scheduled_at": AT}, headers=H)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["data"]["title"] == "new"
    assert row["status"] == "scheduled"  # gaining a time flips idle → scheduled


def test_patch_clearing_time_flips_to_idle():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"scheduled_at": AT}, headers=H).json()["id"]
    row = client.patch(f"/meetings/{mid}", json={"scheduled_at": None}, headers=H).json()
    assert row["status"] == "idle"
    assert "scheduled_at" not in row["data"]


def test_patch_attach_link():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    row = client.patch(f"/meetings/{mid}", json={"meeting_url": URL}, headers=H).json()
    assert row["platform"] == "google_meet"
    assert row["native_meeting_id"] == "abc-defg-hij"
    assert row["constructed_meeting_url"] == URL


def test_patch_link_collision_409():
    client, store, _redis = _client()
    store.seed_meeting(user_id=USER, platform="google_meet",
                       native_meeting_id="abc-defg-hij", status="active")
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    r = client.patch(f"/meetings/{mid}", json={"meeting_url": URL}, headers=H)
    assert r.status_code == 409


def test_patch_workspace_bind_and_unbind():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    assert client.patch(f"/meetings/{mid}", json={"workspace_id": "ws-9"},
                        headers=H).json()["data"]["workspace_id"] == "ws-9"
    row = client.patch(f"/meetings/{mid}", json={"workspace_id": None}, headers=H).json()
    assert "workspace_id" not in row["data"]


def test_patch_fsm_row_409():
    client, store, _redis = _client()
    mid = store.seed_meeting(user_id=USER, platform="google_meet",
                             native_meeting_id="xxx-xxxx-xxx", status="active")
    r = client.patch(f"/meetings/{mid}", json={"title": "nope"}, headers=H)
    assert r.status_code == 409


def test_patch_owner_scoped_404():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    r = client.patch(f"/meetings/{mid}", json={"title": "y"}, headers={"x-user-id": "999"})
    assert r.status_code == 404


def test_patch_empty_body_422():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    assert client.patch(f"/meetings/{mid}", json={}, headers=H).status_code == 422


# ---- delete -------------------------------------------------------------------------

def test_delete_planned_row():
    client, store, _redis = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    assert client.delete(f"/meetings/{mid}", headers=H).status_code == 204
    assert mid not in store._meetings


def test_delete_fsm_row_409():
    client, store, _redis = _client()
    mid = store.seed_meeting(user_id=USER, platform="google_meet",
                             native_meeting_id="xxx-xxxx-xxx", status="active")
    assert client.delete(f"/meetings/{mid}", headers=H).status_code == 409


def test_delete_owner_scoped_404():
    client, _store, _redis = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    assert client.delete(f"/meetings/{mid}", headers={"x-user-id": "999"}).status_code == 404


# ---- sharing: workspace members see the planned row ----------------------------------

def test_workspace_member_sees_planned_meeting_as_shared():
    client, _store, _redis = _client()
    client.post("/meetings", json={
        "title": "Client prep", "scheduled_at": AT, "workspace_id": "ws-42",
    }, headers=H)
    # another user, member of ws-42 (gateway injects x-user-workspaces)
    r = client.get("/meetings", headers={"x-user-id": "99", "x-user-workspaces": "ws-42"})
    rows = r.json()["meetings"]
    assert len(rows) == 1
    assert rows[0]["data"]["title"] == "Client prep"
    assert rows[0]["shared"] is True


def test_non_member_does_not_see_planned_meeting():
    client, _store, _redis = _client()
    client.post("/meetings", json={"title": "secret", "workspace_id": "ws-42"}, headers=H)
    r = client.get("/meetings", headers={"x-user-id": "99", "x-user-workspaces": "ws-other"})
    assert r.json()["meetings"] == []


# ---- link parser --------------------------------------------------------------------

def test_parse_meeting_url_formats():
    assert parse_meeting_url("https://meet.google.com/abc-defg-hij") == ("google_meet", "abc-defg-hij")
    assert parse_meeting_url("abc-defg-hij") == ("google_meet", "abc-defg-hij")
    assert parse_meeting_url("https://us02web.zoom.us/j/1234567890?pwd=x") == ("zoom", "1234567890")
    assert parse_meeting_url("1234567890") == ("zoom", "1234567890")
    assert parse_meeting_url(
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_YWJj%40thread.v2/0"
    ) == ("teams", "19:meeting_YWJj@thread.v2")
    assert parse_meeting_url("https://teams.microsoft.com/meet/9351274713?p=abc") == ("teams", "9351274713")
    assert parse_meeting_url("https://example.com/whatever") is None
    assert parse_meeting_url("") is None


def test_find_meeting_link_in_free_text():
    text = "Agenda attached.\nJoin: https://meet.google.com/abc-defg-hij\nBring coffee."
    assert find_meeting_link(text) == ("google_meet", "abc-defg-hij",
                                       "https://meet.google.com/abc-defg-hij")
    assert find_meeting_link("no links here") is None


# ---- a ROOM's planned call (rooms, F1) ---------------------------------------------------------

def test_a_rooms_planned_call_records_room_agenda_invitees_and_exports_ics(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms")
    client, store, _redis = _client()
    r = client.post("/meetings", json={
        "title": "Install sync", "scheduled_at": AT, "auto_join": False, "workspace_id": "ws-1",
        "room": "s-room1", "agenda": "kg/entities/meeting/2026-07-10-install-sync.md",
        "end_at": "2026-07-10T15:45:00Z", "attendees": ["Ana@Example.com", "bo@example.com"]}, headers=H)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["status"] == "scheduled" and row["data"]["auto_join"] is False      # no bot, a room call
    assert row["data"]["room"] == "s-room1" and row["data"]["agenda"].endswith("install-sync.md")
    assert row["data"]["attendees"] == ["ana@example.com", "bo@example.com"]
    ics = client.get(f"/meetings/{row['id']}/ics", headers=H)
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    text = ics.text
    assert "DTSTART:20260710T150000Z" in text and "DTEND:20260710T154500Z" in text
    assert "ATTENDEE;RSVP=TRUE:mailto:ana@example.com" in text and "SUMMARY:Install sync" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    # the calendar event made by the creator's own click is recorded as the source of truth
    p = client.patch(f"/meetings/{row['id']}", json={"calendar_event_ref": "gcal:evt-123"}, headers=H)
    assert p.status_code == 200 and p.json()["data"]["calendar_event_ref"] == "gcal:evt-123"
    assert client.get(f"/meetings/{row['id']}/ics", headers={"x-user-id": "8"}).status_code == 404


def test_room_fields_are_refused_while_rooms_is_off_even_under_all(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all")
    client, _store, _redis = _client()
    r = client.post("/meetings", json={"title": "x", "scheduled_at": AT, "room": "s-room1"}, headers=H)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "feature_disabled"
    ok = client.post("/meetings", json={"title": "x", "scheduled_at": AT}, headers=H)
    assert ok.status_code == 201 and "room" not in ok.json()["data"]                # nothing else changes
    assert client.get(f"/meetings/{ok.json()['id']}/ics", headers=H).status_code == 403


def test_agent_api_switches_room_plans_on_over_the_internal_tier(monkeypatch):
    """agent-api decides `rooms` (its features layer and the company's POLICIES); meeting-api takes
    its word on the internal tier (`X-User-Features`, an x-user-* header the identity guard keeps
    only from the internal tier) and needs no switch of its own."""
    monkeypatch.setenv("VEXA_FEATURES", "")
    client, _store, _redis = _client()
    r = client.post("/meetings", json={"title": "x", "scheduled_at": AT, "room": "s-room1"},
                    headers={**H, "X-User-Features": "rooms"})
    assert r.status_code == 201 and r.json()["data"]["room"] == "s-room1"
    assert client.get(f"/meetings/{r.json()['id']}/ics", headers={**H, "X-User-Features": "rooms"}).status_code == 200
    assert client.post("/meetings", json={"title": "y", "scheduled_at": AT, "room": "s-room1"},
                       headers=H).status_code == 403


# ---- sync back: the creator's calendar is the source of truth (rooms, PLAN-8H 8.4) ---------------

def _room_call(client, ref="evt123abc"):
    r = client.post("/meetings", json={
        "title": "Install sync", "scheduled_at": AT, "auto_join": False, "room": "s-room1",
        "end_at": "2026-07-10T15:45:00Z", "attendees": ["ana@example.com"]}, headers=H)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    client.patch(f"/meetings/{mid}", json={"calendar_event_ref": ref}, headers=H)
    return mid


def _row(client, mid, headers=H):
    return next(m for m in client.get("/meetings", headers=headers).json()["meetings"] if m["id"] == mid)


def test_a_time_moved_in_the_calendar_moves_the_planned_call(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms")
    client, _store, _redis = _client()
    mid = _room_call(client)
    r = client.post("/meetings/calendar-sync", headers=H, json={
        "event_id": "evt123abc", "status": "updated",
        "start": "2026-07-11T09:00:00Z", "end": "2026-07-11T09:30:00Z"})
    assert r.status_code == 200 and r.json()["synced"] == [mid]
    row = _row(client, mid)
    assert row["data"]["scheduled_at"] == "2026-07-11T09:00:00Z" and row["data"]["end_at"] == "2026-07-11T09:30:00Z"
    assert row["status"] == "scheduled" and not row["data"].get("cancelled_at")


def test_a_cancel_in_the_calendar_marks_the_planned_call_cancelled_and_keeps_it(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms")
    client, _store, _redis = _client()
    mid = _room_call(client)
    r = client.post("/meetings/calendar-sync", headers=H, json={"event_id": "evt123abc", "status": "cancelled"})
    assert r.status_code == 200 and r.json()["synced"] == [mid]
    # what changed, for the room to say: the room, the title and the change
    assert r.json()["changes"] == [{"meeting_id": mid, "room": "s-room1", "title": "Install sync", "status": "cancelled"}]
    row = _row(client, mid)
    assert row["data"]["cancelled_at"] and row["data"]["scheduled_at"] == AT     # kept, marked


def test_sync_touches_only_the_owners_room_rows_with_that_event(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,rooms")
    client, _store, _redis = _client()
    mine = _room_call(client, ref="evtmineaaa")
    plain = client.post("/meetings", json={"title": "not a room", "scheduled_at": AT}, headers=H).json()["id"]
    other_user = {"x-user-id": "8"}
    assert client.post("/meetings/calendar-sync", headers=other_user,
                       json={"event_id": "evtmineaaa", "status": "cancelled"}).json()["synced"] == []
    assert client.post("/meetings/calendar-sync", headers=H,
                       json={"event_id": "evtunknown", "status": "cancelled"}).json()["synced"] == []
    assert not _row(client, mine)["data"].get("cancelled_at") and not _row(client, plain)["data"].get("cancelled_at")


def test_calendar_sync_is_refused_while_rooms_is_off(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all")
    client, _store, _redis = _client()
    r = client.post("/meetings/calendar-sync", headers=H, json={"event_id": "evt123abc", "status": "cancelled"})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "feature_disabled"


def test_the_ics_file_carries_only_well_formed_url_and_attendee_lines():
    """A stored link or attendee with line breaks or separators is left out of the calendar file:
    every property line is one the row's own fields can produce, never one a value adds."""
    from datetime import datetime, timezone

    from meeting_api.collector import room_plan
    row = {"id": 9, "data": {
        "scheduled_at": "2026-10-20T15:00:00Z", "title": "Plan",
        "constructed_meeting_url": ("https://zoom.us/j/1234567890\r\nATTENDEE;RSVP=TRUE:mailto:x@y.z"
                                    "\r\nORGANIZER:mailto:o@y.z"),
        "attendees": ["ok@example.com", {"email": "a@b.c\r\nORGANIZER:mailto:o@y.z"}, "bad value@x.y"]}}
    text = room_plan.ics(row, now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    lines = text.split("\r\n")
    assert not any(line.startswith("ORGANIZER") for line in lines)
    assert [line for line in lines if line.startswith("ATTENDEE")] == ["ATTENDEE;RSVP=TRUE:mailto:ok@example.com"]
    assert not any(line.startswith("URL:") for line in lines)
    ok = room_plan.ics({"id": 9, "data": {"scheduled_at": "2026-10-20T15:00:00Z",
                                          "constructed_meeting_url": "https://zoom.us/j/1234567890"}})
    assert "URL:https://zoom.us/j/1234567890" in ok.split("\r\n")
