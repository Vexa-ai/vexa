"""gate:health — the mailroom answers a conforming liveness /health.

Pure liveness (process-up): no auth, no mailbox hop, no control-plane hop. It also reports whether
ingest is CONFIGURED, because the failure this service will actually have in dev is "the poller
never ran" — a health check that says `ok` while the mailbox is unreachable-by-configuration is
the kind of green that hides a dead loop.
"""
from fastapi.testclient import TestClient

from vexa_mailroom import create_app


def test_health_ok(mailroom):
    client = TestClient(create_app(mailroom))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "mailroom"
    assert body["ingest"] == {"configured": True, "workspaces": ["mk-dev@dev.vexa.ai"]}


def test_health_reports_an_unconfigured_mailbox_instead_of_failing_to_boot():
    client = TestClient(create_app(None, ready=False, reason="unset: MAILROOM_API_KEY"))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["ingest"] == {"configured": False, "reason": "unset: MAILROOM_API_KEY"}


def test_health_needs_no_credential_and_makes_no_hop(mailroom, source, meetings):
    client = TestClient(create_app(mailroom))
    assert client.get("/health").status_code == 200
    assert source.fetches == []
    assert meetings.calls == []
