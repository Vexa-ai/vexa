"""O-MTG-1 eval (HTTP) — drive the receiver over FastAPI TestClient.

Asserts: POST each lifecycle.v1 golden in legal order → 200 accepted, FSM advances; the
accepted events re-conform to the SEALED lifecycle.v1 schema (the `_conforms` pattern from
`runtime/tests/test_api.py`); an illegal transition → 409; a malformed event → 422;
/health is live (the gate:health receiver the orchestrator will point at).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.lifecycle import MeetingStore
from meeting_api.lifecycle.receiver import conforms, create_app

ENDPOINT = "/bots/internal/callback/lifecycle"


def _client() -> tuple[TestClient, MeetingStore]:
    store = MeetingStore()
    return TestClient(create_app(store=store)), store


def test_health():
    client, _ = _client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_full_lifecycle_over_http_conforms(goldens):
    """POST joining → active → completed; every posted golden conforms to the sealed schema."""
    client, store = _client()

    for case in ("joining", "active", "completed-stopped"):
        event = goldens[case]
        # The bot's emitted event re-conforms to lifecycle.v1 (seam re-validation).
        conforms(event, "LifecycleEvent")
        r = client.post(ENDPOINT, json=event)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "accepted"

    body = client.post(ENDPOINT, json={"connection_id": "x", "status": "joining"}).json()
    assert body["status"] == "accepted"

    rec = store.get("sess-uid")
    assert rec.status.value == "completed"
    assert rec.completion_reason.value == "stopped"


def test_failed_join_over_http(goldens):
    client, store = _client()
    client.post(ENDPOINT, json=goldens["joining"])
    r = client.post(ENDPOINT, json=goldens["failed-join"])
    assert r.status_code == 200
    body = r.json()
    assert body["meeting_status"] == "failed"
    assert body["failure_stage"] == "joining"  # server-derived, not the payload value
    assert body["completion_reason"] == "awaiting_admission_rejected"


def test_illegal_transition_is_409(goldens):
    client, _ = _client()
    client.post(ENDPOINT, json=goldens["joining"])
    client.post(ENDPOINT, json=goldens["active"])
    r = client.post(ENDPOINT, json=goldens["joining"])  # active → joining
    assert r.status_code == 409
    body = r.json()
    assert body["status"] == "error"
    assert body["from"] == "active" and body["to"] == "joining"


def test_malformed_event_is_422():
    client, _ = _client()
    # Missing required `status` → fails lifecycle.v1 schema at the seam.
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid"})
    assert r.status_code == 422
    assert "schema violation" in r.json()["detail"]

    # Unknown status enum value → also a schema violation.
    r = client.post(ENDPOINT, json={"connection_id": "sess-uid", "status": "bogus"})
    assert r.status_code == 422


def test_accepted_responses_independent_per_connection(goldens):
    """Two connection_ids advance independently in the same store."""
    client, store = _client()
    client.post(ENDPOINT, json={"connection_id": "a", "status": "joining"})
    client.post(ENDPOINT, json={"connection_id": "b", "status": "joining"})
    client.post(ENDPOINT, json={"connection_id": "a", "status": "active"})
    assert store.get("a").status.value == "active"
    assert store.get("b").status.value == "joining"
