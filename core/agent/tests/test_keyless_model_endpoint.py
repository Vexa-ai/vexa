"""A keyless in-house model runs a turn (car 61b, finding F3).

A bank's on-prem OpenAI-compatible model often takes no credential. The openai-agent harness with
``VEXA_LLM_BASE_URL`` and no key is a complete configuration: the credential preflight lets its turn
through, the proxy dials the endpoint with no Authorization header, and a deployment with neither a
key nor that endpoint is still refused before dispatch.

L2: a real FastAPI app over fakes (no redis, no runtime, no model), and the model proxy's ASGI app
over a fakeredis grant store and a mock upstream that records what reaches the endpoint.
"""
from __future__ import annotations

import json

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as ProxyClient

from control_plane import dispatch
from control_plane import model_proxy as mp
from control_plane.api import create_app
from control_plane.config_preflight import load_declaration
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import model_grant as mg
from shared.config import load_settings
from tests.test_unit_foundation import VALID_INV, _FakeIdentity

WHO = {"X-User-Id": "u_bank"}
BANK_MODEL = "https://llm.bank.internal/v1"
SECRET = "model-proxy-secret-for-tests-0123456789"
PROXY = "http://model-proxy:8102"
#: Every name that configures ``model_inference`` today, and the endpoints a harness may read.
CREDENTIAL_KEYS = tuple(e["key"] for e in load_declaration()["keys"]
                        if e.get("capability") == "model_inference")


class _Runtime:
    def __init__(self) -> None:
        self.spawned: list[tuple] = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, dict(env)))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "message-delta", "text": "hi"}
        yield {"type": "turn-complete"}


def _keyless(monkeypatch, *, base_url: str = BANK_MODEL) -> None:
    for k in (*CREDENTIAL_KEYS, "ANTHROPIC_BASE_URL", "VEXA_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VEXA_RUNNER", "openai-agent")
    if base_url:
        monkeypatch.setenv("VEXA_LLM_BASE_URL", base_url)


def _client(tmp_path):
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    rt = _Runtime()
    app = create_app(Dispatcher(settings, rt, _Identity()), stream_reader=_Reader(),
                     reader=WorkspaceReader(str(root)))
    return TestClient(app), rt


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/submit"])
def test_a_keyless_openai_agent_endpoint_dispatches_a_turn(tmp_path, monkeypatch, path):
    _keyless(monkeypatch)
    client, rt = _client(tmp_path)
    resp = client.post(path, json={"prompt": "hello", "session": "pchat-bank"}, headers=WHO)
    assert resp.status_code == 200, resp.text
    assert "No model credentials" not in resp.text
    assert len(rt.spawned) == 1
    env = rt.spawned[0][2]
    assert env["VEXA_RUNNER"] == "openai-agent"
    # no placeholder credential anywhere in what the worker is started with
    for name in mg.PROVIDER_CREDENTIAL_ENV:
        assert env.get(name, "") == "", name


@pytest.mark.parametrize("path,refused", [("/api/chat", 200), ("/api/chat/submit", 503)])
def test_a_keyless_config_with_no_endpoint_is_still_refused(tmp_path, monkeypatch, path, refused):
    _keyless(monkeypatch, base_url="")
    client, rt = _client(tmp_path)
    resp = client.post(path, json={"prompt": "hello", "session": "pchat-bank"}, headers=WHO)
    assert resp.status_code == refused
    assert "No model credentials are configured" in resp.text
    assert "VEXA_LLM_BASE_URL" in resp.text           # the message names the keyless path too
    assert rt.spawned == []


def test_an_endpoint_without_the_openai_agent_harness_is_still_refused(tmp_path, monkeypatch):
    """The endpoint counts only for the harness that sends no credential on its own: a claude-code
    deployment with a base URL and no key would fall to the CLI's sign-in, which is not configured."""
    _keyless(monkeypatch)
    monkeypatch.setenv("VEXA_RUNNER", "claude-code")
    client, rt = _client(tmp_path)
    resp = client.post("/api/chat/submit", json={"prompt": "hello", "session": "pchat-bank"}, headers=WHO)
    assert resp.status_code == 503 and rt.spawned == []


def test_no_authorization_header_reaches_a_keyless_endpoint(monkeypatch):
    """Dispatch end to end, then the proxy: the sealed route names no credential, and the request the
    endpoint receives carries neither an Authorization nor an x-api-key header — never a placeholder."""
    _keyless(monkeypatch)
    r = fakeredis.FakeRedis(decode_responses=True)
    rt = _Runtime()
    s = load_settings(internal_api_secret="internal-secret-for-tests-0123456789abcdef",
                      model_proxy_url=PROXY, model_proxy_secret=SECRET, agent_model="bank-qwen")
    dispatch.Dispatcher(s, rt, _FakeIdentity(), warm_stream=r).dispatch(
        {**VALID_INV, "runner": "openai-agent"})
    env = rt.spawned[0][2]
    assert env["VEXA_LLM_BASE_URL"] == PROXY + mg.LLM_PATH
    token = env[mg.TOKEN_ENV]
    grants = mp.Grants(r, SECRET)
    route = grants.route(token)
    assert (route.base_url, route.auth, route.credential) == (BANK_MODEL, mp.AUTH_NONE, "")

    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "c1", "choices": [], "usage": {
            "prompt_tokens": 1, "completion_tokens": 1}})

    with ProxyClient(mp.create_app(grants, transport=httpx.MockTransport(upstream))) as proxy:
        resp = proxy.post("/llm/chat/completions", headers={"authorization": f"Bearer {token}"},
                          json={"model": "bank-qwen", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200, resp.text
    (req,) = seen
    assert str(req.url) == BANK_MODEL + "/chat/completions"
    assert "authorization" not in req.headers and "x-api-key" not in req.headers
    assert token not in json.dumps(dict(req.headers))
