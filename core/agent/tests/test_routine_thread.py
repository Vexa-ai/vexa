"""ADR-0046 decision 12 — a routine's agent is a long-lived chat in the person's list.

flows posts each run of a person's routine into ONE chat thread over the internal tier. agent-api
runs it `scheduled` (the `autonomous` regime) on the thread's sibling unit, so a run and the
person's own replies share one conversation — the per-session history — without ever sharing a
worker, and one thread never runs two turns at once."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane.api import create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.routers import chats as chats_mod  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared import delegation, units  # noqa: E402
from shared.config import load_settings  # noqa: E402
from shared.marks import act_label  # noqa: E402

THREAD = "flow-0123456789abcdef"
ROUTINE = {"X-User-Id": "u1", "X-Internal-Secret": "s", "X-Vexa-Flow": "user:u1/brief",
           "X-Vexa-Flow-Step": "agent_run", "X-Vexa-Flow-Regime": "autonomous",
           "X-Vexa-Flow-Name": "brief", "X-Vexa-Flow-Scope": "user:u1", "X-Vexa-Flow-Version": "2"}
PERSON = {"X-User-Id": "u1"}


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


@pytest.fixture
def busy(monkeypatch):
    """Which units read as mid-turn — the one seam the busy check reads."""
    held: set = set()
    monkeypatch.setattr(chats_mod, "unit_busy", lambda url, unit, key: unit in held)
    return held


@pytest.fixture
def client(tmp_path, monkeypatch, busy):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test",
                             redis_url="")
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)))
    return TestClient(app)


def _dispatched(client):
    return client.app.state.dispatcher.dispatched


def _run(client, prompt="brief me"):
    return client.post("/api/chat/submit", headers=ROUTINE,
                       json={"prompt": prompt, "session": THREAD})


def test_a_routine_run_is_a_scheduled_turn_in_the_routines_thread(client):
    r = _run(client)
    assert r.status_code == 200, r.text
    inv = _dispatched(client)[-1]
    assert inv["trigger"] == "scheduled"
    assert delegation.regime_for_trigger(inv["trigger"]) == "autonomous"
    assert units.chat_session(inv) == THREAD
    assert units.dispatch_id(inv) == units.routine_unit_id("u1", THREAD)
    assert act_label(inv["start"]["entrypoint"]["inline"]) == "Routine run", (
        "a run never renders as the person's words")


def test_two_runs_and_the_persons_reply_are_one_conversation(client):
    """Continuity is the session: both runs and the reply between them address the same thread, so
    the second run reads the first and the reply — the same history a chat has."""
    _run(client, "first")
    client.post("/api/chat/submit", headers=PERSON,
                json={"prompt": "also check the budget", "session": THREAD})
    _run(client, "second")
    run1, reply, run2 = _dispatched(client)[-3:]
    assert {units.chat_session(x) for x in (run1, reply, run2)} == {THREAD}
    assert reply["trigger"] == "message", "the person's reply is an ordinary human turn"
    assert units.dispatch_id(reply) == units.chat_unit_id("u1", THREAD)
    assert units.dispatch_id(run1) == units.dispatch_id(run2) == units.routine_unit_id("u1", THREAD)


def test_the_thread_is_in_the_rail_named_by_its_flow_and_marked_a_routine(client):
    _run(client)
    rows = {r["session"]: r for r in client.get("/api/sessions", headers=PERSON).json()["sessions"]}
    row = rows[THREAD]
    assert row["routine"] == {"flow": "brief", "scope": "user:u1", "version": 2}
    assert row["title"] == "brief"
    assert row["touched"] is False, "nobody typed here yet"


def test_only_the_internal_tier_may_post_a_routine_turn(client):
    r = client.post("/api/chat/submit",
                    headers={k: v for k, v in ROUTINE.items() if k != "X-Internal-Secret"},
                    json={"prompt": "x", "session": THREAD})
    assert r.status_code == 403
    assert r.json()["detail"]["refused"] == "flow_turn_needs_internal_tier"
    assert _dispatched(client) == []


def test_a_run_waits_while_the_persons_turn_is_running(client, busy):
    busy.add(units.chat_unit_id("u1", THREAD))
    r = _run(client)
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "thread_busy"
    assert _dispatched(client) == []


def test_the_persons_turn_waits_while_a_run_is_running(client, busy):
    _run(client)
    busy.add(units.routine_unit_id("u1", THREAD))
    r = client.post("/api/chat/submit", headers=PERSON, json={"prompt": "hi", "session": THREAD})
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "thread_busy"


def test_an_ordinary_chat_is_never_held_by_the_routine_check(client, busy):
    busy.add(units.routine_unit_id("u1", "main"))
    r = client.post("/api/chat/submit", headers=PERSON, json={"prompt": "hi", "session": "main"})
    assert r.status_code == 200


def test_pending_reports_the_thread_busy_across_both_halves(client, busy):
    busy.add(units.routine_unit_id("u1", THREAD))
    r = client.get("/api/chat/pending", params={"session": THREAD}, headers=PERSON)
    assert r.status_code == 200 and r.json()["busy"] is True


def test_a_routine_run_carries_the_grants_flows_sent_and_a_person_cannot_add_any(client, monkeypatch):
    """Flows phase 0: the grants ride the routine's turn from flows (internal tier) into the mint;
    the same header on a person's own turn grants nothing."""
    seen = []
    orig = Dispatcher.dispatch

    def spy(self, inv, **kw):
        seen.append(kw.get("routine_grants"))
        return orig(self, inv, **kw)
    monkeypatch.setattr(Dispatcher, "dispatch", spy)
    r = client.post("/api/chat/submit", headers={**ROUTINE, "X-Vexa-Flow-Grants": "bot.request"},
                    json={"prompt": "send the bot", "session": THREAD})
    assert r.status_code == 200, r.text
    assert seen[-1] == ("user:u1/brief", ["bot.request"])
    client.post("/api/chat/submit", headers={**PERSON, "X-Vexa-Flow-Grants": "bot.request"},
                json={"prompt": "hi", "session": THREAD})
    assert seen[-1] is None
