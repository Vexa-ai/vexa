"""O-RT-2 /health eval — the endpoint returns 200 when backend + store (+ any extra probes) are
reachable, and 503 when any probe reports unreachable."""
from fastapi.testclient import TestClient

from runtime_kernel import Runtime
from runtime_kernel.api import create_app


def test_health_ok_when_dependencies_reachable():
    app = create_app(Runtime(profiles={"test": ["sleep", "30"]}))
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["backend"] is True
    assert body["checks"]["store"] is True


def test_health_503_when_a_probe_fails():
    # Wire an extra probe (e.g. scheduler liveness) that reports down.
    app = create_app(
        Runtime(profiles={"test": ["sleep", "30"]}),
        health_checks={"scheduler": lambda: False},
    )
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["scheduler"] is False
    assert body["checks"]["backend"] is True  # other probes still reported


def test_health_503_when_store_unreachable():
    rt = Runtime(profiles={"test": ["sleep", "30"]})

    class BrokenStore:
        def list(self):
            raise ConnectionError("redis down")

        # the rest of the port is unused by the health probe
        def set(self, *a, **k): ...
        def get(self, *a, **k): ...
        def delete(self, *a, **k): ...
        def count_for_owner(self, *a, **k): return 0

    rt.store = BrokenStore()
    client = TestClient(create_app(rt))
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["checks"]["store"] is False
