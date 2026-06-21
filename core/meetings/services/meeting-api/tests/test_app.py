"""The unified meeting-api app — ``create_app`` composes the front-doored modules onto ONE app.

Proves the modular-monolith assembly (P2): the single ``create_app`` mounts lifecycle + bot_spawn +
collector + recordings onto one FastAPI app, answers the shared ``/health``, and each module's core
route is reachable on that one app (driven over the default in-memory stack — no DB / redis / MinIO /
runtime kernel).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
HEADERS = {"x-user-id": str(USER)}


def test_create_app_health():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "meeting-api"


def test_unified_app_mounts_every_module_route():
    """Every module's core route is reachable on the ONE app (the routing table is composed)."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij")
    client = TestClient(create_app(transcript_store=store))

    # Each module's core route is MOUNTED (resolves to a handler — never 404). One app, one router
    # table: lifecycle + bot_spawn + collector + recordings.
    mounted = [
        ("POST", "/bots", {}),                                 # bot_spawn
        ("POST", "/bots/internal/callback/lifecycle", {"foo": "bar"}),  # lifecycle
        ("GET", "/transcripts/google_meet/abc-defg-hij", None),  # collector
        ("GET", "/meetings", None),                            # collector
        ("POST", "/ws/authorize-subscribe", {}),               # collector
        ("GET", "/recordings", None),                          # recordings
    ]
    for method, path, body in mounted:
        r = client.request(method, path, json=body)
        assert r.status_code != 404, f"{method} {path} not mounted"
    # The recordings upload route is multipart — assert it is mounted (415/422, not 404).
    assert client.post("/internal/recordings/upload").status_code != 404

    # And a collector route actually serves data through the unified app.
    r = client.get("/meetings", headers=HEADERS)
    assert r.status_code == 200
    assert any(m["native_meeting_id"] == "abc-defg-hij" for m in r.json()["meetings"])


def test_post_bots_on_unified_app(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    client = TestClient(create_app())
    r = client.post("/bots", headers=HEADERS,
                    json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "requested"


def test_lifecycle_callback_on_unified_app():
    """The lifecycle receiver's callback advances the FSM on the shared app + store."""
    import json
    from pathlib import Path

    # Load a lifecycle.v1 golden by path (the seam).
    for parent in Path(__file__).resolve().parents:
        gdir = parent / "meetings" / "contracts" / "lifecycle.v1" / "golden"
        if gdir.is_dir():
            break
    events = sorted(gdir.glob("LifecycleEvent.*.json"))
    assert events, "expected lifecycle.v1 goldens"
    event = json.loads(events[0].read_text())

    client = TestClient(create_app())
    r = client.post("/bots/internal/callback/lifecycle", json=event)
    assert r.status_code in (200, 409), r.text  # accepted, or a legal-transition rejection
