"""A shared chat reads the transcript of a meeting its members share (#1783, founder's call 2026-10-14).

The meeting was sent INTO a shared workspace both people are members of, and the agent in their
shared chat still refused to read its transcript: every meetings route refused a shared turn. One
meeting's transcript is now read when the meeting is bound to a workspace inside the shared turn's
ceiling (the workspaces every person in the chat reads); a private meeting, or one bound to a
workspace outside the ceiling, stays refused.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from meeting_api.collector.app import build_router
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.identity_token import SHARED_REFUSAL
from meeting_api.regime import refuse_shared

OWNER, OTHER = 7, 8
PLAT, NID = "google_meet", "ojr-rviw-pqz"


class _NullRedis:
    async def publish(self, channel, data):
        return None


def _client(workspace):
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID, meeting_id=100,
                       status="active", data=({"workspace_id": workspace} if workspace else {}),
                       segments=[{"segment_id": "s1", "start": 0.0, "end": 2.0, "text": "budget is 40k",
                                  "speaker": "A", "language": "en"}])
    app = FastAPI(dependencies=[Depends(refuse_shared)])
    app.include_router(build_router(store, _NullRedis()))
    return TestClient(app)


def _shared(user, ceiling, member_of=("ws_1",)):
    # what the identity door writes from a signed `delegation.shared` (no door on this bare app)
    return {"x-user-id": str(user), "x-user-workspaces": ",".join(member_of), "x-user-regime": "human",
            "x-user-delegation-shared": "1", "x-user-delegation-workspaces": ceiling}


def test_a_shared_chat_reads_a_meeting_bound_to_a_workspace_everyone_in_it_reads():
    c = _client("ws_1")
    for user in (OWNER, OTHER):
        r = c.get("/transcripts/by-id/100", headers=_shared(user, "ws_1"))
        assert r.status_code == 200, r.text
        assert r.json()["segments"][0]["text"] == "budget is 40k"
    r = c.get(f"/transcripts/{PLAT}/{NID}", headers=_shared(OWNER, "ws_1"))
    assert r.status_code == 200, r.text


def test_a_private_meeting_stays_refused_in_a_shared_chat():
    c = _client(None)
    r = c.get("/transcripts/by-id/100", headers=_shared(OWNER, "ws_1"))
    assert r.status_code == 403 and r.json()["detail"] == SHARED_REFUSAL, r.text
    r = c.get(f"/transcripts/{PLAT}/{NID}", headers=_shared(OWNER, "ws_1"))
    assert r.status_code == 403 and r.json()["detail"] == SHARED_REFUSAL, r.text


def test_a_meeting_in_a_workspace_outside_the_shared_ceiling_stays_refused():
    c = _client("ws_1")
    r = c.get("/transcripts/by-id/100", headers=_shared(OWNER, "ws_2"))
    assert r.status_code == 403 and r.json()["detail"] == SHARED_REFUSAL, r.text


def test_search_and_lists_stay_refused_in_a_shared_chat():
    c = _client("ws_1")
    assert c.get("/transcripts/search?q=budget", headers=_shared(OWNER, "ws_1")).status_code == 403
    assert c.get("/meetings", headers=_shared(OWNER, "ws_1")).status_code == 403


# ── by the meeting link, as a member (the call's friction: "Meeting not found") ──────────────────

def test_a_member_reads_the_meeting_by_its_link():
    c = _client("ws_1")
    r = c.get(f"/transcripts/{PLAT}/{NID}", headers={"x-user-id": str(OTHER), "x-user-workspaces": "ws_1"})
    assert r.status_code == 200, r.text
    assert r.json()["segments"][0]["text"] == "budget is 40k"
    # and from the shared chat both people are in
    r = c.get(f"/transcripts/{PLAT}/{NID}", headers=_shared(OTHER, "ws_1"))
    assert r.status_code == 200, r.text


def test_someone_outside_the_workspace_does_not_find_it_by_its_link():
    c = _client("ws_1")
    r = c.get(f"/transcripts/{PLAT}/{NID}", headers={"x-user-id": str(OTHER), "x-user-workspaces": "ws_9"})
    assert r.status_code == 404, r.text
    c2 = _client(None)
    r = c2.get(f"/transcripts/{PLAT}/{NID}", headers={"x-user-id": str(OTHER), "x-user-workspaces": "ws_1"})
    assert r.status_code == 404, r.text
