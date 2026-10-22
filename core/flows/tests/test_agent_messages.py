"""ADR-0046 decision 13 — agents speak to each other through flows.

`message_agent` (agent-api) publishes `thread.message`; flows-api's intake checks the policy keys
and the guards, and the `agent_messages` flow delivers the text into the target thread as a queued
turn marked as the sending agent's. Offline: identity and agent-api stubbed at their HTTP seams."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key", "VEXA_FLOWS_SERVICE_KEY": "test-flows-service-key-not-a-placeholder",
        "VEXA_FLOWS_DB_URL": "postgresql+pg8000://agent-messages:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api as fa  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

from flows import FakeClock, Registry, tick  # noqa: E402
from flows_steps import common, policies, routine  # noqa: E402

if not isinstance(fa.db, SqliteDB):
    fa.db = SqliteDB()
OPERATOR = {"X-Flows-Operator-Key": fa.API_KEY}


def _msg(sender="7", to="7", thread="main", text="please summarise", shared=False,
         routine_sender=False, origin=None, mid="m1"):
    refs = {"uid": to, "thread": thread, "text": text, "message_id": mid, "sender_uid": sender,
            "sender_thread": "pchat-1", "sender_label": "Ada's chat", "shared": shared,
            "sender_routine": routine_sender}
    if origin is not None:
        refs["_origin"] = origin
    return {"event_type": "thread.message", "source_event_id": f"msg:{sender}:{mid}",
            "refs": refs}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    fa.db.execute("DELETE FROM reaction")
    monkeypatch.setattr(policies, "read", lambda uid: policies.resolve(None))
    yield


@pytest.fixture
def client():
    return TestClient(fa.app, raise_server_exceptions=False)


def _set_policy(monkeypatch, text):
    monkeypatch.setattr(policies, "read", lambda uid: policies.resolve(f"---\n{text}\n---\n"))


# ── who may message whom ─────────────────────────────────────────────────────────────────────────

def test_between_one_persons_own_threads_is_allowed(client):
    r = client.post("/events", json=_msg(), headers=OPERATOR)
    assert r.status_code == 202 and r.json()["reactions_created"] == 1


def test_across_people_without_a_shared_workspace_is_refused_naming_the_key(client):
    r = client.post("/events", json=_msg(to="9"), headers=OPERATOR)
    assert r.status_code == 403
    assert r.json()["detail"]["refused"] == "agent_messages_shared"


def test_across_people_is_off_by_default_even_inside_a_shared_workspace(client):
    r = client.post("/events", json=_msg(to="9", shared=True, thread="s-team"), headers=OPERATOR)
    assert r.status_code == 403
    assert r.json()["detail"]["refused"] == "agent_messages_shared"


def test_across_people_switched_on_reaches_a_shared_conversation(client, monkeypatch):
    _set_policy(monkeypatch, "agent_messages_shared: on")
    r = client.post("/events", json=_msg(to="9", shared=True, thread="s-team"), headers=OPERATOR)
    assert r.status_code == 202, r.text


def test_a_routine_sending_is_refused_by_default(client):
    r = client.post("/events", json=_msg(routine_sender=True), headers=OPERATOR)
    assert r.status_code == 403
    assert r.json()["detail"]["refused"] == "agent_messages_from_routines"


@pytest.mark.parametrize("line,case,expect", [
    ("agent_messages_own: off", {}, "agent_messages_own"),
    ("agent_messages_shared: off", {"to": "9", "shared": True, "thread": "s-team"},
     "agent_messages_shared"),
    ("agent_messages_shared: on", {"to": "9", "shared": True, "thread": "s-team"}, None),
    ("agent_messages_from_routines: on", {"routine_sender": True}, None),
])
def test_the_policy_file_flips_each_rule(client, monkeypatch, line, case, expect):
    _set_policy(monkeypatch, line)
    r = client.post("/events", json=_msg(**case), headers=OPERATOR)
    if expect is None:
        assert r.status_code == 202, r.text
    else:
        assert r.status_code == 403 and r.json()["detail"]["refused"] == expect


# ── the guards ───────────────────────────────────────────────────────────────────────────────────

def test_ping_pong_stops_at_the_hop_limit(client):
    hops = [f"thread:7/t{i}" for i in range(7)]
    assert client.post("/events", json=_msg(origin=hops, mid="a"), headers=OPERATOR).status_code == 202
    r = client.post("/events", json=_msg(origin=hops + ["thread:7/t7"], mid="b"), headers=OPERATOR)
    assert r.status_code == 429 and r.json()["detail"]["refused"] == "loop_max_hops"


def test_messages_are_capped_per_sender_per_day(client, monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_USER_MESSAGES_PER_DAY", "2")
    codes = [client.post("/events", json=_msg(mid=f"q{i}"), headers=OPERATOR).status_code
             for i in range(3)]
    assert codes == [202, 202, 429]
    other = client.post("/events", json=_msg(sender="8", to="8", mid="q9"), headers=OPERATOR)
    assert other.status_code == 202, "another person's agents count their own"


# ── delivery ─────────────────────────────────────────────────────────────────────────────────────

class _AgentApi:
    def __init__(self):
        self.turns: list[dict] = []
        self.busy: set = set()

    def __call__(self, method, url, headers, body=None, timeout=20):
        if method == "GET" and "/api/chat/pending" in url:
            return 200, {"busy": any(f"session={t}" in url for t in self.busy), "pending": []}
        if method == "POST" and url.endswith("/api/chat/submit"):
            self.turns.append({"uid": headers["X-User-Id"], "session": body["session"],
                               "text": body["prompt"], "headers": dict(headers)})
            return 200, {"ok": True}
        raise AssertionError(url)


@pytest.fixture
def agent_api(monkeypatch):
    api = _AgentApi()
    monkeypatch.setattr(routine, "http", api)
    monkeypatch.setattr(common, "AGENT_API", "http://agent-api.test")
    return api


def _deliverer():
    from flows import EventType, admit
    db, clock, reg = SqliteDB(), FakeClock(1_000_000.0), Registry()
    routine.register_messages(reg)
    reg.flow(name=routine.MESSAGE_FLOW, version=1, on=EventType(routine.MESSAGE_EVENT),
             steps=[reg.steps["deliver_message"]])

    def send(**kw):
        m = _msg(**kw)
        return admit(db, reg, clock, source_event_id=m["source_event_id"],
                     event_type=m["event_type"], subject_refs=m["refs"])
    return db, clock, reg, send


def test_a_round_trip_lands_in_both_threads_in_order(agent_api):
    db, clock, reg, send = _deliverer()
    send(thread="flow-b", text="A asks B", mid="1", origin=["thread:7/pchat-1"])
    while tick(db, reg, clock):
        pass
    send(thread="pchat-1", text="B answers A", mid="2",
         origin=["thread:7/pchat-1", "thread:7/flow-b"])
    while tick(db, reg, clock):
        pass
    assert [(t["session"], t["text"]) for t in agent_api.turns] == [
        ("flow-b", "A asks B"), ("pchat-1", "B answers A")]
    h = agent_api.turns[0]["headers"]
    assert h["X-Vexa-Message-From-Thread"] == "pchat-1" and h["X-Vexa-Message-From-Uid"] == "7"
    assert h["X-Vexa-Flow-Regime"] == "autonomous", "nobody typed it"
    assert json.loads(h["X-Vexa-Message-Origin"]) == ["thread:7/pchat-1"], (
        "the chain rides into the receiving thread so its reply carries the hop count")


def test_a_message_to_a_busy_thread_queues_behind_it(agent_api):
    db, clock, reg, send = _deliverer()
    agent_api.busy.add("flow-b")
    send(thread="flow-b", text="later", mid="1")
    tick(db, reg, clock)
    assert agent_api.turns == []
    agent_api.busy.clear()
    clock.advance(31)
    while tick(db, reg, clock):
        pass
    assert [t["text"] for t in agent_api.turns] == ["later"]
