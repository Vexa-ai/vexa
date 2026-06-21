"""The collector's 3 HTTP routes, conforming to the SEALED api.v1 shapes (loaded BY PATH).

Drives ``create_app`` over the in-memory fakes, OFFLINE (TestClient, no docker, no DB):
  * GET /transcripts/{platform}/{native_meeting_id} → 200 + api.v1 TranscriptionResponse (owned),
    404 (not owned / not found);
  * GET /meetings → 200 + api.v1 MeetingListResponse (filtered by status/platform/limit/offset);
  * POST /ws/authorize-subscribe → the gateway /ws authorizer shape
    {authorized:[{platform,native_id,user_id,meeting_id}], errors:[]};
  * fail-closed: a request with no x-user-id (the header the gateway injects) → 401.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

from collector_contracts import assert_api_conforms

USER = 7
GATEWAY_HEADERS = {"x-user-id": str(USER)}


def _seeded():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="active", constructed_meeting_url="https://meet.google.com/abc-defg-hij",
        segments=[{
            "segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "This is Anna.",
            "language": "en", "speaker": "spk-Anna", "completed": True,
        }],
    )
    return store, mid


def test_get_transcript_conforms():
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.get("/transcripts/google_meet/abc-defg-hij", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert_api_conforms("TranscriptionResponse", body)
    assert body["id"] >= 1
    assert body["platform"] == "google_meet"
    assert [s["text"] for s in body["segments"]] == ["This is Anna."]


def test_get_transcript_not_found_is_404():
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.get("/transcripts/google_meet/does-not-exist", headers=GATEWAY_HEADERS)
    assert r.status_code == 404


def test_get_transcript_other_user_is_404():
    """OWNERSHIP: another user's key cannot read this meeting (not-found, not leaked)."""
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.get("/transcripts/google_meet/abc-defg-hij", headers={"x-user-id": "999"})
    assert r.status_code == 404


def test_get_meetings_conforms():
    store, _ = _seeded()
    store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="99887766",
                       status="completed", created_at="2026-06-20T10:00:00Z")
    client = TestClient(create_app(store, redis=None))
    r = client.get("/meetings", headers=GATEWAY_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert_api_conforms("MeetingListResponse", body)
    assert len(body["meetings"]) == 2
    # newest first
    assert body["meetings"][0]["platform"] == "zoom"


def test_get_meetings_filters():
    store, _ = _seeded()
    store.seed_meeting(user_id=USER, platform="zoom", native_meeting_id="99887766",
                       status="completed", created_at="2026-06-20T10:00:00Z")
    client = TestClient(create_app(store, redis=None))
    r = client.get("/meetings", headers=GATEWAY_HEADERS, params={"platform": "zoom"})
    body = r.json()
    assert_api_conforms("MeetingListResponse", body)
    assert [m["platform"] for m in body["meetings"]] == ["zoom"]

    r2 = client.get("/meetings", headers=GATEWAY_HEADERS, params={"limit": 1})
    assert len(r2.json()["meetings"]) == 1


def test_get_meetings_empty_for_other_user_conforms():
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.get("/meetings", headers={"x-user-id": "999"})
    assert r.status_code == 200
    body = r.json()
    assert_api_conforms("MeetingListResponse", body)
    assert body["meetings"] == []


def test_ws_authorize_subscribe_authorizes_owned_meeting():
    store, mid = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.post(
        "/ws/authorize-subscribe",
        headers=GATEWAY_HEADERS,
        json={"meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    assert len(body["authorized"]) == 1
    auth = body["authorized"][0]
    # The EXACT shape gateway _run_multiplex reads (platform/native_id/user_id/meeting_id).
    assert auth["platform"] == "google_meet"
    assert auth["native_id"] == "abc-defg-hij"
    assert auth["user_id"] == str(USER)
    assert auth["meeting_id"] == str(mid)


def test_ws_authorize_subscribe_rejects_unowned():
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.post(
        "/ws/authorize-subscribe",
        headers=GATEWAY_HEADERS,
        json={"meetings": [{"platform": "zoom", "native_meeting_id": "never-seen"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["authorized"] == []
    assert len(body["errors"]) == 1


def test_ws_authorize_subscribe_empty_list_is_422():
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    r = client.post("/ws/authorize-subscribe", headers=GATEWAY_HEADERS, json={"meetings": []})
    assert r.status_code == 422


def test_routes_fail_closed_without_user_identity():
    """AUTH-NEGATIVE: no x-user-id (the gateway-injected identity) → 401 on every client route."""
    store, _ = _seeded()
    client = TestClient(create_app(store, redis=None))
    assert client.get("/transcripts/google_meet/abc-defg-hij").status_code == 401
    assert client.get("/meetings").status_code == 401
    assert client.post(
        "/ws/authorize-subscribe",
        json={"meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}]},
    ).status_code == 401
