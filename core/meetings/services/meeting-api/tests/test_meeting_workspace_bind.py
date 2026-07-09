"""Bind a meeting to a shared workspace + authorize a MEMBER's live-transcript subscribe (Lane A).

Drives the collector create_app over the in-memory fake, OFFLINE (TestClient, no docker/DB):
  * POST /meetings/{platform}/{native}/workspace binds meetings.data.workspace_id (owner-scoped, 404 else);
  * a MEMBER of the bound workspace (x-user-workspaces) is authorized to subscribe — NOT just the owner;
  * a non-member is refused; a member of a DIFFERENT workspace is refused (the binding is the boundary).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

OWNER, MEMBER, STRANGER = 7, 8, 9
PLAT, NID = "google_meet", "abc-defg-hij"


def _client():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    return TestClient(create_app(store, redis=None)), mid


def test_bind_is_owner_scoped():
    client, _ = _client()
    ok = client.post(f"/meetings/{PLAT}/{NID}/workspace", json={"workspace_id": "team-notes"}, headers={"x-user-id": str(OWNER)})
    assert ok.status_code == 200 and ok.json()["workspace_id"] == "team-notes"
    # a non-owner cannot bind (their own row doesn't exist → 404)
    nope = client.post(f"/meetings/{PLAT}/{NID}/workspace", json={"workspace_id": "team-notes"}, headers={"x-user-id": str(STRANGER)})
    assert nope.status_code == 404


def _authz(client, uid, workspaces=None):
    headers = {"x-user-id": str(uid)}
    if workspaces is not None:
        headers["x-user-workspaces"] = ",".join(workspaces)
    r = client.post("/ws/authorize-subscribe",
                    json={"meetings": [{"platform": PLAT, "native_meeting_id": NID}]}, headers=headers)
    return r.json()


def test_member_of_bound_workspace_may_subscribe():
    client, mid = _client()
    client.post(f"/meetings/{PLAT}/{NID}/workspace", json={"workspace_id": "team-notes"}, headers={"x-user-id": str(OWNER)})

    # owner: authorized (branch a, unchanged)
    assert _authz(client, OWNER)["authorized"], "owner must always subscribe"
    # member of the bound workspace: authorized (branch b — the new capability)
    member = _authz(client, MEMBER, ["team-notes", "other-ws"])
    assert member["authorized"] and member["authorized"][0]["meeting_id"] == str(mid)
    # non-member (no workspaces): refused
    assert not _authz(client, STRANGER, [])["authorized"]
    # member of a DIFFERENT workspace than the binding: refused (binding is the boundary)
    assert not _authz(client, STRANGER, ["some-other-ws"])["authorized"]


def test_unbound_meeting_is_owner_only():
    client, _ = _client()  # never bound
    assert _authz(client, OWNER)["authorized"]                      # owner still fine
    assert not _authz(client, MEMBER, ["team-notes"])["authorized"] # nobody rides in without a binding
