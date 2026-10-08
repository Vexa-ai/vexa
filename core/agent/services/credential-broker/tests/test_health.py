"""gate:health — the broker answers a conforming liveness probe without an assertion."""


def test_health_is_unauthenticated_and_conforming(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "credential-broker", "store": "local"}


def test_everything_else_requires_an_assertion(client):
    for method, path in [("GET", "/api/connections"), ("POST", "/api/setup"), ("GET", "/health/extra")]:
        r = client.request(method, path)
        assert r.status_code == 401
        assert r.json() == {"detail": "Product identity refused"}
