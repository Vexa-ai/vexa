"""H5/W1 — no worker, turn or model shell holds a model-provider key.

agent-api seals the turn's model route for the model proxy (``control_plane.model_proxy``) and hands
the worker a per-turn token (``shared.model_grant``). These run the proxy offline — its ASGI app, a
fakeredis grant store, a mock upstream recording what reaches the provider — and the dispatch end to
end with a fake runtime capturing the spec a worker would be started with.
"""
from __future__ import annotations

import json
import threading

import fakeredis
import httpx
import pytest
from starlette.testclient import TestClient

from control_plane import dispatch
from control_plane import model_proxy as mp
from shared import model_grant as mg
from shared import unit_input
from shared.config import load_settings
from tests.test_unit_foundation import VALID_INV, _FakeIdentity

SECRET = "model-proxy-secret-for-tests-0123456789"
REAL_KEY = "sk-ant-api03-REAL-DEPLOYMENT-KEY-never-in-a-worker"
PROXY = "http://model-proxy:8102"
DELEGATION = "eyJ.delegation-bearer.of-the-person"


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class StreamingTransport(httpx.AsyncBaseTransport):
    """``httpx.MockTransport`` reads a response whole before returning it; a provider streaming an
    answer does not. This hands the handler's stream back unread."""

    def __init__(self, handler) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        return await self.handler(request)


class Upstream:
    """A provider: records every request it receives, answers JSON or an SSE stream."""

    def __init__(self, stream_chunks=None, gate: "threading.Event | None" = None) -> None:
        self.requests: list[httpx.Request] = []
        self.chunks = stream_chunks
        self.gate = gate

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.chunks is None:
            return httpx.Response(200, json={"id": "msg_1", "content": [{"type": "text", "text": "ok"}]})
        chunks, gate = self.chunks, self.gate

        async def body():
            import anyio
            yield chunks[0]
            if gate is not None:          # the rest is produced only once the client has read the first
                await anyio.to_thread.run_sync(gate.wait, 10)
            for c in chunks[1:]:
                yield c
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())


