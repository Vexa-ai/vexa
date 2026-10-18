"""One chat, followed across every hop it causes (logevent.v1, Vexa-ai/vexa#1783).

Two ids:

* the TRACE — one turn. agent-api dispatches it under the request's ``X-Trace-Id``; the worker gets
  it on the signed stream entry (or ``VEXA_TRACE_ID`` at a cold spawn) and puts it on its MCP
  attachment, so every tool call continues it.
* the SESSION — the agent unit. agent-api mints the worker's delegation token with ``unit=<unit id>``;
  identity answers it, the gateway signs it into the identity it forwards (``delegation.unit`` →
  ``x-user-delegation-unit``), agent-api logs under it and asserts it to the credential broker, which
  audits by it. A worker cannot name another one: the claim is inside a signature.

The chain is driven here with fakes in one process — the dispatcher, the worker's serve loop, the
gateway's signing (``identity_token.claims_from_validation`` + ``sign``, the vendored copy of the
gateway's own), agent-api's door (``IdentityGuard`` + ``obs.TraceMiddleware``) and the broker client
— so the ids are checked at every seam without a container.
"""
from __future__ import annotations

import json
import threading
import time

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import broker_assertion, broker_client, delegation_refresh as drf
from control_plane import delegation_revocation as dr
from control_plane import dispatch, identity_token
from shared import delegation, obs
from shared.config import load_settings
from shared.units import delegation_key
from tests.conftest import signed_turn
from worker import engine

SECRET = "test-delegation-secret"
INTERNAL = "internal-secret-for-tests"

CHAT = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}],
    "trigger": "message",
    "context": {"kind": "none", "session": "main"},
    "start": {"entrypoint": {"inline": "what did Ada promise?"}},
}


class _Runtime:
    def __init__(self):
        self.spawned: list[dict] = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "running"


class _Identity:
    def mint(self, *a):
        return "dispatch-token"


@pytest.fixture
def lines():
    sink: list = []
    obs.capture(sink)
    yield sink
    obs.capture(None)


def _dispatch(tmp_path, store, *, trace=None):
    settings = load_settings(mcp_url="http://gateway:8000/mcp", mcp_delegation_secret=SECRET,
                             workspaces_dir=str(tmp_path))
    rt = _Runtime()
    token = obs.set_trace_id(trace) if trace else None
    try:
        d = dispatch.Dispatcher(settings, rt, _Identity(), delegation_store=store)
        d._redis = lambda: store                       # the warm path writes the signed entry here
        d._watch_delivery = lambda *a, **k: None       # no worker to ack in this test
        uid = d.dispatch(CHAT)
    finally:
        if token is not None:
            obs.reset_trace_id(token)
    return uid, rt.spawned[-1]


# ── agent-api: the dispatch ─────────────────────────────────────────────────────────────────────

def test_the_workers_token_names_its_unit_and_the_spawn_carries_the_requests_trace(tmp_path, lines):
    store = fakeredis.FakeRedis(decode_responses=True)
    uid, env = _dispatch(tmp_path, store, trace="chat-request-trace")
    claims = delegation.verify_delegation(SECRET, env["VEXA_MCP_DELEGATION_TOKEN"])
    assert claims["unit"] == uid == "agent-u_jane-chat-main"
    assert env["VEXA_TRACE_ID"] == "chat-request-trace"
    (_id, fields), = store.xrange(f"unit:{uid}:in")
    assert json.loads(fields["turn"])["trace"] == "chat-request-trace"   # signed with the turn
    line = next(e for e in lines if e["event"] == "unit_dispatched")
    assert (line["session"], line["trace_id"]) == (uid, "chat-request-trace")


def test_a_refreshed_token_keeps_the_unit(tmp_path):
    store = fakeredis.FakeRedis(decode_responses=True)
    uid, env = _dispatch(tmp_path, store)
    claims = delegation.verify_delegation(SECRET, env["VEXA_MCP_DELEGATION_TOKEN"])
    dr.sweep(store, lambda: [uid], now=claims["iat"] + 1000,
             refresher=drf.Refresher(SECRET, lambda: 3600))
    fresh = delegation.verify_delegation(SECRET, store.get(delegation_key(uid)), now=claims["iat"] + 1000)
    assert fresh["jti"] != claims["jti"] and fresh["unit"] == uid


# ── the worker: the turn's trace reaches the MCP attachment ─────────────────────────────────────

