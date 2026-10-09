"""The runtime's workload and schedule surfaces answer only the control plane.

Every route except /health requires the runtime caller credential (RUNTIME_API_TOKEN as a bearer),
and a runtime with no usable credential refuses to boot instead of serving the API open.
"""
import pytest
from fastapi.testclient import TestClient

from runtime_kernel import FakeClock, Runtime, Scheduler
from runtime_kernel.api import create_app
from runtime_kernel.caller_auth import CallerTokenError, load_caller_token

from _caller import TOKEN, caller_client

GUARDED = [
    ("POST", "/workloads", {"workloadId": "w1", "profile": "test", "env": {}}),
    ("GET", "/workloads", None),
    ("GET", "/workloads/w1", None),
    ("POST", "/workloads/w1/stop", {}),
    ("DELETE", "/workloads/w1", None),
    ("POST", "/schedule", {"execute_at": "2030-01-01T00:00:00Z", "request": {"url": "http://x/"}}),
    ("GET", "/schedule", None),
    ("DELETE", "/schedule/j1", None),
]


def _app():
    import fakeredis

    sched = Scheduler(fakeredis.FakeRedis(decode_responses=True), dispatch=lambda r: {"status_code": 200},
                      clock=FakeClock(start=0.0))
    return create_app(Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=1.0), scheduler=sched,
                      caller_token=TOKEN)


@pytest.mark.parametrize("method,path,body", GUARDED)
@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong-token-of-the-same-general-shape-000000"},
    {"Authorization": f"Basic {TOKEN}"},
    {"Authorization": TOKEN},
    {"X-Internal-Secret": TOKEN},
])
def test_every_control_route_refuses_a_caller_without_the_credential(method, path, body, headers):
    r = TestClient(_app()).request(method, path, json=body, headers=headers)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_nothing_is_spawned_on_a_refused_create():
    app = _app()
    TestClient(app).post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {}})
    assert app.state.runtime.store.get("w1") is None


def test_the_credential_opens_the_surface():
    client = caller_client(_app())
    r = client.post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {}})
    assert r.status_code == 201
    assert client.get("/workloads/w1").status_code == 200
    assert client.delete("/workloads/w1").status_code == 200


def test_health_stays_open_for_probes():
    assert TestClient(_app()).get("/health").status_code == 200


def test_an_app_without_a_credential_cannot_be_built():
    with pytest.raises(CallerTokenError):
        create_app(Runtime(profiles={}), caller_token="")


@pytest.mark.parametrize("value", ["", "   ", "changeme", "vexa-internal-secret", "short-but-not-empty"])
def test_boot_refuses_an_unusable_token(value):
    with pytest.raises(CallerTokenError):
        load_caller_token({"RUNTIME_API_TOKEN": value})


def test_boot_accepts_a_real_token():
    assert load_caller_token({"RUNTIME_API_TOKEN": TOKEN}) == TOKEN


def test_production_boot_refuses_without_a_token(monkeypatch):
    from runtime_kernel.__main__ import build_production_app
    from runtime_kernel.config_preflight import ConfigError

    monkeypatch.setenv("RUNTIME_BACKEND", "process")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AGENT_IMAGE", raising=False)
    monkeypatch.delenv("RUNTIME_API_TOKEN", raising=False)
    with pytest.raises((ConfigError, CallerTokenError)):
        build_production_app()
    monkeypatch.setenv("RUNTIME_API_TOKEN", "too-short")
    with pytest.raises(CallerTokenError):
        build_production_app()
