"""gate:access — the operator surface is not open by accident.

The mailroom has no public surface: it is not fronted by the gateway and its container port is
loopback-bound. What it does have is an ``/internal`` surface that can trigger a poll and read
every binding, so when an internal secret IS configured, default-deny is asserted here rather than
assumed. The open-when-unconfigured case is asserted too — a permission that depends on config
must have both halves pinned, or the config becomes the untested part.
"""
from fastapi.testclient import TestClient

from vexa_mailroom import create_app

SECRET = "internal-secret-under-test"


def test_internal_routes_deny_without_the_secret(mailroom, meetings):
    client = TestClient(create_app(mailroom, internal_secret=SECRET))
    for method, path in (("post", "/internal/poll"), ("get", "/internal/bindings"),
                         ("get", "/internal/notices")):
        r = getattr(client, method)(path)
        assert r.status_code == 401, path
    assert meetings.calls == []          # a denied poll must not have run


def test_internal_routes_deny_a_wrong_secret(mailroom):
    client = TestClient(create_app(mailroom, internal_secret=SECRET))
    r = client.post("/internal/poll", headers={"X-Internal-Secret": "nope"})
    assert r.status_code == 401


def test_internal_routes_allow_the_configured_secret(mailroom, source):
    from conftest import envelope, read_ics
    source.add(envelope(read_ics("google-request-meet.ics"), message_id="<a>"))
    client = TestClient(create_app(mailroom, internal_secret=SECRET))
    r = client.post("/internal/poll", headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["counts"] == {"created": 1}
    bindings = client.get("/internal/bindings", headers={"X-Internal-Secret": SECRET}).json()
    assert bindings["bindings"][0]["uid"] == "google-one-off-001@google.com"


def test_health_is_reachable_even_when_internal_is_locked(mailroom):
    client = TestClient(create_app(mailroom, internal_secret=SECRET))
    assert client.get("/health").status_code == 200


def test_unconfigured_secret_leaves_the_loopback_surface_open(mailroom):
    """Documented, not accidental: with no secret the dev container's routes answer."""
    client = TestClient(create_app(mailroom))
    assert client.get("/internal/bindings").status_code == 200


def test_no_mailroom_reports_unavailable_rather_than_pretending(mailroom):
    client = TestClient(create_app(None, ready=False, reason="unset: MAILROOM_API_KEY"))
    assert client.post("/internal/poll").status_code == 503
