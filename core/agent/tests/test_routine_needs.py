"""A routine that needs the person's mail or calendar says so when it is saved (`routine_needs`).

A scheduled run has nobody in the loop, and every mail, calendar and connection verb refuses that
run with `human_session_required` (remedy `ask_in_chat`). The routine is still saved; its card and
the create answer carry `needs_person` and one `warning` sentence, so the person learns before the
first run is refused rather than never.
"""
from __future__ import annotations

from control_plane import routine_needs
from tests.test_routine_flows import _as, world, flows  # noqa: F401 — fixtures


def test_a_prompt_that_reads_mail_or_the_calendar_needs_a_person():
    assert routine_needs.needs_person("Check my email every 10 minutes") == ["mail"]
    assert routine_needs.needs_person("Summarise my Gmail inbox") == ["mail"]
    assert routine_needs.needs_person("List tomorrow's calendar and meeting invites") == ["calendar"]
    assert routine_needs.needs_person("Read my mail, then my calendar") == ["mail", "calendar"]


def test_naming_a_person_only_tool_is_enough():
    """The tool half is read from the manifests: a route flagged `person` under Connections."""
    tools = routine_needs.person_tools()
    assert tools.get("mail_inbox") == "mail"
    assert tools.get("calendar_events") == "calendar"
    assert tools.get("secret_service_call") == "connections"
    assert "connections_status" not in tools, "a read of connection metadata needs nobody"
    assert routine_needs.needs_person("call secret_service_call with the weekly report") == ["connections"]


def test_the_detector_is_conservative():
    """Work that needs nobody is never flagged — a false warning teaches people to ignore it."""
    for prompt in ("Write the weekly brief from my workspace notes",
                   "Invite nobody; tidy the meeting notes folder",
                   "Summarise yesterday's meetings into the desk", ""):
        assert routine_needs.needs_person(prompt) == [], prompt


def test_the_create_route_saves_and_warns_never_refuses(world, flows):  # noqa: F811
    client, _, _ = world
    r = client.post("/api/routines", json={"name": "Inbox watch", "cron": "*/15 * * * *",
                                           "prompt": "Check my email and flag anything urgent.",
                                           "run_now": False}, headers=_as())
    assert r.status_code == 201, r.text
    body = r.json()
    assert any(f["name"] == "Inbox-watch" for f in flows.rows), "still saved, as a flow"
    assert body["needs_person"] == ["mail"]
    assert "Ask in chat" in body["warning"]
    plain = client.post("/api/routines", json={"name": "Brief", "cron": "0 9 * * *",
                                               "prompt": "Write the brief.", "run_now": False},
                        headers=_as()).json()
    assert plain["needs_person"] == [] and "warning" not in plain
    cards = {c["name"]: c for c in client.get("/api/routines", headers=_as()).json()["routines"]}
    assert cards["Inbox-watch"]["needs_person"] == ["mail"]
    assert cards["Brief"]["needs_person"] == []


def test_a_routine_made_in_a_chat_runs_with_that_chats_scope(world, flows):  # noqa: F811
    """Founder 2026-10-15: a routine made in a chat that works in a shared workspace ran with
    Personal as its target and every private workspace readable. It inherits the chat's scope."""
    from control_plane import identity_token, routine_flows as rf, workspace_attach as attach
    client, _, root = world
    a = attach.create_workspace(root, "7", name="FINOS research").slug
    me = _as()
    assert client.post("/api/chat/target", json={"session": "pchat-r", "workspace": a}, headers=me).status_code == 200
    claims = {"sub": "7", "email": "ada@example.com",
              "delegation": {"regime": "human", "workspaces": "*", "unit": "agent-7-chat-pchat-r-g1"}}
    from tests.test_routine_flows import KEY
    turn = {identity_token.HEADER: identity_token.sign(KEY, claims)}
    r = client.post("/api/routines", json={"name": "Research loop", "cron": "17 * * * *",
                                           "prompt": "One research iteration.", "run_now": False}, headers=turn)
    assert r.status_code == 201, r.text
    thread = rf.thread_session(r.json()["flow"]["scope"], r.json()["flow"]["name"])
    v = client.get(f"/api/workspace/states?session={thread}", headers=me).json()
    assert v["chat"]["target"] == a
