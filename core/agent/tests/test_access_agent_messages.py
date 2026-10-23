"""Access vectors for `message_agent` across two people (ADR-0046 decision 13, 2026-10-13 rule).

Another person's agent is reached only in a stored conversation (`s-…`) that no flow owns, where the
receiver takes part and the sender takes part with write, inside a workspace both belong to where
the sender holds a writing role. Everything else is refused with ONE body, whether the thread is
absent or not allowed. A worker with nobody in the loop sends as a routine unless it is answering a
delivered message. L2: FastAPI over a sqlite session store, flows stubbed at the publish seam."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests._session_fakes import MemBlobs
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.routers import agent_messages as am
from control_plane.session_store import SessionStore, flow_ref
from control_plane.workspace_membership import InMemoryMembershipIndex
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


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


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    mindex = InMemoryMembershipIndex()
    client = TestClient(create_app(Dispatcher(settings, _Runtime(), _Identity()),
                                   stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                                   sessions=_Sessions(), session_store=store,
                                   membership_index=mindex))
    published: list = []
    monkeypatch.setattr(am, "_publish", lambda sid, refs: (published.append((sid, refs)) or
                                                           (202, {"reactions_created": 1})))
    _open(client, "alice", "pchat-a")
    _open(client, "bob", "main")
    yield client, store, mindex, published
    store.close()


def _open(client, uid, session):
    assert client.post("/api/chat/submit", headers={"X-User-Id": uid},
                       json={"prompt": "hello", "session": session}).status_code == 200


def _send(client, uid, headers=None, **body):
    return client.post("/api/agents/message", headers={"X-User-Id": uid, **(headers or {})},
                       json=body)


def _both_in(mindex, alice_role="contributor", bob_role="contributor", ws="ws-team"):
    mindex.add("alice", ws, alice_role, "t")
    mindex.add("bob", ws, bob_role, "t")


def _conversation(store, *, alice_role="member", flow=None):
    row = store.create("bob", name="pchat-team", title="team")
    if alice_role:
        store.add_participant(row["id"], "bob", "alice", role=alice_role)
    if flow:
        store.update(row["id"], flow=flow)
    return row["id"]


def _refused_uniformly(r):
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == am.CROSS_PERSON_REFUSAL


def test_the_allowed_case_is_delivered(world):
    client, store, mindex, published = world
    _both_in(mindex)
    sid = _conversation(store)
    r = _send(client, "alice", thread=sid, person="bob", text="hi", from_thread="pchat-a")
    assert r.status_code == 202, r.text
    assert published[-1][1]["thread"] == sid and published[-1][1]["shared"] is True


def test_a_reader_of_the_common_workspace_is_refused(world):
    client, store, mindex, published = world
    _both_in(mindex, alice_role="viewer")
    sid = _conversation(store)
    _refused_uniformly(_send(client, "alice", thread=sid, person="bob", text="hi",
                             from_thread="pchat-a"))
    assert published == []


def test_no_common_workspace_is_refused(world):
    client, store, mindex, published = world
    sid = _conversation(store)
    _refused_uniformly(_send(client, "alice", thread=sid, person="bob", text="hi",
                             from_thread="pchat-a"))
    assert published == []


def test_the_other_persons_main_is_refused(world):
    client, store, mindex, published = world
    _both_in(mindex)
    _refused_uniformly(_send(client, "alice", thread="main", person="bob", text="hi",
                             from_thread="pchat-a"))
    assert published == []


def test_the_other_persons_routine_thread_is_refused(world):
    client, store, mindex, published = world
    _both_in(mindex)
    _open(client, "bob", "flow-0123abcd")
    _refused_uniformly(_send(client, "alice", thread="flow-0123abcd", person="bob", text="hi",
                             from_thread="pchat-a"))
    # …and a stored session bound to a flow, even one alice takes part in
    sid = _conversation(store, flow=flow_ref("user:bob", "brief"))
    _refused_uniformly(_send(client, "alice", thread=sid, person="bob", text="hi",
                             from_thread="pchat-a"))
    assert published == []


def test_a_session_the_sender_is_not_in_is_refused(world):
    client, store, mindex, published = world
    _both_in(mindex)
    sid = _conversation(store, alice_role=None)
    _refused_uniformly(_send(client, "alice", thread=sid, person="bob", text="hi",
                             from_thread="pchat-a"))
    assert published == []


def test_a_session_the_sender_only_reads_is_refused(world):
    client, store, mindex, published = world
    _both_in(mindex)
    sid = _conversation(store, alice_role="viewer")
    _refused_uniformly(_send(client, "alice", thread=sid, person="bob", text="hi",
                             from_thread="pchat-a"))
    assert published == []


def test_absent_and_not_allowed_answer_the_same_body(world):
    client, store, mindex, _ = world
    _both_in(mindex)
    private = _conversation(store, alice_role=None)
    absent = _send(client, "alice", thread="s-doesnotexist", person="bob", text="hi",
                   from_thread="pchat-a")
    hidden = _send(client, "alice", thread=private, person="bob", text="hi", from_thread="pchat-a")
    named = _send(client, "alice", thread="no-such-name", person="bob", text="hi",
                  from_thread="pchat-a")
    assert absent.status_code == hidden.status_code == named.status_code == 403
    assert absent.json() == hidden.json() == named.json()


def test_flows_policy_refusal_reads_the_same_body(world, monkeypatch):
    client, store, mindex, _ = world
    _both_in(mindex)
    sid = _conversation(store)
    monkeypatch.setattr(am, "_publish", lambda s, refs: (403, {"refused": "agent_messages_shared",
                                                               "policy": "x"}))
    _refused_uniformly(_send(client, "alice", thread=sid, person="bob", text="hi",
                             from_thread="pchat-a"))


def test_same_person_delivery_keeps_working(world):
    client, store, mindex, published = world
    _open(client, "alice", "pchat-b")
    r = _send(client, "alice", thread="pchat-b", text="hi", from_thread="pchat-a")
    assert r.status_code == 202, r.text
    assert published[-1][1]["sender_routine"] is False


# ── who is sending (F2) ─────────────────────────────────────────────────────────────────────────

UNWATCHED = {"x-user-regime": "autonomous", "x-user-delegation-workspaces": "ws-team"}


def test_an_unattended_worker_naming_main_sends_as_a_routine(world):
    client, store, mindex, published = world
    _open(client, "alice", "main")
    _send(client, "alice", headers=UNWATCHED, thread="pchat-a", text="x", from_thread="main")
    assert published[-1][1]["sender_routine"] is True


def test_an_unattended_reply_to_a_delivered_message_is_not_a_routine_and_keeps_its_chain(world):
    client, store, mindex, published = world
    _open(client, "alice", "pchat-b")
    # deliver a message into pchat-b the way flows does, so it carries a chain
    hops = ["thread:alice/pchat-a"]
    r = client.post("/api/chat/submit", headers={
        "X-User-Id": "alice", "X-Internal-Secret": "s", "X-Vexa-Flow": "agent_messages",
        "X-Vexa-Flow-Step": "deliver_message", "X-Vexa-Flow-Regime": "autonomous",
        "X-Vexa-Message-From-Uid": "alice", "X-Vexa-Message-From-Thread": "pchat-a",
        "X-Vexa-Message-From-Label": "a", "X-Vexa-Message-Origin": json.dumps(hops)},
        json={"prompt": "asked", "session": "pchat-b"})
    assert r.status_code == 200, r.text
    _send(client, "alice", headers=UNWATCHED, thread="pchat-a", text="answer", from_thread="pchat-b")
    refs = published[-1][1]
    assert refs["sender_routine"] is False
    assert refs["_origin"] == hops + ["thread:alice/pchat-b"]


def test_a_person_in_the_loop_is_not_a_routine(world):
    client, store, mindex, published = world
    _open(client, "alice", "main")
    _send(client, "alice", headers={"x-user-regime": "human", "x-user-delegation-workspaces": "*"},
          thread="pchat-a", text="x", from_thread="main")
    assert published[-1][1]["sender_routine"] is False


# ── the delivery side holds the same rule ───────────────────────────────────────────────────────

def _deliver(client, *, sender, receiver, session):
    return client.post("/api/chat/submit", headers={
        "X-User-Id": receiver, "X-Internal-Secret": "s", "X-Vexa-Flow": "agent_messages",
        "X-Vexa-Flow-Step": "deliver_message", "X-Vexa-Flow-Regime": "autonomous",
        "X-Vexa-Message-From-Uid": sender, "X-Vexa-Message-From-Thread": "pchat-a",
        "X-Vexa-Message-From-Label": "a", "X-Vexa-Message-Origin": "[]"},
        json={"prompt": "hi", "session": session})


def test_a_delivery_into_another_persons_main_is_refused(world):
    client, store, mindex, _ = world
    r = _deliver(client, sender="alice", receiver="bob", session="main")
    _refused_uniformly(r)


def test_a_delivery_into_a_shared_conversation_starts(world):
    client, store, mindex, _ = world
    sid = _conversation(store)
    r = _deliver(client, sender="alice", receiver="bob", session=sid)
    assert r.status_code == 200, r.text
