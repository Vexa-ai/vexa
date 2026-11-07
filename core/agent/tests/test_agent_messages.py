"""ADR-0046 decision 13 — agents speak to each other, the agent-api half.

`message_agent` checks what only this domain knows — the sending thread is the caller's own, the
target thread exists, whether two people share a workspace — and publishes `thread.message`; flows
(stubbed here at the publish seam) applies the policy and the guards and delivers the text back
into the target thread through `/api/chat/submit`, which marks it as from the sending agent."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane.api import create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.routers import agent_messages as am  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared import units  # noqa: E402
from shared.config import load_settings  # noqa: E402
from shared.marks import act_label, read_message_mark  # noqa: E402


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


from control_plane.workspace_membership import InMemoryMembershipIndex  # noqa: E402

MINDEX = InMemoryMembershipIndex()


@pytest.fixture
def world(tmp_path, monkeypatch):
    MINDEX._by_subject.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test",
                             redis_url="")
    client = TestClient(create_app(Dispatcher(settings, _Runtime(), _Identity()),
                                   stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                                   membership_index=MINDEX))
    published: list = []
    monkeypatch.setattr(am, "_publish", lambda sid, refs: (published.append((sid, refs)) or
                                                           (202, {"reactions_created": 1})))
    return client, published


def _open(client, uid, session):
    r = client.post("/api/chat/submit", headers={"X-User-Id": uid},
                    json={"prompt": "hello", "session": session})
    assert r.status_code == 200, r.text


def _send(client, uid, **body):
    return client.post("/api/agents/message", headers={"X-User-Id": uid}, json=body)


def _deliver(client, refs):
    """What flows' `deliver_message` posts — the internal tier, the message headers."""
    import json
    return client.post("/api/chat/submit", headers={
        "X-User-Id": refs["uid"], "X-Internal-Secret": "s", "X-Vexa-Flow": "agent_messages",
        "X-Vexa-Flow-Step": "deliver_message", "X-Vexa-Flow-Regime": "autonomous",
        "X-Vexa-Message-From-Uid": refs["sender_uid"],
        "X-Vexa-Message-From-Thread": refs["sender_thread"],
        "X-Vexa-Message-From-Label": refs["sender_label"],
        "X-Vexa-Message-Origin": json.dumps(refs["_origin"])},
        json={"prompt": refs["text"], "session": refs["thread"]})


def test_a_round_trip_lands_in_both_threads_in_order_and_each_names_the_other(world):
    client, published = world
    _open(client, "u1", "pchat-a")
    _open(client, "u1", "pchat-b")
    assert _send(client, "u1", thread="pchat-b", text="A asks B",
                 from_thread="pchat-a").status_code == 202
    assert _deliver(client, published[-1][1]).status_code == 200
    assert _send(client, "u1", thread="pchat-a", text="B answers A",
                 from_thread="pchat-b").status_code == 202
    assert _deliver(client, published[-1][1]).status_code == 200
    turns = [inv for inv in client.app.state.dispatcher.dispatched
             if inv["trigger"] == "scheduled"]
    got = [(units.chat_session(inv), read_message_mark(inv["start"]["entrypoint"]["inline"]))
           for inv in turns]
    assert [(s, m["text"], m["thread"]) for s, m in got] == [
        ("pchat-b", "A asks B", "pchat-a"), ("pchat-a", "B answers A", "pchat-b")]
    # never the person's words: the record keeps the message, the reader names who sent it
    assert act_label(turns[0]["start"]["entrypoint"]["inline"]) == "A asks B"
    # the second hop carries the first: the conversation's chain grows by one per hop
    assert published[0][1]["_origin"] == ["thread:u1/pchat-a"]
    assert published[1][1]["_origin"] == ["thread:u1/pchat-a", "thread:u1/pchat-b"]


def test_the_sending_thread_must_be_the_callers_own(world):
    client, published = world
    _open(client, "u2", "theirs")
    _open(client, "u1", "pchat-b")
    r = _send(client, "u1", thread="pchat-b", text="x", from_thread="theirs")
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_thread"
    assert published == []


def test_a_thread_that_does_not_exist_is_refused(world):
    client, published = world
    _open(client, "u1", "pchat-a")
    r = _send(client, "u1", thread="nope", text="x", from_thread="pchat-a")
    assert r.status_code == 404 and r.json()["detail"]["refused"] == "no_such_thread"


def test_another_persons_own_thread_is_never_an_address(world):
    """Across two people only a stored conversation both take part in is an address (the store
    is absent here), so a thread of their own is refused — with or without a shared workspace."""
    client, published = world
    _open(client, "u1", "pchat-a")
    _open(client, "u2", "pchat-z")
    r = _send(client, "u1", thread="pchat-z", text="x", from_thread="pchat-a", person="u2")
    assert r.status_code == 403 and r.json()["detail"] == am.CROSS_PERSON_REFUSAL
    MINDEX.add("u1", "ws-1", "member", "t")
    MINDEX.add("u2", "ws-1", "member", "t")
    r = _send(client, "u1", thread="pchat-z", text="x", from_thread="pchat-a", person="u2")
    assert r.status_code == 403 and r.json()["detail"] == am.CROSS_PERSON_REFUSAL
    assert published == []


def test_a_routine_thread_sends_as_a_routine(world):
    client, published = world
    _open(client, "u1", "pchat-b")
    client.post("/api/chat/submit", headers={
        "X-User-Id": "u1", "X-Internal-Secret": "s", "X-Vexa-Flow": "user:u1/brief",
        "X-Vexa-Flow-Step": "agent_run", "X-Vexa-Flow-Regime": "autonomous",
        "X-Vexa-Flow-Name": "brief", "X-Vexa-Flow-Scope": "user:u1", "X-Vexa-Flow-Version": "1"},
        json={"prompt": "run", "session": "flow-abc"})
    _send(client, "u1", thread="pchat-b", text="x", from_thread="flow-abc")
    assert published[-1][1]["sender_routine"] is True
    assert published[-1][1]["sender_label"] == "routine brief"


def test_a_refusal_from_flows_reaches_the_agent_typed(world, monkeypatch):
    client, _ = world
    monkeypatch.setattr(am, "_publish", lambda sid, refs: (403, {"refused": "agent_messages_shared"}))
    _open(client, "u1", "pchat-a")
    _open(client, "u1", "pchat-b")
    r = _send(client, "u1", thread="pchat-b", text="x", from_thread="pchat-a")
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "agent_messages_shared"


def test_a_delivered_message_does_not_turn_a_chat_into_a_routine(world):
    client, published = world
    _open(client, "u1", "pchat-a")
    _open(client, "u1", "pchat-b")
    _send(client, "u1", thread="pchat-b", text="hi", from_thread="pchat-a")
    _deliver(client, published[-1][1])
    rows = {r["session"]: r for r in client.get("/api/sessions",
                                                headers={"X-User-Id": "u1"}).json()["sessions"]}
    assert rows["pchat-b"]["routine"] is None