@pytest.fixture
def world():
    r = fakeredis.FakeRedis(decode_responses=True)
    clock = Clock()
    grants = mp.Grants(r, SECRET, clock=clock)
    upstream = Upstream()
    app = mp.create_app(grants, transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        yield r, clock, grants, upstream, client


def _route(**kw) -> mp.Route:
    base = dict(base_url="https://api.anthropic.com", auth=mp.AUTH_X_API_KEY, credential=REAL_KEY,
                models=("claude-sonnet-4-5",))
    base.update(kw)
    return mp.Route(**base)


def _turn(grants, unit="agent-1-chat-main", route=None, ttl=900, **kw) -> str:
    rid = grants.put_route(unit, route or _route())
    return grants.mint(unit, rid, ttl_sec=ttl, **kw)


def _ask(client, token, model="claude-sonnet-4-5", path="/llm/v1/messages", header="authorization"):
    headers = {"authorization": f"Bearer {token}"} if header == "authorization" else {"x-api-key": token}
    return client.post(path, headers={**headers, "anthropic-version": "2023-06-01"},
                       json={"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]})


# ── the proxy forwards with the route's key, never the token ──────────────────────────────────────

def test_the_proxy_forwards_with_the_routes_key_and_never_the_turn_token(world):
    _r, _clock, grants, upstream, client = world
    token = _turn(grants)
    resp = _ask(client, token)
    assert resp.status_code == 200 and resp.json()["id"] == "msg_1"
    (req,) = upstream.requests
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == REAL_KEY
    assert "authorization" not in req.headers
    assert token not in json.dumps(dict(req.headers)) and token.encode() not in req.content
    assert req.headers["anthropic-version"] == "2023-06-01"


def test_the_token_is_accepted_as_an_api_key_header_too(world):
    _r, _clock, grants, upstream, client = world
    assert _ask(client, _turn(grants), header="x-api-key").status_code == 200
    assert upstream.requests[0].headers["x-api-key"] == REAL_KEY


def test_a_bearer_route_and_a_gateway_path(world):
    _r, _clock, grants, upstream, client = world
    token = _turn(grants, route=_route(base_url="https://openrouter.ai/api/v1", auth=mp.AUTH_BEARER,
                                       models=("z-ai/glm-5.3",)))
    resp = client.post("/llm/chat/completions", headers={"authorization": f"Bearer {token}"},
                       json={"model": "z-ai/glm-5.3", "messages": []})
    assert resp.status_code == 200
    req = upstream.requests[0]
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {REAL_KEY}"


# ── the four refusals ─────────────────────────────────────────────────────────────────────────────

def test_a_token_from_another_turn_is_refused(world):
    """Turn 1's token stops working when turn 1 ends (the worker revokes it as its last run lets go);
    turn 2's own token keeps working, and neither reaches another unit's route."""
    _r, _clock, grants, upstream, client = world
    unit = "agent-1-chat-main"
    rid = grants.put_route(unit, _route())
    turn1 = grants.mint(unit, rid, ttl_sec=900)
    turn2 = grants.mint(unit, rid, ttl_sec=900)
    assert _ask(client, turn1).status_code == 200
    assert client.post(mg.REVOKE_PATH, headers={"authorization": f"Bearer {turn1}"}).status_code == 200
    refused = _ask(client, turn1)
    assert refused.status_code == 401 and "not a live turn" in refused.json()["error"]["message"]
    assert _ask(client, turn2).status_code == 200
    # a token is bound to its own unit's route: another unit's token reaches that unit's endpoint only
    other = _turn(grants, unit="agent-2-chat-main",
                  route=_route(base_url="https://other.example", credential="sk-other"))
    assert _ask(client, other).status_code == 200
    assert upstream.requests[-1].headers["x-api-key"] == "sk-other"
    assert upstream.requests[-2].headers["x-api-key"] == REAL_KEY
    assert len(upstream.requests) == 3          # the refused request never left the proxy


def test_an_expired_token_is_refused(world):
    r, clock, grants, upstream, client = world
    token = _turn(grants, ttl=900)
    assert _ask(client, token).status_code == 200
    clock.t += 901                               # the record is still in Redis: the claim decides
    assert r.get(mp.GRANT_PREFIX + mp._digest(token))
    refused = _ask(client, token)
    assert refused.status_code == 401 and "expired" in refused.json()["error"]["message"]
    assert len(upstream.requests) == 1


def test_a_model_outside_the_turns_scope_is_refused(world):
    _r, _clock, grants, upstream, client = world
    token = _turn(grants, route=_route(models=("claude-haiku-4-5",)))
    refused = _ask(client, token, model="claude-opus-4-1")
    assert refused.status_code == 403 and "outside this turn's scope" in refused.json()["error"]["message"]
    assert upstream.requests == []
    # positive control: the turn's own model (and its dated snapshot) goes through
    assert _ask(client, token, model="claude-haiku-4-5").status_code == 200
    assert _ask(client, token, model="claude-haiku-4-5-20251001").status_code == 200


def test_an_unknown_or_missing_token_is_refused(world):
    _r, _clock, _grants, upstream, client = world
    assert _ask(client, "vxm_made-up").status_code == 401
    assert client.post("/llm/v1/messages", json={"model": "x"}).status_code == 401
    assert _ask(client, REAL_KEY).status_code == 401      # a real provider key is not a turn token
    assert upstream.requests == []


def test_the_turns_request_ceiling_is_its_spend_scope(world):
    _r, _clock, grants, upstream, client = world
    token = _turn(grants, max_requests=2)
    assert [_ask(client, token).status_code for _ in range(3)] == [200, 200, 429]
    assert len(upstream.requests) == 2


def test_an_output_allowance_above_the_cap_is_lowered(world):
    _r, _clock, grants, upstream, client = world
    token = _turn(grants, route=_route(max_output_tokens=32))
    assert _ask(client, token).status_code == 200
    assert json.loads(upstream.requests[0].content)["max_tokens"] == 32


# ── streaming ─────────────────────────────────────────────────────────────────────────────────────

def _serve(app):
    """The proxy on a real socket: an in-process test client buffers a whole response, so streaming
    is proved over HTTP, as a harness reaches it."""
    import socket
    import time as _time

    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(200):
        if server.started:
            break
        _time.sleep(0.02)
    return server, th, f"http://127.0.0.1:{port}"


def test_a_streamed_answer_reaches_the_harness_event_by_event():
    """The first event reaches the client BEFORE the upstream produces the second: the proxy relays
    as it arrives, it does not buffer the answer."""
    r = fakeredis.FakeRedis(decode_responses=True)
    grants = mp.Grants(r, SECRET, clock=Clock())
    gate = threading.Event()
    events = [b'event: message_start\ndata: {"type":"message_start"}\n\n',
              b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"text":"hel"}}\n\n',
              b'event: message_stop\ndata: {"type":"message_stop"}\n\n']
    upstream = Upstream(stream_chunks=events, gate=gate)
    app = mp.create_app(grants, transport=StreamingTransport(upstream))
    token = _turn(grants)
    server, th, base = _serve(app)
    try:
        with httpx.Client(timeout=5) as client:
            with client.stream("POST", base + "/llm/v1/messages", headers={"authorization": f"Bearer {token}"},
                               json={"model": "claude-sonnet-4-5", "stream": True, "max_tokens": 8,
                                     "messages": []}) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                it = resp.iter_bytes()
                first = next(it)          # would time out (5 s) if the proxy waited for the whole answer
                assert b"message_start" in first and b"message_stop" not in first
                gate.set()
                rest = b"".join(it)
    finally:
        gate.set()
        server.should_exit = True
        th.join(5)
    assert b"content_block_delta" in rest and b"message_stop" in rest
    assert upstream.requests[0].headers["x-api-key"] == REAL_KEY


# ── a subscription stays with the vendor's own CLI ───────────────────────────────────────────────

def test_a_subscription_route_keeps_its_credential_with_the_cli_and_is_never_proxied(world):
    """A consumer OAuth credential is read only by the official client, never moved into a proxy
    (docs/docs/model-credentials-licensing.mdx): the route stays the CLI's, every API key is still
    stamped empty, and the proxy refuses to forward a model call on it."""
    _r, _clock, grants, upstream, client = world
    env = {"VEXA_RUNNER": "claude-code", "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-SUB",
           "VEXA_MCP_DELEGATION_TOKEN": DELEGATION}
    token = mp.confine(env, url=PROXY, grants=grants, unit="agent-1", cold=True,
                       toolbelt="http://gateway:8000/mcp", source={"OPENAI_API_KEY": "sk-proj-DEPLOY"})
    assert env["VEXA_MODEL_ROUTE"] == mp.ROUTE_SUBSCRIPTION
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-SUB"
    assert env["ANTHROPIC_API_KEY"] == env["OPENAI_API_KEY"] == ""
    assert env["ANTHROPIC_BASE_URL"] == ""
    assert "sk-ant-oat01-SUB" not in json.dumps(grants.route(token).as_dict())
    refused = _ask(client, token)
    assert refused.status_code == 403 and upstream.requests == []
    # …while its toolbelt still goes through the proxy
    assert client.post(mg.MCP_PATH, headers={"authorization": f"Bearer {token}"}, json={}).status_code == 200


# ── the route rests sealed ────────────────────────────────────────────────────────────────────────

def test_the_route_rests_sealed_in_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    grants = mp.Grants(r, SECRET, clock=Clock())
    token = _turn(grants)
    dump = json.dumps({k: r.get(k) for k in r.keys("*")})
    assert REAL_KEY not in dump and token not in dump
    assert grants.route(token).credential == REAL_KEY
    assert mp.Grants(r, "another-secret-entirely-000000", clock=Clock()).route(token) is None


# ── the dispatch: what a worker is started with ───────────────────────────────────────────────────

class _Runtime:
    def __init__(self, alive: bool = False) -> None:
        self.spawned = []
        self.alive = alive

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, dict(env)))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "running" if self.alive else "completed"


