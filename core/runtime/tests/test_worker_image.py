"""Unit tests for DockerBackend.ensure_worker_image — the startup PULL that guarantees the dedicated
agent-worker image (core/agent/worker/Dockerfile) is present before any dispatch.

This replaced the pre-0.12.0 tag ALIAS that re-tagged agent-api bytes under the worker name: the
worker is a different build, so the alias made every published-images dispatch die with
``No module named worker``. The contract now: no-op when the image is local (compose-built :dev),
PULL when absent, and on pull failure return the worker name UNCHANGED so the spawn fails loudly
with 'No such image' — never silently running agent-api bytes.

These never touch a real daemon: the unix-socket session is faked so we can assert the exact
/images/create pull call. Also exercises the worker create spec using the worker image name."""
from __future__ import annotations

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


class PullingSession(FakeSession):
    """Stateful fake: the target image 404s until a /images/create pull is seen, then 200s —
    the shape of a real daemon around a successful pull."""

    def __init__(self, pull_status: int = 200):
        super().__init__({})
        self.pulled = False
        self.pull_status = pull_status

    def request(self, method, url, **kw):
        path = "/" + url.split("%2F", 1)[1].split("/", 1)[1] if "%2F" in url else url
        self.calls.append((method, path))
        if method == "POST" and path.startswith("/images/create"):
            if self.pull_status == 200:
                self.pulled = True
            return FakeResp(self.pull_status, "pull says no" if self.pull_status != 200 else "")
        if method == "GET" and path.startswith(f"/images/{TARGET}/json"):
            return FakeResp(200 if self.pulled else 404)
        return FakeResp(500, "no route")


def test_noop_when_worker_image_present():
    routes = {("GET", f"/images/{TARGET}/json"): FakeResp(200)}  # locally built (:dev path)
    b, sess = _backend(routes)
    assert b.ensure_worker_image(TARGET) == TARGET
    assert not any("/images/create" in p for (_m, p) in sess.calls)  # never pulls


def test_pulls_when_worker_image_missing():
    b = DockerBackend()
    sess = PullingSession()
    b._session = sess
    assert b.ensure_worker_image(TARGET) == TARGET
    pulls = [p for (m, p) in sess.calls if m == "POST" and p.startswith("/images/create")]
    # exactly one pull, of the WORKER repo+tag (percent-encoded query values)
    assert pulls == ["/images/create?fromImage=vexaai%2Fv012-agent-worker&tag=dev"]
    # never creates a tag alias of anything — the impostor-alias regression guard
    assert not any("/tag" in p for (_m, p) in sess.calls)


def test_pull_failure_keeps_worker_name_fail_visible():
    b = DockerBackend()
    b._session = PullingSession(pull_status=500)
    # the WORKER name comes back even though the pull failed: dispatch must fail with
    # 'No such image: …agent-worker…', NOT silently run agent-api bytes under the wrong name.
    assert b.ensure_worker_image(TARGET) == TARGET


def test_no_daemon_calls_for_empty_target():
    b, sess = _backend({})
    assert b.ensure_worker_image("") == ""
    assert sess.calls == []


def test_returns_target_on_exception():
    class Boom(FakeSession):
        def request(self, *a, **k):
            raise RuntimeError("socket gone")

    b = DockerBackend()
    b._session = Boom({})
    assert b.ensure_worker_image(TARGET) == TARGET  # never raises, keeps the truthful name


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
        Runnable(image=TARGET, command=["python", "-m", "worker"]),
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
        Runnable(image=TARGET, command=["python", "-m", "worker"]),
        {"ANTHROPIC_AUTH_TOKEN": "dispatch-wins"},
    )
    env = dict(item.split("=", 1) for item in captured["Env"])
    assert env["ANTHROPIC_AUTH_TOKEN"] == "dispatch-wins"
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_MODEL"] == "deepseek/deepseek-v4-pro"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek/deepseek-v4-flash"
