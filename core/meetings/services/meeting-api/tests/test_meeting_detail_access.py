"""GET /meetings/{id} authorizes by the SAME access union as the list it is opened from.

Owner, transcript-share viewer, or member of the bound workspace (``x-user-workspaces``, resolved by
the gateway). A meeting that surfaces in a caller's ``GET /meetings`` must open on its detail
route; a caller outside the union gets the same 404 as for a meeting that does not exist (#1736).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.projection import OWNER_ONLY_KEYS

OWNER, WS_MEMBER, OTHER_MEMBER, STRANGER = 51, 52, 53, 54
PLAT, NID = "google_meet", "abc-defg-hij"
HOOK = "https://hooks.example.com/owner"


def _client():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID,
                       data={"title": "Weekly sync", "workspace_id": "ws-7", "webhook_url": HOOK})
    return next(iter(store._meetings)), TestClient(create_app(store, redis=None))


def _get(client, mid, uid, workspaces=None):
    headers = {"x-user-id": str(uid)}
    if workspaces is not None:
        headers["x-user-workspaces"] = workspaces
    return client.get(f"/meetings/{mid}", headers=headers)


def test_workspace_member_opens_a_meeting_their_list_shows():
    mid, client = _client()
    hdrs = {"x-user-id": str(WS_MEMBER), "x-user-workspaces": "ws-7"}
    listed = [m["id"] for m in client.get("/meetings", headers=hdrs).json()["meetings"]]
    assert mid in listed, "precondition: the bound meeting surfaces in the member's list"

    r = _get(client, mid, WS_MEMBER, "ws-other, ws-7")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == mid
    data = r.json()["data"]
    assert data.get("title") == "Weekly sync"
    for key in OWNER_ONLY_KEYS:
        assert key not in data, f"owner-private {key} reached a workspace member"


def test_owner_still_gets_the_owner_view():
    mid, client = _client()
    r = _get(client, mid, OWNER)
    assert r.status_code == 200
    assert r.json()["data"].get("webhook_url") == HOOK


def test_member_of_an_unrelated_workspace_gets_404():
    mid, client = _client()
    assert _get(client, mid, OTHER_MEMBER, "ws-other").status_code == 404


def test_stranger_and_empty_membership_get_404():
    mid, client = _client()
    assert _get(client, mid, STRANGER).status_code == 404
    assert _get(client, mid, STRANGER, " , ").status_code == 404


def test_membership_never_widens_past_the_requested_id():
    mid, client = _client()
    assert _get(client, mid + 999, WS_MEMBER, "ws-7").status_code == 404