#: Every credential a deployment can configure, set in agent-api's own environment.
DEPLOYMENT = {"ANTHROPIC_API_KEY": "sk-ant-api03-DEPLOY", "ANTHROPIC_AUTH_TOKEN": "sk-or-v1-DEPLOY",
              "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-DEPLOY", "OPENAI_API_KEY": "sk-proj-DEPLOY",
              "CODEX_API_KEY": "sk-codex-DEPLOY", "VEXA_LLM_API_KEY": "sk-llm-DEPLOY"}


def _dispatcher(r, runtime, **settings):
    s = load_settings(internal_api_secret="internal-secret-for-tests-0123456789abcdef",
                      model_proxy_url=PROXY, model_proxy_secret=SECRET, **settings)
    return dispatch.Dispatcher(s, runtime, _FakeIdentity(), warm_stream=r)


def _no_deployment_secret_in(text: str) -> list[str]:
    return [name for name, value in DEPLOYMENT.items() if value in text]


@pytest.mark.parametrize("runner", ["claude-code", "codex", "openai-agent"])
def test_no_key_appears_in_the_worker_env_or_its_delivered_turn(monkeypatch, runner):
    for k, v in DEPLOYMENT.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VEXA_RUNNER", runner)
    monkeypatch.setenv("VEXA_LLM_BASE_URL", "https://llm.deployment.example/v1")
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime()
    d = _dispatcher(r, rt)
    wid = d.dispatch({**VALID_INV, "runner": runner})
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_RUNNER"] == runner
    assert _no_deployment_secret_in(json.dumps(env)) == []
    for name in mg.PROVIDER_CREDENTIAL_ENV:
        assert env[name] == "", name
    assert env["ANTHROPIC_BASE_URL"] == env["VEXA_LLM_BASE_URL"] == PROXY + mg.LLM_PATH
    token = env[mg.TOKEN_ENV]
    assert token.startswith("vxm_")
    # the delivered entry carries the same turn's token, and no key either
    (entry,) = r.xrange(f"unit:{wid}:in")
    msg = unit_input.verified_turn(env[unit_input.KEY_ENV], entry[1])
    assert msg[mg.ENTRY_FIELD] == token
    assert _no_deployment_secret_in(json.dumps(entry[1])) == []
    # …and the proxy holds the deployment's credential for that harness
    route = mp.Grants(r, SECRET).route(token)
    expected = {"claude-code": "sk-or-v1-DEPLOY", "codex": "sk-proj-DEPLOY", "openai-agent": "sk-llm-DEPLOY"}
    assert route.credential == expected[runner]


