"""Unit tests for DockerBackend.ensure_image_alias — the rebuild-free TAG ALIAS that lets spawned
workers run the agent-api BYTES under a distinct image NAME (vexaai/v012-agent-worker:dev).

These never touch a real daemon: the unix-socket session is faked so we can assert the exact tag
call is made when the alias is missing, is a NO-OP when present, and FALLS BACK to the agent-api
image on any tag failure (so dispatch never breaks). Also exercises the worker create spec using the
worker image name."""
from __future__ import annotations

import pytest

from runtime_kernel.docker_backend import DockerBackend
from runtime_kernel.profiles import Runnable


class FakeResp:
    def __init__(self, status_code: int, text: str = "", body: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._body = body or {}

    def json(self):
        return self._body


class FakeSession:
    """Records requests and replies from a programmable map keyed by (METHOD, path-prefix)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, **kw):
        # url is http+unix://<sock>%2F...<path> — recover the API path after the encoded socket.
        path = "/" + url.split("%2F", 1)[1].split("/", 1)[1] if "%2F" in url else url
        self.calls.append((method, path))
        for (m, prefix), resp in self.routes.items():
            if method == m and path.startswith(prefix):
                return resp
        return FakeResp(500, "no route")


def _backend(routes) -> tuple[DockerBackend, FakeSession]:
    b = DockerBackend()
    sess = FakeSession(routes)
    b._session = sess
    return b, sess


TARGET = "vexaai/v012-agent-worker:dev"
SOURCE = "vexaai/v012-agent-api:dev"


def test_alias_created_when_missing():
    routes = {
        ("GET", f"/images/{TARGET}/json"): FakeResp(404),   # target absent
        ("GET", f"/images/{SOURCE}/json"): FakeResp(200),   # source present
        ("POST", f"/images/{SOURCE}/tag"): FakeResp(201),   # tag succeeds
    }
    b, sess = _backend(routes)
    assert b.ensure_image_alias(TARGET, SOURCE) == TARGET
    # the tag call carried the right repo+tag
    tag_calls = [p for (m, p) in sess.calls if m == "POST" and "/tag" in p]
    assert tag_calls == [f"/images/{SOURCE}/tag?repo=vexaai/v012-agent-worker&tag=dev"]


def test_alias_noop_when_present():
    routes = {("GET", f"/images/{TARGET}/json"): FakeResp(200)}  # target already exists
    b, sess = _backend(routes)
    assert b.ensure_image_alias(TARGET, SOURCE) == TARGET
    assert not any("/tag" in p for (_m, p) in sess.calls)  # never tagged


def test_alias_noop_when_target_equals_source():
    b, sess = _backend({})
    assert b.ensure_image_alias(SOURCE, SOURCE) == SOURCE
    assert sess.calls == []  # nothing to do, no daemon calls at all


def test_fallback_when_tag_fails():
    routes = {
        ("GET", f"/images/{TARGET}/json"): FakeResp(404),
        ("GET", f"/images/{SOURCE}/json"): FakeResp(200),
        ("POST", f"/images/{SOURCE}/tag"): FakeResp(500, "daemon boom"),
    }
    b, _ = _backend(routes)
    assert b.ensure_image_alias(TARGET, SOURCE) == SOURCE  # dispatch keeps using agent-api image


def test_fallback_when_source_missing():
    routes = {
        ("GET", f"/images/{TARGET}/json"): FakeResp(404),
        ("GET", f"/images/{SOURCE}/json"): FakeResp(404),  # source not built locally
    }
    b, sess = _backend(routes)
    assert b.ensure_image_alias(TARGET, SOURCE) == SOURCE
    assert not any("/tag" in p for (_m, p) in sess.calls)  # never attempts the tag


def test_fallback_on_exception():
    class Boom(FakeSession):
        def request(self, *a, **k):
            raise RuntimeError("socket gone")

    b = DockerBackend()
    b._session = Boom({})
    assert b.ensure_image_alias(TARGET, SOURCE) == SOURCE  # exception → fall back, no raise


def test_worker_create_spec_uses_worker_image():
    """The create payload's Image field is whatever Runnable.image carries (the worker image name),
    and the worker keeps its vexa-worker-* name + role/kind labels."""
    routes = {
        ("POST", "/containers/create"): FakeResp(201, body={"Id": "cid123"}),
        ("POST", "/containers/cid123/start"): FakeResp(204),
    }
    b, sess = _backend(routes)
    captured = {}
    orig = sess.request

    def spy(method, url, **kw):
        if method == "POST" and "/containers/create" in url:
            captured.update(kw.get("json", {}))
        return orig(method, url, **kw)

    sess.request = spy
    h = b.start(
        "agent-foo-chat",
        Runnable(image=TARGET, command=["python", "-m", "agent_api.worker"]),
        {"VEXA_X": "y"},
    )
    assert captured["Image"] == TARGET
    assert captured["Labels"]["vexa.role"] == "worker"
    assert captured["Labels"]["vexa.kind"] == "chat"
    assert captured["Labels"]["runtime.workload_id"] == "agent-foo-chat"  # workload id unchanged
    assert h._impl == "vexa-worker-foo-chat"  # cosmetic container name preserved


def test_worker_create_spec_injects_anthropic_route_env(monkeypatch):
    routes = {
        ("POST", "/containers/create"): FakeResp(201, body={"Id": "cid123"}),
        ("POST", "/containers/cid123/start"): FakeResp(204),
    }
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setenv("ANTHROPIC_MODEL", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "deepseek/deepseek-v4-flash")
    b, sess = _backend(routes)
    captured = {}
    orig = sess.request

    def spy(method, url, **kw):
        if method == "POST" and "/containers/create" in url:
            captured.update(kw.get("json", {}))
        return orig(method, url, **kw)

    sess.request = spy
    b.start(
        "agent-foo-chat",
        Runnable(image=TARGET, command=["python", "-m", "agent_api.worker"]),
        {"ANTHROPIC_AUTH_TOKEN": "dispatch-wins"},
    )
    env = dict(item.split("=", 1) for item in captured["Env"])
    assert env["ANTHROPIC_AUTH_TOKEN"] == "dispatch-wins"
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_MODEL"] == "deepseek/deepseek-v4-pro"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek/deepseek-v4-flash"
