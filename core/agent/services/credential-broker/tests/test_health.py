"""gate:health — the broker answers a conforming liveness probe without an assertion, and a
readiness probe that says whether its credential store answers.

Liveness (`/health`) is the process: it stays 200 while the store is down, so an orchestrator does
not restart a broker for an outage a restart cannot fix. Readiness (`/ready`) is the dependency:
503 while the store does not answer, so traffic is not sent to a broker that would refuse every
credential operation.
"""
import json


def test_health_is_unauthenticated_and_conforming(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "credential-broker", "store": "local"}


def test_ready_answers_ok_while_the_store_answers(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "credential-broker", "store": "local"}
    assert r.headers.get("cache-control") == "no-store"


def test_ready_is_503_while_the_store_does_not_answer_and_liveness_stays_up(client, store, capsys):
    store.fail = "transport"
    capsys.readouterr()
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "service": "credential-broker", "store": "local"}
    faults = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"event":"broker_fault"' in line]
    assert faults and faults[-1]["fields"] == {"source": "store", "kind": "unhealthy", "route": "/ready"}
    assert client.get("/health").status_code == 200


def test_a_store_probe_that_raises_is_unready_not_a_crash(client, store, monkeypatch):
    def boom():
        raise RuntimeError("PRIVATE")
    monkeypatch.setattr(store, "healthy", boom)
    r = client.get("/ready")
    assert r.status_code == 503 and "PRIVATE" not in r.text


def test_everything_else_requires_an_assertion(client):
    for method, path in [("GET", "/api/connections"), ("POST", "/api/setup"), ("GET", "/health/extra"),
                         ("POST", "/ready"), ("GET", "/ready/extra")]:
        r = client.request(method, path)
        assert r.status_code == 401
        assert r.json() == {"detail": "Product identity refused"}
