"""A routine that needs the person's mail or calendar says so when it is saved (`routine_needs`).

A scheduled run has nobody in the loop, and every mail, calendar and connection verb refuses that
run with `human_session_required` (remedy `ask_in_chat`). The routine is still saved; its card and
the create answer carry `needs_person` and one `warning` sentence, so the person learns before the
first run is refused rather than never.
"""
from __future__ import annotations

from control_plane import routine_needs
from control_plane import workspace_routines as wr
from tests.test_routines import _FakeScheduler, _app, _write


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


def test_the_create_route_saves_and_warns_never_refuses():
    scheduler = _FakeScheduler()
    client, _ = _app(scheduler)
    r = client.post("/api/routines", json={"name": "Inbox watch", "cron": "*/10 * * * *",
                                           "prompt": "Check my email and flag anything urgent.",
                                           "run_now": False},
                    headers={"X-User-Id": "u_jane"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(scheduler.jobs) == 1, "a routine that needs a person is still saved"
    assert body["needs_person"] == ["mail"]
    assert "Ask in chat" in body["warning"]

    plain = client.post("/api/routines", json={"name": "Brief", "cron": "0 9 * * *",
                                               "prompt": "Write the brief.", "run_now": False},
                        headers={"X-User-Id": "u_jane"}).json()
    assert plain["needs_person"] == [] and "warning" not in plain


def test_the_list_route_marks_workspace_and_api_routines(tmp_path):
    root = tmp_path / "workspaces"
    _write(root / "u_jane" / "routines" / "inbox.md",
           "---\nenabled: true\ncron: '*/10 * * * *'\nprompt: Check my inbox.\n---\n")
    _write(root / "u_jane" / "routines" / "brief.md",
           "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Write the brief.\n---\n")
    scheduler = _FakeScheduler()
    wr.reconcile_workspace_routines("u_jane", scheduler=scheduler, invocations_url="http://x/invocations",
                                    workspaces_dir=root)
    cards = {c["name"]: c for c in wr.routine_cards_for_subject("u_jane", jobs=scheduler.list_jobs(),
                                                                 workspaces_dir=root)}
    assert cards["inbox"]["needs_person"] == ["mail"]
    assert "refused" in cards["inbox"]["warning"]
    assert cards["brief"]["needs_person"] == [] and "warning" not in cards["brief"]
