"""#579 — native-id-keyed api.v1 continuity on the meeting-api collector.

A 0.10 api.v1 client (incl. the shipped 0.10 dashboard) addresses meetings by
``(platform, native_meeting_id)``. 0.12 re-keyed the mutate routes to numeric row-id, so those
native paths 404'd. These tests drive the SHIPPED collector handlers (offline, in-memory fake)
proving the restored native-keyed surface:

  * PATCH /meetings/{platform}/{native} — resolves native → newest OWNED row → 200 (rename);
    unknown native → 404; FSM-owned row → 409; a shared (non-owned) row → 404 (never mutable).
  * DELETE /meetings/{platform}/{native} — 200 + row gone; unknown → 404.
  * GET /bots/status — carries BOTH `running` and `running_bots` (sealed golden field), same list.
  * GET /bots/{platform}/{native}/chat — owner boundary real (unowned → 404); honest empty list.

Negative control for the acceptance table: the same requests on current v0.12.2 (no native route)
return 404 — these tests are the green half of that red→green pair.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
H = {"x-user-id": str(USER)}
URL = "https://meet.google.com/abc-defg-hij"
PLAT, NATIVE = "google_meet", "abc-defg-hij"


class _CaptureRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, data):
        self.published.append((channel, data))


def _client():
    store = InMemoryTranscriptStore()
    return TestClient(create_app(store, redis=_CaptureRedis())), store


# ---- C1: native PATCH ---------------------------------------------------------------

def test_native_patch_renames_owned_meeting_200():
    client, _store = _client()
    # a planned meeting created from a link carries (platform, native)
    client.post("/meetings", json={"title": "old", "meeting_url": URL}, headers=H)
    r = client.patch(f"/meetings/{PLAT}/{NATIVE}", json={"title": "new"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["title"] == "new"


def test_native_patch_resolves_to_newest_row():
    """Several rows on the SAME native link → the native path addresses the NEWEST (spawn-dedup rule)."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, status="idle",
                       created_at="2026-01-01T00:00:00Z")
    newest = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, status="idle",
                                created_at="2026-06-01T00:00:00Z")
    r = client.patch(f"/meetings/{PLAT}/{NATIVE}", json={"title": "hit-newest"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == newest


def test_native_patch_unknown_native_404():
    client, _store = _client()
    r = client.patch(f"/meetings/{PLAT}/nope-nope-nope", json={"title": "x"}, headers=H)
    assert r.status_code == 404


def test_native_patch_fsm_row_409():
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, status="active")
    r = client.patch(f"/meetings/{PLAT}/{NATIVE}", json={"title": "nope"}, headers=H)
    assert r.status_code == 409


def test_native_patch_shared_row_not_owned_404():
    """A row owned by another user but visible to the caller via a workspace share is NEVER mutable
    through the native path — owner-scoped resolution excludes `shared` rows."""
    client, store = _client()
    store.seed_meeting(user_id=999, platform=PLAT, native_meeting_id=NATIVE, status="idle",
                       data={"workspace_id": "ws-1"})
    r = client.patch(f"/meetings/{PLAT}/{NATIVE}", json={"title": "steal"},
                     headers={"x-user-id": str(USER), "x-user-workspaces": "ws-1"})
    assert r.status_code == 404


# ---- C1: native DELETE --------------------------------------------------------------

def test_native_delete_owned_meeting_200_and_gone():
    client, store = _client()
    mid = client.post("/meetings", json={"title": "x", "meeting_url": URL}, headers=H).json()["id"]
    r = client.delete(f"/meetings/{PLAT}/{NATIVE}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == mid
    assert mid not in store._meetings


def test_native_delete_unknown_native_404():
    client, _store = _client()
    assert client.delete(f"/meetings/{PLAT}/{NATIVE}", headers=H).status_code == 404


# ---- row-id routes still work (no regression) ---------------------------------------

def test_row_id_patch_still_200():
    client, _store = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    assert client.patch(f"/meetings/{mid}", json={"title": "y"}, headers=H).status_code == 200


def test_row_id_delete_still_204():
    client, _store = _client()
    mid = client.post("/meetings", json={"title": "x"}, headers=H).json()["id"]
    assert client.delete(f"/meetings/{mid}", headers=H).status_code == 204


# ---- C2: /bots/status running_bots back-compat --------------------------------------

def test_bots_status_carries_running_and_running_bots():
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, status="active")
    body = client.get("/bots/status", headers=H).json()
    assert "running" in body and "running_bots" in body
    assert body["running_bots"] == body["running"]
    assert len(body["running_bots"]) == 1
    assert body["count"] == 1


# ---- C3: native chat READ (honest empty; owner boundary real) -----------------------

def test_chat_read_owned_returns_empty_messages():
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, status="active")
    r = client.get(f"/bots/{PLAT}/{NATIVE}/chat", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"messages": []}


def test_chat_read_unowned_404():
    client, _store = _client()
    assert client.get(f"/bots/{PLAT}/{NATIVE}/chat", headers=H).status_code == 404