class _Stream:
    def __init__(self, inbox):
        self.out: list = []
        self._inbox = list(inbox)
        self._lock = threading.Lock()

    def xadd(self, name, fields):
        with self._lock:
            self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        with self._lock:
            if not self._inbox:
                return []
            eid, fields = self._inbox.pop(0)
        return [(next(iter(streams)), [(eid, fields)])]


def _attached(path):
    return json.loads(path.read_text())["mcpServers"][engine.VEXA_MCP_SERVER]["headers"]


def _serve_one_turn(tmp_path, entry: dict, token: str, *, start=None):
    """Run the worker's serve loop over one stream entry; return the attachment headers each turn
    started with."""
    path = tmp_path / "mcp.json"
    engine.write_mcp_config(path, "http://gateway:8000/mcp", token)
    refresh = engine.DelegationRefresh(fakeredis.FakeRedis(decode_responses=True), "k",
                                       path=str(path), url="http://gateway:8000/mcp", token=token)
    seen: list = []

    def turn(_prompt):
        refresh()                            # what main()'s `_fresh` does before every turn
        seen.append((_attached(path), obs.get_trace_id()))
        yield {"type": "done", "reply": "ok", "sessionId": "s1"}

    stream = _Stream([("1-0", signed_turn(entry))] if entry else [])
    engine.serve(stream, out_topic="out", in_topic="in", turn=turn, start=start or {}, idle_ms=10)
    return seen


def test_a_warm_turn_runs_under_the_trace_on_its_signed_entry(tmp_path, monkeypatch, lines):
    monkeypatch.setenv("VEXA_WRITEBACK", "0")
    seen = _serve_one_turn(tmp_path, {"type": "message", "prompt": "hi", "nonce": "n1",
                                      "trace": "turn-trace-1"}, "vxd_tok")
    (headers, bound), = seen
    assert bound == "turn-trace-1"
    assert headers["X-Trace-Id"] == "turn-trace-1"
    assert headers["Authorization"] == "Bearer vxd_tok"
    started = [e for e in lines if e["event"] == "turn_started"]
    assert started and started[0]["trace_id"] == "turn-trace-1"
    assert not any("vxd_tok" in json.dumps(e) for e in lines)


