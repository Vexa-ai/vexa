"""Putting a meeting into a shared workspace takes WRITE access there (deny tests, P20).

Binding (`data.workspace_id`) publishes the meeting to every member of the workspace — list, page,
live transcript, recording if allowed — so it is a write into that workspace. A member who may only
read it (viewer), or someone who is not a member at all, is refused on every path that binds: the
bind route, creating a planned meeting, patching one, and sending a bot. The write set arrives on
the signed identity as `x-user-writable-workspaces`; its absence is "may write nowhere".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

OWNER = 7
PLAT, NID = "google_meet", "abc-defg-hij"


def _headers(member=None, writable=None):
    h = {"x-user-id": str(OWNER)}
    if member is not None:
        h["x-user-workspaces"] = ",".join(member)
    if writable is not None:
        h["x-user-writable-workspaces"] = ",".join(writable)
    return h


VIEWER = _headers(member=["team"], writable=[])
NOT_A_MEMBER = _headers(member=["other"], writable=["other"])
NO_CLAIM = _headers(member=["team"])           # an identity minted before the claim existed
WRITER = _headers(member=["team"], writable=["team"])


def _client():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    return TestClient(create_app(store, redis=None)), store, mid


@pytest.mark.parametrize("who", [VIEWER, NOT_A_MEMBER, NO_CLAIM], ids=["viewer", "not-a-member", "no-claim"])
def test_the_bind_route_refuses_a_caller_who_cannot_write(who):
    client, store, mid = _client()
    r = client.post(f"/meetings/{PLAT}/{NID}/workspace", json={"workspace_id": "team"}, headers=who)
    assert r.status_code == 403 and "edit access" in r.json()["detail"]
    assert "workspace_id" not in store._meetings[mid]["data"], "nothing was bound"


@pytest.mark.parametrize("who", [VIEWER, NOT_A_MEMBER, NO_CLAIM], ids=["viewer", "not-a-member", "no-claim"])
def test_planned_meetings_refuse_it_on_create_and_patch(who):
    client, store, _ = _client()
    assert client.post("/meetings", json={"title": "x", "workspace_id": "team"}, headers=who).status_code == 403
    mid = client.post("/meetings", json={"title": "y"}, headers=who).json()["id"]
    assert client.patch(f"/meetings/{mid}", json={"workspace_id": "team"}, headers=who).status_code == 403
    assert "workspace_id" not in (store._meetings[mid]["data"] or {})


def test_a_writer_binds_and_anyone_may_unbind_their_own():
    client, store, mid = _client()
    assert client.post(f"/meetings/{PLAT}/{NID}/workspace", json={"workspace_id": "team"},
                       headers=WRITER).status_code == 200
    pid = client.post("/meetings", json={"title": "z", "workspace_id": "team"}, headers=WRITER).json()["id"]
    # unbinding takes nothing away from the workspace's members they did not already lose
    assert client.patch(f"/meetings/{pid}", json={"workspace_id": None}, headers=VIEWER).status_code == 200


def test_an_unwatched_worker_cannot_bind_even_where_it_could_write():
    """R1801-8: binding is a person's act. A delegated worker with nobody in the loop is refused on
    planned create and patch, whatever its write set says; a human-regime worker is not."""
    unwatched = {**WRITER, "x-user-regime": "autonomous", "x-user-delegation-workspaces": "team"}
    human = {**WRITER, "x-user-regime": "human", "x-user-delegation-workspaces": "*"}
    client, store, _ = _client()
    r = client.post("/meetings", json={"title": "x", "workspace_id": "team"}, headers=unwatched)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required"
    mid = client.post("/meetings", json={"title": "y"}, headers=unwatched).json()["id"]
    assert client.patch(f"/meetings/{mid}", json={"workspace_id": "team"}, headers=unwatched).status_code == 403
    assert client.patch(f"/meetings/{mid}", json={"workspace_id": "team"}, headers=human).status_code == 200
