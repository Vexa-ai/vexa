"""INDEPENDENT transcript share (M0) — share a meeting's live feed via a capability link, NO workspace.

Offline over the in-memory fake:
  * owner mints a share link (open) → a different authenticated user redeems → is authorized to subscribe;
  * a user who never redeemed is refused (no workspace, no grant);
  * restricted mode admits only an allow-listed verified email;
  * decoupled from workspaces entirely (no binding, no membership involved);
  * the mint is addressable by ROW ID as well as by (platform, native) — including on a row that
    NO pair can address, which is the shape that broke the attendee mail on 2026-09-02.
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


# ── mint by ROW id ───────────────────────────────────────────────────────────────────────────
# The (platform, native) pair is not an identity. Meeting 97 (2026-09-02) was planned from an
# invite whose url matched no platform: platform='unknown', platform_specific_id='' — no pair
# addressed it, `POST /meetings/unknown/96088138284/share` answered 404, and the attendee mail
# shipped anyway with no capability, so every recipient landed in a chat that said "no meeting
# with id 97 on my side". The row id always exists; these tests hold that door open.
def test_mint_by_row_id_gives_the_owner_a_working_token():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID,
                             segments=[{"segment_id": "s1", "text": "hello", "speaker": "A"}])
    client = TestClient(create_app(store, redis=None))

    r = client.post(f"/meetings/{mid}/share", json={"mode": "open"}, headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.split(".")[0] == str(mid)          # <meeting_id>.<secret>, and it names THIS row

    # the token is a real capability, not just a well-formed string
    assert client.post("/transcripts/share/accept", json={"token": token},
                       headers={"x-user-id": str(VISITOR)}).status_code == 200
    assert client.get(f"/transcripts/by-id/{mid}", headers={"x-user-id": str(VISITOR)}).status_code == 200


def test_mint_by_row_id_works_on_a_row_NO_pair_can_address():
    """The regression. platform='unknown' + an empty native is unaddressable by the pair route —
    that route 404s here, and the by-id route must not."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform="unknown", native_meeting_id="", status="scheduled")
    client = TestClient(create_app(store, redis=None))

    # what actually happened in production: the pair the flow could construct addresses nothing
    assert client.post("/meetings/unknown/96088138284/share",
                       json={"mode": "restricted", "allowed_emails": ["a@vexa.ai"]},
                       headers={"x-user-id": str(OWNER)}).status_code == 404
    # the row id still names it
    r = client.post(f"/meetings/{mid}/share",
                    json={"mode": "restricted", "allowed_emails": ["a@vexa.ai"]},
                    headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200 and r.json()["token"].split(".")[0] == str(mid)


def test_mint_by_row_id_keeps_restricted_semantics():
    """`restricted` + this attendee's own address travels through the by-id route unchanged: a
    forwarded mail must grant its new reader nothing."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    client = TestClient(create_app(store, redis=None))
    token = client.post(f"/meetings/{mid}/share",
                        json={"mode": "restricted", "allowed_emails": ["ok@vexa.ai"]},
                        headers={"x-user-id": str(OWNER)}).json()["token"]

    bad = client.post("/transcripts/share/accept", json={"token": token},
                      headers={"x-user-id": str(VISITOR), "x-user-email": "forwarded@x.com"})
    assert bad.status_code == 403
    good = client.post("/transcripts/share/accept", json={"token": token},
                       headers={"x-user-id": str(OTHER), "x-user-email": "ok@vexa.ai"})
    assert good.status_code == 200


def test_mint_by_row_id_is_owner_scoped_and_leaks_no_existence():
    """A row that is not the caller's reads exactly like one that does not exist. Minting a
    capability on someone else's meeting must be impossible, and must not even confirm the id."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    client = TestClient(create_app(store, redis=None))

    theirs = client.post(f"/meetings/{mid}/share", json={"mode": "open"},
                         headers={"x-user-id": str(VISITOR)})
    unknown = client.post(f"/meetings/{mid + 9999}/share", json={"mode": "open"},
                          headers={"x-user-id": str(OWNER)})
    assert theirs.status_code == 404 and unknown.status_code == 404
    # Same answer SHAPE: the only difference is the id the caller itself supplied, so nothing in
    # the response distinguishes "exists but not yours" from "does not exist".
    assert theirs.json()["detail"].replace(str(mid), "<id>") == \
        unknown.json()["detail"].replace(str(mid + 9999), "<id>")

    # and nothing was written to the row the non-owner aimed at
    assert store._meetings[mid]["data"].get("share_grants") in (None, [])


def test_the_pair_route_still_mints():
    """Additive, not a replacement: 0.10 clients and the /transcripts/{platform}/{native}/share
    alias still address the mint by pair."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    client = TestClient(create_app(store, redis=None))
    r = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"},
                    headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200 and r.json()["token"].split(".")[0] == str(mid)


def test_the_two_routes_do_not_shadow_each_other():
    """Segment count keeps them apart, and both are reachable in the same app. A row id and a
    platform name are never confused: `/meetings/{id}/share` is three segments, the pair route
    four, so the router can only ever match one of them."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=OWNER, platform=PLAT, native_meeting_id=NID)
    client = TestClient(create_app(store, redis=None))
    hdr = {"x-user-id": str(OWNER)}

    by_id = client.post(f"/meetings/{mid}/share", json={"mode": "open"}, headers=hdr)
    by_pair = client.post(f"/meetings/{PLAT}/{NID}/share", json={"mode": "open"}, headers=hdr)
    assert by_id.status_code == by_pair.status_code == 200
    assert by_id.json()["id"] != by_pair.json()["id"]        # two distinct grants, one row
    assert len(store._meetings[mid]["data"]["share_grants"]) == 2

    # a non-numeric id is refused by validation, never resolved as some other kind of name
    assert client.post("/meetings/not-a-row/share", json={}, headers=hdr).status_code == 422
