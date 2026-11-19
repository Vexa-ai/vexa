"""The runtime's workload and schedule surfaces answer only the control plane.

Every route except /health requires the runtime caller credential (RUNTIME_API_TOKEN as a bearer),
and a runtime with no usable credential refuses to boot instead of serving the API open.
"""
import time

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


def test_every_value_the_declaration_forbids_is_refused_as_published():
    """The declaration's forbidden_values is the one list: each is refused by name, not by length."""
    from runtime_kernel import config_preflight as cp

    (entry,) = [k for k in cp.load_declaration()["keys"] if k["key"] == "RUNTIME_API_TOKEN"]
    assert entry["forbidden_values"]
    for value in entry["forbidden_values"]:
        with pytest.raises(CallerTokenError, match="published"):
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


# ── callbacks are signed ────────────────────────────────────────────────────────────────────────

#: The same vector meeting-api's verifier is pinned against (tests/test_runtime_callback_signature.py).
VECTOR_TOKEN = "runtime-caller-token-for-tests-0123456789abcdef"
VECTOR_EVENT = {"workloadId": "mtg-1-abcdef12", "state": "stopped", "at": "2026-10-09T00:00:00+00:00",
                "exitCode": 0, "stopReason": "completed"}
VECTOR_URL = "http://meeting-api:8080/runtime/callback"
VECTOR_TIMESTAMP = 1791504000
VECTOR_SIGNATURE = "t=1791504000,v2=f1ed1283cda387b2dbe249719eb9546925955eba96f7829baee866ab264c41c0"


def test_the_callback_signature_matches_the_shared_vector():
    from runtime_kernel.caller_auth import sign_callback

    assert sign_callback(VECTOR_TOKEN, VECTOR_EVENT, VECTOR_URL, VECTOR_TIMESTAMP) == VECTOR_SIGNATURE


def test_the_signature_covers_the_time_the_url_and_the_event():
    """The signature binds when and where it was sent, not only what: the same event signed for
    another URL, or at another time, is another signature."""
    from runtime_kernel.caller_auth import sign_callback

    base = sign_callback(VECTOR_TOKEN, VECTOR_EVENT, VECTOR_URL, VECTOR_TIMESTAMP)
    assert sign_callback(VECTOR_TOKEN, {**VECTOR_EVENT, "state": "running"}, VECTOR_URL, VECTOR_TIMESTAMP) != base
    assert sign_callback(VECTOR_TOKEN, VECTOR_EVENT, "http://other:8080/runtime/callback", VECTOR_TIMESTAMP) != base
    assert sign_callback(VECTOR_TOKEN, VECTOR_EVENT, VECTOR_URL, VECTOR_TIMESTAMP + 1) != base


def test_every_callback_carries_the_runtimes_signature_and_never_its_token():
    from runtime_kernel.callbacks import CallbackQueue
    from runtime_kernel.caller_auth import SIGNATURE_HEADER, sign_callback
    from runtime_kernel.kernel import Runtime

    posted = []
    queue = CallbackQueue(poster=lambda url, body, headers: posted.append((url, body, headers)) or 200)
    rt = Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=1.0)
    client = caller_client(create_app(rt, callback_queue=queue, caller_token=TOKEN))
    assert client.post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {},
                                           "callbackUrl": "http://meeting-api:8080/runtime/callback"}).status_code == 201
    client.delete("/workloads/w1")
    assert posted
    for url, body, headers in posted:
        ts = int(headers[SIGNATURE_HEADER].split(",")[0][2:])
        assert abs(ts - time.time()) < 60
        assert headers[SIGNATURE_HEADER] == sign_callback(TOKEN, body, url, ts)
        assert TOKEN not in str(headers) and TOKEN not in str(body)


def test_a_retried_callback_is_signed_when_it_is_sent():
    """A delivery the receiver did not ack is retried by sweep(); each attempt carries a signature
    made then, so a retry is not refused for an old timestamp."""
    from runtime_kernel.callbacks import CallbackQueue
    from runtime_kernel.caller_auth import SIGNATURE_HEADER

    sent, now = [], [1000]
    queue = CallbackQueue(poster=lambda url, body, headers: sent.append(headers[SIGNATURE_HEADER]) or 503)
    queue.signer = lambda url, event: {SIGNATURE_HEADER: f"t={now[0]},v2=x"}
    queue.enqueue("http://meeting-api:8080/runtime/callback", {"workloadId": "w", "state": "stopped"})
    now[0] = 2000
    queue.sweep()
    assert sent == ["t=1000,v2=x", "t=2000,v2=x"]
