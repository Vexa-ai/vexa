"""One key does everything its owner may do (#1922): the default `bot`-only key sends a bot and reads
that meeting's transcript; ownership, not the scope, keeps it out of another user's meeting.

Driven through meeting-api's own scope gate with the gateway's signed identity (gateway-identity.v1),
so the second gate and the ownership check are both on the path.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.route_scopes import INSUFFICIENT_SCOPE

KEY = identity_token.generate_signing_key()
OWNER, OTHER = 7, 8


def _signed(scopes, sub=OWNER) -> dict:
    return {identity_token.HEADER: identity_token.sign(
        KEY, {"sub": str(sub), "scopes": list(scopes), "limits": 3})}


@pytest.fixture
def rig():
    store = InMemoryTranscriptStore()
    mine = store.seed_meeting(
        user_id=OWNER, platform="google_meet", native_meeting_id="abc-defg-hij", meeting_id=41,
        segments=[{"segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "This is Anna.",
                   "language": "en", "speaker": "spk-Anna", "completed": True}])
    theirs = store.seed_meeting(
        user_id=OTHER, platform="google_meet", native_meeting_id="xyz-wxyz-xyz", meeting_id=42,
        segments=[{"segment_id": "ch-0:1:b", "start": 1.0, "end": 2.0, "text": "Not yours.",
                   "language": "en", "speaker": "spk-Bo", "completed": True}])
    client = TestClient(create_app(transcript_store=store, identity_key=KEY.public_key(),
                                   internal_secret="owner-scope-internal", open_callbacks=True))
    return client, mine, theirs


def _not_scope_refused(r) -> bool:
    return not (r.status_code == 403 and r.json().get("detail") == INSUFFICIENT_SCOPE)


@pytest.mark.parametrize("scopes", [["bot"], ["tx"]])
def test_one_key_sends_a_bot_and_reads_its_own_transcript(rig, scopes):
    client, mine, _ = rig
    spawn = client.post("/bots", headers=_signed(scopes),
                        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert _not_scope_refused(spawn), spawn.text
    r = client.get(f"/transcripts/by-id/{mine}", headers=_signed(scopes))
    assert r.status_code == 200, r.text
    assert [s["text"] for s in r.json()["segments"]] == ["This is Anna."]
    r = client.get("/transcripts/google_meet/abc-defg-hij", headers=_signed(scopes))
    assert r.status_code == 200, r.text
    r = client.get("/meetings", headers=_signed(scopes))
    assert r.status_code == 200, r.text


def test_the_same_key_gets_404_on_another_users_meeting(rig):
    client, _, theirs = rig
    r = client.get(f"/transcripts/by-id/{theirs}", headers=_signed(["bot"]))
    assert r.status_code in (403, 404), r.text
    assert "Not yours." not in r.text
    r = client.get("/transcripts/google_meet/xyz-wxyz-xyz", headers=_signed(["bot"]))
    assert r.status_code in (403, 404), r.text


def test_a_bot_key_subscribes_to_its_own_meeting_and_not_to_anothers(rig):
    client, mine, theirs = rig
    own = client.post("/ws/authorize-subscribe", headers=_signed(["bot"]),
                      json={"meetings": [{"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}]})
    assert own.status_code == 200 and str(mine) in own.text, own.text
    other = client.post("/ws/authorize-subscribe", headers=_signed(["bot"]),
                        json={"meetings": [{"platform": "google_meet", "native_meeting_id": "xyz-wxyz-xyz"}]})
    assert _not_scope_refused(other), other.text
    assert str(theirs) not in other.text, other.text


def test_a_browser_only_key_reads_nothing(rig):
    client, mine, _ = rig
    r = client.get(f"/transcripts/by-id/{mine}", headers=_signed(["browser"]))
    assert r.status_code == 403