def test_a_persons_own_endpoint_key_reaches_the_proxy_only(monkeypatch):
    class _Models:
        def resolve(self, subject):
            return {"mode": "custom", "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "sk-or-v1-PERSON-OWN", "model": "z-ai/glm-5.3", "runner": "openai-agent"}
    monkeypatch.setenv("VEXA_MODEL_BASE_URL_ALLOW", "openrouter.ai")
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime()
    s = load_settings(internal_api_secret="internal-secret-for-tests-0123456789abcdef",
                      model_proxy_url=PROXY, model_proxy_secret=SECRET)
    d = dispatch.Dispatcher(s, rt, _FakeIdentity(), warm_stream=r, model_config=_Models())
    d.dispatch(VALID_INV)
    env = rt.spawned[0][2]
    assert "sk-or-v1-PERSON-OWN" not in json.dumps(env)
    route = mp.Grants(r, SECRET).route(env[mg.TOKEN_ENV])
    assert (route.base_url, route.credential, route.models) == (
        "https://openrouter.ai/api/v1", "sk-or-v1-PERSON-OWN", ("z-ai/glm-5.3",))


def test_a_message_to_a_running_worker_gets_a_token_to_the_route_that_worker_runs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-FIRST")
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime(alive=False)
    d = _dispatcher(r, rt)
    wid = d.dispatch(VALID_INV)
    first = rt.spawned[0][2][mg.TOKEN_ENV]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-SECOND")
    rt.alive = True                         # warm: the worker keeps the route it was started on
    d.dispatch(VALID_INV)
    entries = r.xrange(f"unit:{wid}:in")
    second = unit_input.verified_turn(rt.spawned[0][2][unit_input.KEY_ENV], entries[-1][1])[mg.ENTRY_FIELD]
    grants = mp.Grants(r, SECRET)
    assert second != first
    assert grants.route(second).credential == grants.route(first).credential == "sk-ant-api03-FIRST"


def test_without_a_proxy_the_worker_holds_no_credential_at_all(monkeypatch):
    """Fail closed: a deployment missing the proxy settings starts workers with no model credential,
    never with the key."""
    for k, v in DEPLOYMENT.items():
        monkeypatch.setenv(k, v)
    rt = _Runtime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    env = rt.spawned[0][2]
    assert _no_deployment_secret_in(json.dumps(env)) == []
    assert env[mg.TOKEN_ENV] == ""


def test_a_pinned_claude_model_pins_every_tier_the_cli_asks_for(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-X")
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime()
    _dispatcher(r, rt, agent_model="claude-sonnet-4-5").dispatch(VALID_INV)
    env = rt.spawned[0][2]
    assert {env[t] for t in mp._CLAUDE_TIERS} == {"claude-sonnet-4-5"}
    assert mp.Grants(r, SECRET).route(env[mg.TOKEN_ENV]).models == ("claude-sonnet-4-5",)


# ── route reading ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env,expected", [
    ({"VEXA_RUNNER": "claude-code", "ANTHROPIC_API_KEY": "k"}, (mp.ANTHROPIC_API, mp.AUTH_X_API_KEY, "k", "")),
    ({"VEXA_RUNNER": "claude-code", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "https://gw"},
     ("https://gw", mp.AUTH_BEARER, "t", "")),
    ({"VEXA_RUNNER": "claude-code", "CLAUDE_CODE_OAUTH_TOKEN": "o"}, (mp.ANTHROPIC_API, mp.AUTH_OAUTH, "", mp.SUB_CLAUDE)),
    ({"VEXA_RUNNER": "claude-code"}, (mp.ANTHROPIC_API, mp.AUTH_OAUTH, "", mp.SUB_CLAUDE)),
    ({"VEXA_RUNNER": "claude-code", "VEXA_MODEL_ROUTE": "subject", "ANTHROPIC_BASE_URL": "https://own"},
     ("https://own", mp.AUTH_NONE, "", "")),
    ({"VEXA_RUNNER": "codex", "OPENAI_API_KEY": "sk"}, (mp.OPENAI_API, mp.AUTH_BEARER, "sk", "")),
    ({"VEXA_RUNNER": "codex"}, ("", mp.AUTH_OAUTH, "", mp.SUB_CODEX)),
    ({"VEXA_RUNNER": "openai-agent", "VEXA_LLM_BASE_URL": "http://q/v1", "VEXA_LLM_API_KEY": "l"},
     ("http://q/v1", mp.AUTH_BEARER, "l", "")),
])
def test_the_route_is_read_the_way_its_harness_reads_it(env, expected):
    rt = mp.route_of(env)
    assert (rt.base_url, rt.auth, rt.credential, rt.subscription) == expected


