"""/version — the deployment reports what it IS, or says it does not know.

The route exists because a frontend cannot honestly report a backend version it baked at its
own build time: dashboard.staging.vexa.ai showed "v0.12.18" for days while the cluster served
v0.12.22-rc.3. The only honest source is the running backend, asked at request time.

Two properties are load-bearing and both are asserted here:
  * the value is read from the environment ON EACH REQUEST — a redeploy that changes the env
    changes the answer without rebuilding anything;
  * an unset environment yields "unknown", never a constant. A stale number is worse than no
    number, because a reader acts on it.
"""
from fastapi.testclient import TestClient

from gateway import create_app
from conftest import FakeAuthorizer, FakeDownstream, FakeRedis


def _client():
    return TestClient(create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis()))


def test_version_reports_the_environment(monkeypatch):
    monkeypatch.setenv("VEXA_VERSION", "0.12.22-rc.3")
    monkeypatch.setenv("VEXA_REVISION", "0653de25")
    r = _client().get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "gateway"
    assert body["version"] == "0.12.22-rc.3"
    assert body["revision"] == "0653de25"


def test_version_says_unknown_rather_than_a_constant(monkeypatch):
    monkeypatch.delenv("VEXA_VERSION", raising=False)
    monkeypatch.delenv("VEXA_REVISION", raising=False)
    body = _client().get("/version").json()
    assert body["version"] == "unknown"
    assert body["revision"] == "unknown"


def test_version_reflects_a_changed_environment_without_a_rebuild(monkeypatch):
    """Read per request, not captured at import — the regression that made the badge lie."""
    client = _client()
    monkeypatch.setenv("VEXA_VERSION", "0.12.18")
    assert client.get("/version").json()["version"] == "0.12.18"
    monkeypatch.setenv("VEXA_VERSION", "0.12.22-rc.3")
    assert client.get("/version").json()["version"] == "0.12.22-rc.3"


def test_version_needs_no_api_key(monkeypatch):
    """Public like /health — a UI reports the version before anyone has signed in."""
    monkeypatch.setenv("VEXA_VERSION", "0.12.22")
    assert _client().get("/version").status_code == 200
