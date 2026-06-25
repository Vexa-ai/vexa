"""PUT /meetings/{platform}/{native_meeting_id}/intent — the USER-owned INTENT phase.

The user dropdown is the source of truth for the pre-FSM states `idle` / `scheduled`. The endpoint
writes meetings.status to `idle`|`scheduled` ONLY and rejects (422) any FSM-owned value, so the
dropdown can never bypass the bot lifecycle FSM. On a genuine change it publishes a FLAT
`meeting.status` frame to the user-scoped redis channel `u:{user_id}:meetings`.

Drives the collector ``create_app`` over the in-memory fake, OFFLINE (TestClient, no docker/DB).
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
H = {"x-user-id": str(USER)}
PLAT, NID = "google_meet", "abc-defg-hij"
AT = "2026-06-25T18:00:00Z"


class _CaptureRedis:
    """Minimal RedisBus stub — records every publish for assertions."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, data):
        self.published.append((channel, data))


def _client(status="idle"):
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NID, status=status)
    redis = _CaptureRedis()
    return TestClient(create_app(store, redis=redis)), store, redis, mid


# ---- accepts the two intent states -------------------------------------------------

def test_intent_idle_accepted_and_persisted():
    client, store, _redis, mid = _client(status="scheduled")
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "idle"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"meeting_id": mid, "status": "idle", "scheduled_at": None}
    assert store._meetings[mid]["status"] == "idle"
    # scheduled_at is cleared when going idle
    assert "scheduled_at" not in store._meetings[mid]["data"]


def test_intent_scheduled_stores_at():
    client, store, _redis, mid = _client(status="idle")
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "scheduled", "at": AT}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"meeting_id": mid, "status": "scheduled", "scheduled_at": AT}
    assert store._meetings[mid]["status"] == "scheduled"
    assert store._meetings[mid]["data"]["scheduled_at"] == AT


# ---- rejects FSM-owned values (422) ------------------------------------------------

def test_intent_rejects_fsm_owned_values():
    client, store, _redis, mid = _client(status="idle")
    for bad in ("requested", "joining", "awaiting_admission", "needs_help",
                "active", "stopping", "completed", "failed"):
        r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": bad}, headers=H)
        assert r.status_code == 422, f"{bad}: {r.text}"
        # the row is untouched — the FSM still owns it
        assert store._meetings[mid]["status"] == "idle"


def test_intent_rejects_unknown_value():
    client, _store, _redis, _mid = _client()
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "bogus"}, headers=H)
    assert r.status_code == 422


def test_intent_scheduled_requires_at():
    client, _store, _redis, _mid = _client()
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "scheduled"}, headers=H)
    assert r.status_code == 422


def test_intent_requires_intent_field():
    client, _store, _redis, _mid = _client()
    assert client.put(f"/meetings/{PLAT}/{NID}/intent", json={}, headers=H).status_code == 422


# ---- owner scoping / identity ------------------------------------------------------

def test_intent_owner_scoped_404():
    client, _store, _redis, _mid = _client()
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "idle"},
                   headers={"x-user-id": "999"})
    assert r.status_code == 404


def test_intent_requires_user_identity():
    client, _store, _redis, _mid = _client()
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "idle"})
    assert r.status_code == 401


# ---- publishes the user-channel frame on change ------------------------------------

def test_intent_publishes_user_channel_frame():
    client, _store, redis, mid = _client(status="idle")
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "scheduled", "at": AT}, headers=H)
    assert r.status_code == 200
    assert len(redis.published) == 1
    channel, raw = redis.published[0]
    assert channel == f"u:{USER}:meetings"
    frame = json.loads(raw)
    assert frame == {
        "type": "meeting.status",
        "meeting_id": mid,
        "native": NID,
        "status": "scheduled",
        "when": AT,
    }


def test_intent_idempotent_noop_does_not_publish():
    client, _store, redis, _mid = _client(status="idle")
    r = client.put(f"/meetings/{PLAT}/{NID}/intent", json={"intent": "idle"}, headers=H)
    assert r.status_code == 200
    assert redis.published == []  # PUT to the current state is a no-op → no fan-out
