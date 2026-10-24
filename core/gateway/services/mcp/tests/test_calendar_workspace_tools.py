"""calendar_status · calendar_connect · set_auto_join · meeting_bind_workspace — compositions of
public routes, each forwarding the caller's own key to the gateway, and their MCP annotations."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import API_KEY, GATEWAY_URL, FakeGateway, meetings_assembly
from vexa_mcp import create_app


def _soon(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _row(mid, *, hours=2.0, auto_join=None, native="abc-defg-hij", pinned=False, **data):
    d = {"title": f"Meeting {mid}", "scheduled_at": _soon(hours), "calendar_connection_id": "cal-1", **data}
    if auto_join is not None:
        d["auto_join"] = auto_join
    if pinned:
        d["auto_join_user_set"] = True
    return {"id": mid, "platform": "google_meet" if native else "unknown", "native_meeting_id": native,
            "status": "scheduled", "data": d}


CALENDARS = {"calendars": [{"id": "cal-1", "name": "Work", "enabled": True, "auto_join": True, "bot_name": "Vexa",
                            "ics_url_set": True, "ics_url_masked": "calendar.google.com/…f.ics"}]}


def _app(gateway: FakeGateway, agent: bool = False) -> TestClient:
    asm = meetings_assembly()
    if agent:
        asm["assembly_env"] = {**asm["assembly_env"], "AGENT_API_URL": "http://agent-down.test"}
    return TestClient(create_app(GATEWAY_URL, transport=httpx.MockTransport(gateway.handler), **asm))


H = {"Authorization": f"Bearer {API_KEY}"}


def _hops(gateway):
    return [(r.method, r.url.path) for r in gateway.requests]


def test_calendar_status_lists_calendars_and_who_the_bot_will_join(gateway):
    gateway.routes[("GET", "/user/calendars")] = (200, CALENDARS)
    gateway.routes[("GET", "/user/calendar/sync")] = (200, {"last_sync": "2026-10-24T10:00:00Z", "last_error": None})
    gateway.routes[("GET", "/meetings")] = (200, {"meetings": [
        _row(1, hours=3), _row(2, hours=1, auto_join=False, pinned=True), _row(3, hours=5, native=None),
        _row(4, hours=24 * 30)]})            # beyond the window
    r = _app(gateway).get("/calendar-status", headers=H, params={"days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["calendars"][0] == CALENDARS["calendars"][0]
    assert body["sync"]["last_sync"]
    assert [m["meeting_db_id"] for m in body["upcoming"]] == [2, 1, 3]
    by = {m["meeting_db_id"]: m for m in body["upcoming"]}
    assert by[1]["will_auto_join"] is True and by[1]["why_not"] is None
    assert by[2]["will_auto_join"] is False and by[2]["pinned"] is True and "off" in by[2]["why_not"]
    assert by[3]["will_auto_join"] is False and "link" in by[3]["why_not"]
    assert body["will_auto_join"] == 1
    meetings_hop = next(q for q in gateway.requests if q.url.path == "/meetings")
    assert meetings_hop.url.params["status"] == "scheduled"
    assert all(q.headers["x-api-key"] == API_KEY for q in gateway.requests)


def test_calendar_status_without_tx_still_shows_the_calendars(gateway):
    gateway.routes[("GET", "/user/calendars")] = (200, CALENDARS)
    gateway.routes[("GET", "/meetings")] = (403, {"detail": "Insufficient scope for this endpoint"})
    body = _app(gateway).get("/calendar-status", headers=H).json()
    assert body["calendars"] and body["upcoming"] is None and "tx" in body["upcoming_unavailable"]


def test_calendar_connect_hands_back_the_deployment_page_and_takes_no_credential(gateway, monkeypatch):
    monkeypatch.setenv("VEXA_CALENDAR_CONNECT_URL", "https://app.example.com/settings/calendar")
    c = _app(gateway)
    body = c.get("/calendar-connect", headers=H).json()
    assert body["url"] == "https://app.example.com/settings/calendar" and body["steps"]
    assert gateway.requests == []                                     # no hop at all
    assert c.get("/calendar-connect", headers=H, params={"ics_url": "https://x/y.ics"}).status_code == 422
    tool = next(t for t in c.app.state.mcp.tools if t.name == "calendar_connect")
    assert not (tool.inputSchema.get("properties") or {})


def test_calendar_connect_without_a_configured_page_says_where_to_go(gateway, monkeypatch):
    monkeypatch.delenv("VEXA_CALENDAR_CONNECT_URL", raising=False)
    body = _app(gateway).get("/calendar-connect", headers=H).json()
    assert body["url"] is None and "settings" in body["note"].lower()


def test_set_auto_join_on_one_meeting_patches_that_row(gateway):
    gateway.routes[("PATCH", "/meetings/7")] = (200, _row(7, auto_join=False, pinned=True))
    r = _app(gateway).post("/auto-join", headers=H, json={"enabled": False, "meeting_db_id": 7})
    assert r.status_code == 200, r.text
    assert _hops(gateway) == [("PATCH", "/meetings/7")]
    assert gateway.last_json() == {"auto_join": False}
    assert r.json()["meeting"]["auto_join"] is False and r.json()["meeting"]["pinned"] is True


def test_set_auto_join_for_all_calendars_sets_each_policy_and_syncs(gateway):
    gateway.routes[("GET", "/user/calendars")] = (200, {"calendars": [{"id": "a"}, {"id": "b"}]})
    r = _app(gateway).post("/auto-join", headers=H, json={"enabled": True, "all_calendars": True})
    assert r.status_code == 200, r.text
    assert _hops(gateway) == [("GET", "/user/calendars"), ("PATCH", "/user/calendars/a"),
                              ("POST", "/user/calendars/a/sync"), ("PATCH", "/user/calendars/b"),
                              ("POST", "/user/calendars/b/sync")]
    assert [c["id"] for c in r.json()["calendars"]] == ["a", "b"]


@pytest.mark.parametrize("body", [{"enabled": True}, {"enabled": True, "meeting_db_id": 1, "calendar_id": "a"}])
def test_set_auto_join_needs_exactly_one_target(gateway, body):
    r = _app(gateway).post("/auto-join", headers=H, json=body)
    assert r.status_code == 422 and gateway.requests == []


def test_a_refused_planned_change_reaches_the_agent(gateway):
    gateway.routes[("PATCH", "/meetings/9")] = (409, {"detail": "Meeting is no longer planned (bot lifecycle owns it)"})
    r = _app(gateway).post("/auto-join", headers=H, json={"enabled": True, "meeting_db_id": 9})
    assert r.status_code == 409 and "no longer planned" in r.text


def test_meeting_bind_workspace_is_absent_without_agents(gateway):
    names = {t.name for t in _app(gateway).app.state.mcp.tools}
    assert "meeting_bind_workspace" not in names and "calendar_status" in names


LISTING = {"all": [{"slug": "u2", "name": "Personal", "kind": "personal", "role": "owner"},
                   {"slug": "ws-team", "name": "Team", "kind": "shared", "role": "contributor"}]}


def test_bind_to_a_shared_workspace_creates_the_page_attaches_it_and_shares_the_meeting(gateway):
    gateway.routes[("GET", "/meetings/5")] = (200, {"id": 5, "platform": "google_meet", "native_meeting_id": "abc",
                                                    "data": {"title": "Acme sync", "scheduled_at": "2026-10-30T10:00:00Z"}})
    gateway.routes[("GET", "/agent/workspace/shared")] = (200, LISTING)
    gateway.routes[("GET", "/agent/workspace/file")] = (404, {"detail": "not found"})
    gateway.routes[("POST", "/agent/meeting/note/attach")] = (200, {"path": "x", "workspace": "ws-team", "recorded": True})
    r = _app(gateway, agent=True).post("/meeting-workspace", headers=H, json={"meeting_db_id": 5, "workspace": "Team"})
    assert r.status_code == 200, r.text
    assert _hops(gateway) == [("GET", "/meetings/5"), ("GET", "/agent/workspace/shared"), ("PATCH", "/meetings/5"),
                              ("GET", "/agent/workspace/file"), ("PUT", "/agent/workspace/file"),
                              ("POST", "/agent/meeting/note/attach")]
    put = json.loads(gateway.requests[4].content)
    assert put["path"] == "kg/entities/meeting/2026-10-30-acme-sync.md" and put["slug"] == "ws-team"
    assert json.loads(gateway.requests[2].content) == {"workspace_id": "ws-team"}
    attach = gateway.last_json()
    assert attach == {"path": put["path"], "workspace": "ws-team", "meeting_id": "5", "replace": False}
    body = r.json()
    assert body["shared_with_members"] is True and body["page"]["created"] is True and body["page"]["recorded"] is True


def test_bind_a_live_meeting_falls_back_to_the_native_route(gateway):
    gateway.routes[("GET", "/meetings/6")] = (200, {"id": 6, "platform": "zoom", "native_meeting_id": "123",
                                                    "status": "active", "data": {"title": "Live"}})
    gateway.routes[("GET", "/agent/workspace/shared")] = (200, LISTING)
    gateway.routes[("PATCH", "/meetings/6")] = (409, {"detail": "Meeting is no longer planned"})
    r = _app(gateway, agent=True).post("/meeting-workspace", headers=H, json={"meeting_db_id": 6, "workspace": "ws-team"})
    assert r.status_code == 200, r.text
    assert ("POST", "/meetings/zoom/123/workspace") in _hops(gateway)


def test_bind_to_personal_does_not_share_and_an_unknown_workspace_is_named(gateway):
    gateway.routes[("GET", "/meetings/5")] = (200, {"id": 5, "platform": "google_meet", "native_meeting_id": "abc", "data": {}})
    gateway.routes[("GET", "/agent/workspace/shared")] = (200, LISTING)
    c = _app(gateway, agent=True)
    r = c.post("/meeting-workspace", headers=H, json={"meeting_db_id": 5, "workspace": "personal"})
    assert r.status_code == 200, r.text
    assert r.json()["shared_with_members"] is False and ("PATCH", "/meetings/5") not in _hops(gateway)
    assert gateway.last_json()["workspace"] == "personal"
    r = c.post("/meeting-workspace", headers=H, json={"meeting_db_id": 5, "workspace": "Nope"})
    assert r.status_code == 404 and "ws-team" in r.text


def test_every_new_tool_is_annotated(gateway):
    tools = {t.name: t for t in _app(gateway, agent=True).app.state.mcp.tools}
    expect = {"calendar_status": (True, False), "calendar_connect": (True, False),
              "set_auto_join": (False, False), "meeting_bind_workspace": (False, False)}
    for name, (ro, destructive) in expect.items():
        a = tools[name].annotations
        assert a is not None and tools[name].title, name
        assert (a.readOnlyHint, a.destructiveHint) == (ro, destructive), name
        assert isinstance(a.idempotentHint, bool) and isinstance(a.openWorldHint, bool), name
    assert tools["set_auto_join"].annotations.openWorldHint is True
