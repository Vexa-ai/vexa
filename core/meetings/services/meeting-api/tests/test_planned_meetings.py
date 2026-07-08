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
