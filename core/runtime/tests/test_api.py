"""Stage-2 (API) gate — drive the full runtime.v1 lifecycle OVER HTTP, assert the status responses +
the delivered RuntimeEvents all conform to the frozen contract."""
import json
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient
from referencing import Registry, Resource

from runtime_kernel import Runtime
from runtime_kernel.api import create_app

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts" / "runtime.v1" / "runtime.schema.json").read_text()
)
_REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))


def _conforms(obj: dict, shape: str) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


def test_lifecycle_over_http_conforms():
    events = []
    app = create_app(Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=3.0), deliver=events.append)
    client = TestClient(app)

    r = client.post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {}})
    assert r.status_code == 201
    _conforms(r.json(), "WorkloadStatus")

    assert client.get("/workloads/w1").json()["state"] == "running"
    assert any(s["workloadId"] == "w1" for s in client.get("/workloads").json())

    s = client.post("/workloads/w1/stop", json={"reason": "stopped"})
    assert s.status_code == 200 and s.json()["state"] == "stopped"
    _conforms(s.json(), "WorkloadStatus")

    d = client.delete("/workloads/w1")
    assert d.status_code == 200 and d.json()["state"] == "destroyed"

    # the API delivered the full legal lifecycle, every event conforming to runtime.v1
    assert [e.state.value for e in events] == ["starting", "running", "stopping", "stopped", "destroyed"]
    for e in events:
        _conforms(json.loads(e.model_dump_json(exclude_none=True)), "RuntimeEvent")


def test_unknown_profile_is_400_and_unknown_workload_404():
    client = TestClient(create_app(Runtime(profiles={})))
    assert client.post("/workloads", json={"workloadId": "x", "profile": "nope", "env": {}}).status_code == 400
    assert client.get("/workloads/missing").status_code == 404


def test_double_create_over_http_touches_not_respawns():
    """runtime.v1 idempotent create (ADR 0027): a second POST /workloads for a running workloadId
    returns its live status — no second spawn, no duplicate lifecycle events."""
    events = []
    app = create_app(Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=3.0), deliver=events.append)
    client = TestClient(app)
    try:
        first = client.post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {}})
        assert first.status_code == 201 and first.json()["state"] == "running"

        touched = client.post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {}})
        assert touched.status_code == 201 and touched.json()["state"] == "running"
        _conforms(touched.json(), "WorkloadStatus")

        # one spawn's worth of events — the touch emitted nothing new
        assert [e.state.value for e in events] == ["starting", "running"]
    finally:
        client.post("/workloads/w1/stop", json={"reason": "stopped"})
        client.delete("/workloads/w1")


def test_create_and_stop_wire_the_enforcer(client_factory=None):
    """WP-M8: the Enforcer only reaps workloads it was told about — the API's
    create/stop/destroy seams must register and forget. Before the wiring,
    create_app accepted no enforcer at all and every workload was invisible to
    the reaper (the immortal-bot incident)."""
    from runtime_kernel.enforcement import Enforcer

    runtime = Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=3.0)
    enforcer = Enforcer(runtime)
    client = TestClient(create_app(runtime, enforcer=enforcer))

    created = client.post("/workloads", json={"workloadId": "wl-enforce-1", "profile": "test", "env": {}})
    assert created.status_code == 201, created.text
    assert "wl-enforce-1" in enforcer._tracked
    stopped = client.post("/workloads/wl-enforce-1/stop", json={"reason": "stopped"})
    assert stopped.status_code == 200, stopped.text
    assert "wl-enforce-1" not in enforcer._tracked
