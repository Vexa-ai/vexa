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
