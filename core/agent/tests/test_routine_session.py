"""ADR-0046 decision 12 over ADR-0048 — a routine's thread is a SESSION in the session store, bound
to its flow.

flows posts each run of a person's routine into one chat thread. With conversations stored apart
from workspaces, that thread is a row in agent-api's session store: the run creates it, binds it to
the flow and titles it with the flow's name, and every later run — and every reply the person types
in between — is handed the store's head version, so a run inherits all that came before it.

L2: a real FastAPI app over a sqlite session store and fakes; no redis, no runtime, no model."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from control_plane import session_sync
from tests._session_fakes import MemBlobs
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.routers import chats as chats_mod
from control_plane.session_store import SessionStore, read_flow
from control_plane.workspace_reader import WorkspaceReader
from shared import units
from shared.config import load_settings

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
    held: set = set()
    monkeypatch.setattr(chats_mod, "unit_busy", lambda url, unit, key: unit in held)
    return held


@pytest.fixture
def env(tmp_path, monkeypatch, busy):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test",
                             redis_url="")
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     session_store=store)
    yield TestClient(app), store, dispatcher
    store.close()


def _run(client, prompt="brief me"):
    return client.post("/api/chat/submit", headers=ROUTINE, json={"prompt": prompt, "session": THREAD})


def _reply(client, prompt):
    return client.post("/api/chat/submit", headers=PERSON, json={"prompt": prompt, "session": THREAD})


def _hand_back(store, dispatcher, inv, text):
    """What the worker does at turn end: return the conversation it was handed, one turn longer."""
    bundle = dispatcher._session_bundle(inv)
    files = {k: base64.b64decode(v) for k, v in bundle["files"].items()}
    log = files.get("transcript.jsonl", b"") + text.encode() + b"\n"
    ev = {"session_id": bundle["id"], "base": bundle["version"],
          "files": {"transcript.jsonl": base64.b64encode(log).decode()}}
    # the unit is bound to what it was handed (S4): that session, that base, that speaker
    bound = {"session_id": bundle["id"], "base": bundle["version"],
             "subject": (inv.get("identity") or {}).get("subject") or ""}
    assert session_sync.save_event(store, ev, bound) is not None


def _handed(dispatcher, inv) -> list[str]:
    b = dispatcher._session_bundle(inv)
    raw = base64.b64decode(b["files"].get("transcript.jsonl", "")) if b["files"] else b""
    return [x for x in raw.decode().splitlines() if x]


def test_the_first_run_creates_a_session_bound_to_its_flow_titled_by_it(env):
    client, store, _ = env
    assert _run(client).status_code == 200
    row = store.by_name("u1", THREAD)
    assert row is not None, "the routine's thread is a session in the store"
    assert read_flow(row["flow"]) == {"scope": "user:u1", "flow": "brief"}
    assert row["title"] == "brief"
    assert [p["principal"] for p in store.participants(row["id"])] == ["u1"]


def test_two_runs_land_in_one_session_in_order_and_the_second_inherits_the_first(env):
    client, store, dispatcher = env
    _run(client, "first")
    run1 = dispatcher.dispatched[-1]
    _hand_back(store, dispatcher, run1, "run 1 said: the inbox is clear")
    _run(client, "second")
    run2 = dispatcher.dispatched[-1]
    assert units.dispatch_id(run1) == units.dispatch_id(run2)
    assert _handed(dispatcher, run2) == ["run 1 said: the inbox is clear"], (
        "the second run is handed the conversation the first one left")
    _hand_back(store, dispatcher, run2, "run 2 said: still clear")
    assert store.by_name("u1", THREAD)["version"] == 2
    assert len([r for r in store.list_for("u1") if r["name"] == THREAD]) == 1


def test_a_reply_between_runs_is_seen_by_the_next_run(env):
    client, store, dispatcher = env
    _run(client, "first")
    _hand_back(store, dispatcher, dispatcher.dispatched[-1], "run 1")
    assert _reply(client, "also check the budget").status_code == 200
    reply = dispatcher.dispatched[-1]
    assert reply["trigger"] == "message", "the person's reply is an ordinary human turn"
    _hand_back(store, dispatcher, reply, "person: also check the budget")
    _run(client, "second")
    assert _handed(dispatcher, dispatcher.dispatched[-1]) == ["run 1", "person: also check the budget"]
    assert read_flow(store.by_name("u1", THREAD)["flow"])["flow"] == "brief", (
        "a person's reply never unbinds the thread")


def test_a_busy_thread_queues_the_run_until_it_is_idle(env, busy):
    client, _, dispatcher = env
    _run(client)
    n = len(dispatcher.dispatched)
    busy.add(units.chat_unit_id("u1", THREAD))
    r = _run(client)
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "thread_busy"
    assert len(dispatcher.dispatched) == n, "never two turns at once in one thread"


def test_the_store_alone_marks_the_thread_a_routine(env, busy):
    """The rail and the busy check read the binding from the store, so a routine thread stays a
    routine's even where the per-person index has lost its mark."""
    client, store, _ = env
    _run(client)
    client.app.state.sessions._mem["u1"][THREAD].pop("routine")   # the index forgets the mark
    assert not next(r for r in client.app.state.sessions.list("u1") if r["session"] == THREAD).get("routine")
    rows = {r["session"]: r for r in client.get("/api/sessions", headers=PERSON).json()["sessions"]}
    assert rows[THREAD]["routine"]["flow"] == "brief"
    busy.add(units.routine_unit_id("u1", THREAD))
    r = _reply(client, "hi")
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "thread_busy"


def test_an_ordinary_chat_is_never_bound_to_a_flow(env):
    client, store, _ = env
    assert _reply(client, "hello").status_code == 200
    assert client.post("/api/chat/submit", headers=PERSON,
                       json={"prompt": "hi", "session": "main"}).status_code == 200
    assert store.by_name("u1", "main")["flow"] is None