def test_the_entrypoint_turn_runs_under_VEXA_TRACE_ID(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_WRITEBACK", "0")
    monkeypatch.setenv("VEXA_TRACE_ID", "cold-spawn-trace")
    seen = _serve_one_turn(tmp_path, {}, "vxd_tok", start={"entrypoint": {"inline": "hi"}})
    assert seen[0][0]["X-Trace-Id"] == "cold-spawn-trace"


def test_a_trace_that_is_not_header_safe_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_WRITEBACK", "0")
    seen = _serve_one_turn(tmp_path, {"type": "message", "prompt": "hi",
                                      "trace": "x\r\nAuthorization: Bearer stolen"}, "vxd_tok")
    tid = seen[0][0]["X-Trace-Id"]
    assert "\n" not in tid and "stolen" not in tid and tid == seen[0][1]


# ── agent-api's door: the session comes from the gateway's signature, never a client ─────────────

@pytest.fixture
def gateway_key():
    return identity_token.generate_signing_key()


@pytest.fixture
def broker(tmp_path, monkeypatch):
    key = tmp_path / "agent.key"
    key.write_text("fixture-agent-key-" + "c" * 40 + "\n")
    sent = []
    real = httpx.Client

    def client(**kwargs):
        def handle(req):
            sent.append(req)
            return httpx.Response(200, json={"ok": True})
        return real(transport=httpx.MockTransport(handle), **{k: v for k, v in kwargs.items() if k != "transport"})
    monkeypatch.setattr(broker_client.httpx, "Client", client)
    return {"sent": sent, "key_file": str(key), "key": key.read_bytes().strip()}


def _agent_api(gateway_key, broker):
    app = FastAPI()

    @app.post("/connections/use")
    def use():                         # sync, like the Connections routes: served in the threadpool
        subject, signed = broker_client.forwarded()
        broker_client.request(base_url="http://credential-broker:8100", key_file=broker["key_file"],
                              role="agent", actor=subject, method="POST", path="/api/connections/c1/call",
                              payload={"parameters": {}}, identity=signed)
        obs.log_event("connection_used")
        return {"ok": True}

    app.add_middleware(broker_client.ForwardedIdentity)
    app.add_middleware(obs.TraceMiddleware)
    app.add_middleware(identity_token.IdentityGuard, verify_key=gateway_key.public_key(),
                       internal_secret=INTERNAL, service="agent-api")
    return TestClient(app)


def _gateway_forward(gateway_key, validation: dict) -> dict:
    """What the gateway stamps onto a forward for identity's answer (gateway-identity.v1)."""
    return identity_token.signed_headers(gateway_key, validation)


def _assertion(broker, req):
    return broker_assertion.verify(req.headers[broker_assertion.HEADER], key_for=lambda role: broker["key"],
                                   method="POST", path="/api/connections/c1/call", body=req.content)


def test_end_to_end_one_chat_is_one_session_and_one_trace_at_every_hop(tmp_path, gateway_key, broker,
                                                                      monkeypatch, lines):
    """chat → agent-api dispatch → worker turn → MCP (via the gateway) → agent-api → broker."""
    monkeypatch.setenv("VEXA_WRITEBACK", "0")
    store = fakeredis.FakeRedis(decode_responses=True)
    # 1. the chat request reaches agent-api under the gateway's trace; the unit is dispatched
    uid, env = _dispatch(tmp_path, store, trace="chat-trace-42")
    token = env["VEXA_MCP_DELEGATION_TOKEN"]
    # 2. the worker runs the turn: its MCP attachment carries the token and the trace
    seen = _serve_one_turn(tmp_path, {"type": "message", "prompt": "hi", "trace": env["VEXA_TRACE_ID"]},
                           token)
    attached = seen[0][0]
    assert attached["X-Trace-Id"] == "chat-trace-42"
    # 3. the gateway resolves the bearer through identity — which answers the signed unit — and
    #    signs it onto the forward (the MCP hands the same trace back on its re-entry)
    claims = delegation.verify_delegation(SECRET, attached["Authorization"].split(" ", 1)[1])
    validation = {"user_id": claims["sub"], "scopes": ["bot", "tx"], "email": "jane@example.com",
                  "delegation": {"regime": claims["scope"]["regime"],
                                 "workspaces": claims["scope"]["workspaces"], "unit": claims["unit"]}}
    forward = _gateway_forward(gateway_key, validation)
    assert forward["x-user-delegation-unit"] == uid
    # 4. agent-api serves the tool's call: logs under the session, asserts it to the broker
    lines.clear()
    r = _agent_api(gateway_key, broker).post(
        "/connections/use", headers={**forward, "X-Trace-Id": attached["X-Trace-Id"]})
    assert r.status_code == 200, r.text
    assert r.headers["x-trace-id"] == "chat-trace-42"
    req = broker["sent"][-1]
    assert req.headers["x-trace-id"] == "chat-trace-42"
    assert _assertion(broker, req)["session"] == uid
    used = next(e for e in lines if e["event"] == "connection_used")
    assert (used["session"], used["trace_id"], used["service"]) == (uid, "chat-trace-42", "agent-api")
    assert not any(token in json.dumps(e) for e in lines)


def test_a_client_cannot_name_a_session(gateway_key, broker, lines):
    """A person's own signed identity carries no unit: a client-set x-user-delegation-unit is
    dropped by the guard, nothing is logged under it, and the broker is asked under a per-request id."""
    forward = _gateway_forward(gateway_key, {"user_id": "u_jane", "scopes": ["bot", "tx"]})
    r = _agent_api(gateway_key, broker).post(
        "/connections/use", headers={**forward, "x-user-delegation-unit": "agent-u_bob-chat-main"})
    assert r.status_code == 200, r.text
    assert _assertion(broker, broker["sent"][-1])["session"].startswith("agent-request-")
    assert not any(e.get("session") for e in lines)


def test_an_unsigned_session_header_is_refused_at_the_door(gateway_key, broker):
    r = _agent_api(gateway_key, broker).post(
        "/connections/use", headers={"x-user-delegation-unit": "agent-u_bob-chat-main"})
    assert r.status_code == 401
    assert not broker["sent"]


def test_every_line_conforms_to_logevent_v1(tmp_path, lines):
    store = fakeredis.FakeRedis(decode_responses=True)
    _dispatch(tmp_path, store, trace="t-1")
    allowed = {"ts", "level", "service", "trace_id", "span", "user_id", "meeting_id", "session",
               "audience", "event", "fields"}
    assert lines
    for e in lines:
        assert {"ts", "level", "service", "trace_id", "audience", "event"} <= set(e) <= allowed
        assert e["level"] in {"debug", "info", "warning", "error", "critical"}
        assert e["audience"] in {"user", "system"}
