"""ADR-0046 decision 13 over ADR-0048 — an agent's address is a SESSION in the session store.

A thread `message_agent` names may be a person's own chat or a stored session they take part in
(`s-…`, a chat another person added them to); a routine's thread is known as one by its flow
binding in the store. L2: FastAPI over a sqlite session store, flows stubbed at the publish seam."""
from __future__ import annotations

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
    mindex.add("alice", "ws-team", "contributor", "t")
    mindex.add("bob", "ws-team", "contributor", "t")
    client = TestClient(create_app(Dispatcher(settings, _Runtime(), _Identity()),
                                   stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                                   sessions=_Sessions(), session_store=store,
                                   membership_index=mindex))
    published: list = []
    monkeypatch.setattr(am, "_publish", lambda sid, refs: (published.append((sid, refs)) or
                                                           (202, {"reactions_created": 1})))
    yield client, store, published
    store.close()


def _send(client, uid, **body):
    return client.post("/api/agents/message", headers={"X-User-Id": uid}, json=body)


def _open(client, uid, session):
    assert client.post("/api/chat/submit", headers={"X-User-Id": uid},
                       json={"prompt": "hello", "session": session}).status_code == 200


def test_a_shared_session_is_an_address_for_each_participant(world):
    client, store, published = world
    _open(client, "alice", "pchat-a")
    shared = store.create("bob", name="pchat-team", title="team")
    store.add_participant(shared["id"], "bob", "alice", role="member")
    r = _send(client, "alice", thread=shared["id"], person="bob", text="hi", from_thread="pchat-a")
    assert r.status_code == 202, r.text
    assert published[-1][1]["thread"] == shared["id"]


def test_a_session_the_caller_is_not_in_is_not_theirs_to_write_from(world):
    client, store, _ = world
    _open(client, "alice", "pchat-a")
    private = store.create("bob", name="pchat-private")
    r = _send(client, "alice", thread="pchat-a", text="hi", from_thread=private["id"])
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_your_thread"
    r = _send(client, "alice", thread="s-0000", person="bob", text="hi", from_thread="pchat-a")
    assert r.status_code == 403 and r.json()["detail"] == am.CROSS_PERSON_REFUSAL


def test_a_store_bound_thread_sends_as_a_routine(world):
    """The flow binding in the store is what makes a thread a routine's — so a routine still sends
    as a routine (and `agent_messages_from_routines` still applies) if the index row lost its mark."""
    client, store, published = world
    _open(client, "u1", "pchat-b")
    _open(client, "u1", "flow-abc")
    store.update(store.by_name("u1", "flow-abc")["id"], flow=flow_ref("user:u1", "brief"))
    _send(client, "u1", thread="pchat-b", text="x", from_thread="flow-abc")
    assert published[-1][1]["sender_routine"] is True
    assert published[-1][1]["sender_label"] == "routine brief"
