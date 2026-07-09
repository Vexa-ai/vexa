"""INDEPENDENT transcript share (M0) — share a meeting's live feed via a capability link, NO workspace.

Offline over the in-memory fake:
  * owner mints a share link (open) → a different authenticated user redeems → is authorized to subscribe;
  * a user who never redeemed is refused (no workspace, no grant);
  * restricted mode admits only an allow-listed verified email;
  * decoupled from workspaces entirely (no binding, no membership involved).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

OWNER, VISITOR, OTHER = 7, 8, 9
PLAT, NID = "google_meet", "abc-defg-hij"


def _client():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    return TestClient(create_app(store, redis=None))


def _authorized(client, uid):
    r = client.post("/ws/authorize-subscribe",
                    json={"meetings": [{"platform": PLAT, "native_meeting_id": NID}]},
                    headers={"x-user-id": str(uid)})
    return bool(r.json().get("authorized"))


def test_open_share_link_grants_subscribe_no_workspace():
    client = _client()
    minted = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"}, headers={"x-user-id": str(OWNER)})
    assert minted.status_code == 200
    token = minted.json()["token"]
    assert token.split(".")[0].isdigit()  # <meeting_id>.<secret>

    assert not _authorized(client, VISITOR)         # before redeem: no access
    ok = client.post("/transcripts/share/accept", json={"token": token}, headers={"x-user-id": str(VISITOR)})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert _authorized(client, VISITOR)             # after redeem: subscribe authorized — NO workspace involved
    assert not _authorized(client, OTHER)           # someone who never redeemed stays refused


def test_restricted_share_checks_verified_email():
    client = _client()
    minted = client.post(f"/meetings/{PLAT}/{NID}/share",
                         json={"mode": "restricted", "allowed_emails": ["ok@vexa.ai"]},
                         headers={"x-user-id": str(OWNER)}).json()
    # wrong email → refused
    bad = client.post("/transcripts/share/accept", json={"token": minted["token"]},
                      headers={"x-user-id": str(VISITOR), "x-user-email": "evil@x.com"})
    assert bad.status_code == 403
    assert not _authorized(client, VISITOR)
    # allow-listed email → admitted
    good = client.post("/transcripts/share/accept", json={"token": minted["token"]},
                       headers={"x-user-id": str(VISITOR), "x-user-email": "ok@vexa.ai"})
    assert good.status_code == 200
    assert _authorized(client, VISITOR)


def test_shared_meeting_surfaces_in_the_recipients_list():
    """After redeeming, the meeting shows in the recipient's /meetings list (flagged shared) so they can
    FIND and open it — even though they don't own it. This is the fix for 'shared meeting is not there'."""
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    client = TestClient(create_app(store, redis=None))
    token = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"}, headers={"x-user-id": str(OWNER)}).json()["token"]

    assert client.get("/meetings", headers={"x-user-id": str(VISITOR)}).json()["meetings"] == []  # before
    client.post("/transcripts/share/accept", json={"token": token}, headers={"x-user-id": str(VISITOR)})
    mine = client.get("/meetings", headers={"x-user-id": str(VISITOR)}).json()["meetings"]
    assert len(mine) == 1 and mine[0]["native_meeting_id"] == NID and mine[0]["shared"] is True


def test_bad_token_is_404():
    client = _client()
    r = client.post("/transcripts/share/accept", json={"token": "999.nope"}, headers={"x-user-id": str(VISITOR)})
    assert r.status_code == 404


def test_visitor_can_LOAD_the_transcript_after_redeem():
    """After redeeming, the recipient can READ the durable transcript by id — not just subscribe."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID,
                             segments=[{"segment_id": "s1", "text": "hello", "speaker": "A"}])
    client = TestClient(create_app(store, redis=None))
    token = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"}, headers={"x-user-id": str(OWNER)}).json()["token"]

    # before redeem: a stranger reading the row by id is refused (P0 — no leak)
    assert client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(VISITOR)}).status_code == 404
    client.post("/transcripts/share/accept", json={"token": token}, headers={"x-user-id": str(VISITOR)})
    # after redeem: the recipient loads the durable transcript
    ok = client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(VISITOR)})
    assert ok.status_code == 200 and ok.json()["segments"]
    # a still-unrelated user remains refused
    assert client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(OTHER)}).status_code == 404
