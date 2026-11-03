"""A room's meeting has no personal owner (security gate 0.13.4, Low row "room bot owner").

A room's turn runs as its workspace principal, whose own account runs no bot, so the bot it asks for
is keyed to the account that pays for the room (`bills_to`, D2): row, concurrency count and spend.
That row then read as the PAYER'S OWN meeting to every personal credential of theirs — the owner
tier, minting a share link to anyone, rebinding it out of the room's workspace, deleting it, the
transcript search over "my meetings" — none of which a member of the room may do. The row now records
that it is the room's (`data.room_principal`), and the payer reaches it as a member of its workspace.

Offline: the shipped `request_bot` over the in-memory spawn fakes, and the shipped collector routes
over the in-memory transcript store.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.bot_spawn import request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

PAYER, OTHER = 7, 8
PLAT, NID = "google_meet", "abc-defg-hij"
ROOM = {"principal": "900", "principal_workspace": "ws_1"}
SHARE = {"mode": "restricted", "allowed_emails": ["anyone@example.test"]}
KW = dict(redis_url="r", token_secret="test-admin-token", meeting_api_url="http://meeting-api:8080")


async def test_a_room_s_bot_records_that_it_is_the_room_s():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    row = await request_bot(repo, runtime, user_id=PAYER, platform=PLAT, native_meeting_id=NID,
                            workspace_id="ws_1", room_principal=ROOM, **KW)
    assert row["data"]["room_principal"] == ROOM
    assert row["data"]["workspace_id"] == "ws_1"


async def test_a_room_s_bot_never_reopens_the_payer_s_own_meeting():
    """continue_meeting reopens the latest terminal row of (account, platform, meeting): a room's
    turn must not reopen the payer's private one into the room, nor the payer the room's."""
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    mine = await request_bot(repo, runtime, user_id=PAYER, platform=PLAT, native_meeting_id=NID, **KW)
    repo.set_status(mine["id"], "completed")
    room = await request_bot(repo, runtime, user_id=PAYER, platform=PLAT, native_meeting_id=NID,
                             workspace_id="ws_1", room_principal=ROOM, continue_meeting=True, **KW)
    assert room["id"] != mine["id"] and "room_principal" not in (mine.get("data") or {})
    repo.set_status(room["id"], "completed")
    again = await request_bot(repo, runtime, user_id=PAYER, platform=PLAT, native_meeting_id=NID,
                              continue_meeting=True, **KW)
    assert again["id"] != room["id"]


def _h(uid, *workspaces):
    return {"x-user-id": str(uid), "x-user-workspaces": ",".join(workspaces)}


def _store():
    store = InMemoryTranscriptStore()
    room = store.seed_meeting(user_id=PAYER, platform=PLAT, native_meeting_id=NID, meeting_id=100,
                              status="completed", data={"workspace_id": "ws_1", "room_principal": dict(ROOM)},
                              segments=[{"segment_id": "s1", "text": "the room's words", "speaker": "A"}])
    mine = store.seed_meeting(user_id=PAYER, platform=PLAT, native_meeting_id="xyz-abcd-efg", meeting_id=101,
                              status="completed", data={},
                              segments=[{"segment_id": "s2", "text": "my own words", "speaker": "A"}])
    return room, mine, TestClient(create_app(store, redis=None))


def test_the_payer_s_personal_credential_is_not_the_room_meeting_s_owner():
    room, _mine, c = _store()
    # no owner acts: a share link to anyone, its access settings, rebinding it out of the room, deleting it
    assert c.post(f"/meetings/{room}/share", json=SHARE, headers=_h(PAYER, "ws_1")).status_code == 404
    assert c.post(f"/meetings/{PLAT}/{NID}/share", json=SHARE,
                  headers=_h(PAYER, "ws_1")).status_code == 404
    assert c.patch(f"/meetings/{room}/access", json={"recording": True},
                   headers=_h(PAYER, "ws_1")).status_code == 404
    assert c.post(f"/meetings/{PLAT}/{NID}/workspace", json={"workspace_id": "personal"},
                  headers=_h(PAYER, "ws_1")).status_code in (403, 404)
    assert c.delete(f"/meetings/{PLAT}/{NID}", headers=_h(PAYER, "ws_1")).status_code == 404
    # it is listed as the room's, read at the member tier
    rows = {r["id"]: r for r in c.get("/meetings", headers=_h(PAYER, "ws_1")).json()["meetings"]}
    assert rows[room]["shared"] is True
    assert c.get(f"/transcripts/by-id/{room}", headers=_h(PAYER, "ws_1")).status_code == 200


def test_out_of_the_room_the_payer_reads_it_no_more():
    room, _mine, c = _store()
    assert c.get(f"/transcripts/by-id/{room}", headers=_h(PAYER)).status_code == 404
    assert room not in {r["id"] for r in c.get("/meetings", headers=_h(PAYER)).json()["meetings"]}


def test_the_payer_still_owns_their_own_meeting_and_a_member_still_reads_the_room_s():
    """The control: an ordinary meeting of the payer's is theirs, and another member reads the room's."""
    room, mine, c = _store()
    rows = {r["id"]: r for r in c.get("/meetings", headers=_h(PAYER)).json()["meetings"]}
    assert rows[mine]["shared"] is False
    assert c.post(f"/meetings/{mine}/share", json=SHARE, headers=_h(PAYER)).status_code == 200
    assert c.get(f"/transcripts/by-id/{room}", headers=_h(OTHER, "ws_1")).status_code == 200