def test_the_family_alias_and_snapshot_rules():
    assert mp.model_in_scope("claude-sonnet-4-5-20250929", ("sonnet",))
    assert mp.model_in_scope("claude-sonnet-4-5-20250929", ("claude-sonnet-4-5[1m]",))
    assert not mp.model_in_scope("claude-opus-4-1", ("sonnet",))
    assert not mp.model_in_scope("claude-sonnet-4-5x", ("claude-sonnet-4-5",))
    assert mp.model_in_scope("anything", ())


def test_the_ports_twin_matches_the_shared_names():
    from llm import ports
    assert set(ports.MODEL_CREDENTIAL_VARS) == {*mg.PROVIDER_CREDENTIAL_ENV, mg.TOKEN_ENV}


# ── the toolbelt: the delegation bearer never reaches the model's shell ───────────────────────────

def test_the_toolbelt_goes_through_the_proxy_with_the_units_bearer(world):
    r, _clock, grants, upstream, client = world
    unit = "agent-1-chat-main"
    token = _turn(grants, unit=unit, route=_route().with_toolbelt("http://gateway:8000/mcp", DELEGATION))
    resp = client.post(mg.MCP_PATH, headers={"authorization": f"Bearer {token}", "mcp-session-id": "s1"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status_code == 200
    req = upstream.requests[-1]
    assert str(req.url) == "http://gateway:8000/mcp"
    assert req.headers["authorization"] == f"Bearer {DELEGATION}" and req.headers["mcp-session-id"] == "s1"
    # a refreshed bearer (agent-api's current record for the unit) wins over the one sealed at spawn
    from control_plane import delegation_revocation as dr
    r.set(dr.CURRENT_PREFIX + unit, "eyJ.refreshed")
    client.post(mg.MCP_PATH, headers={"authorization": f"Bearer {token}"}, json={})
    assert upstream.requests[-1].headers["authorization"] == "Bearer eyJ.refreshed"
    # toolbelt calls are not model calls: they never spend the turn's model-request ceiling
    assert r.get(mp.COUNT_PREFIX + mp._digest(token)) is None


def test_the_toolbelt_refuses_what_the_model_path_refuses(world):
    _r, clock, grants, upstream, client = world
    token = _turn(grants, route=_route().with_toolbelt("http://gateway:8000/mcp", DELEGATION), ttl=60)
    assert client.post(mg.MCP_PATH, headers={"authorization": "Bearer vxm_nope"}, json={}).status_code == 401
    clock.t += 61
    assert client.post(mg.MCP_PATH, headers={"authorization": f"Bearer {token}"}, json={}).status_code == 401
    no_belt = _turn(grants, unit="agent-9", route=_route())
    assert client.post(mg.MCP_PATH, headers={"authorization": f"Bearer {no_belt}"}, json={}).status_code == 404
    assert upstream.requests == []


def test_the_dispatch_seals_the_toolbelt_and_the_worker_attaches_the_proxy(monkeypatch, tmp_path):
    from shared import delegation as d
    from worker import engine
    secret = "delegation-secret-for-tests-0123456789"
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime()
    _dispatcher(r, rt, mcp_url="http://gateway:8000/mcp", mcp_delegation_secret=secret).dispatch(VALID_INV)
    env = rt.spawned[0][2]
    bearer = env["VEXA_MCP_DELEGATION_TOKEN"]
    assert d.verify_delegation(secret, bearer)
    route = mp.Grants(r, SECRET).route(env[mg.TOKEN_ENV])
    assert (route.toolbelt, route.toolbelt_bearer) == ("http://gateway:8000/mcp", bearer)
    # the worker: what it writes for its harness names the proxy and the turn token, never the bearer
    for k in ("VEXA_MCP_URL", "VEXA_MCP_DELEGATION_TOKEN", mg.URL_ENV):
        monkeypatch.setenv(k, env[k])
    monkeypatch.setattr(mg, "_boot_token", env[mg.TOKEN_ENV])
    monkeypatch.setattr(mg, "_revoke", lambda _t: None)
    path, _tools = engine.mcp_delegation_config(tmp_path)
    attachment = engine.McpAttachment(path)
    assert attachment.proxied
    with mg.use("vxm_run_token"):
        run_file = attachment.begin_run()
        endpoint = attachment.endpoint()
    for p in (path, run_file):
        text = open(p).read()
        assert bearer not in text
        cfg = json.loads(text)["mcpServers"][engine.VEXA_MCP_SERVER]
        assert cfg["url"] == PROXY + mg.MCP_PATH
    assert json.loads(open(run_file).read())["mcpServers"][engine.VEXA_MCP_SERVER]["headers"][
        "Authorization"] == "Bearer vxm_run_token"
    assert endpoint == (PROXY + mg.MCP_PATH, {"Authorization": "Bearer vxm_run_token"})
    attachment.end_run(run_file)
    import os
    assert not os.path.exists(run_file)
