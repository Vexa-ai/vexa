"""Addressing ONE meeting, by the identity a meeting always has — fr_b6340167da32b8b6.

`platform` + `native_meeting_id` names a ROOM, not a meeting. A Google Meet link is the same link
every week, so the pair resolves to the caller's NEWEST row on it (`_resolve_owned_native`). Reads
were already fine — `list_meetings` returns every row and `GET /transcripts/by-id/{id}` fetches an
exact one — but the WRITE had no such door: an agent could pull last week's transcript and then had
nowhere to put what it learned. Annotating "wrote back" to this week's meeting instead, silently,
and the wrong row is indistinguishable from the right one in the response.

So: `POST /meetings/{meeting_id}/annotate`, and a `meeting_id` filter on `GET /transcripts/search`.
Both additive; the room-code paths are untouched and tested here alongside them.

Three segments against the pair route's four, so neither route shadows the other on segment count
— the property `POST /meetings/{meeting_id}/share` already relies on. This file pins that too,
because a routing collision would present as the wrong meeting being written, not as an error.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER, OTHER = 7, 99
H = {"x-user-id": str(USER)}
PLAT, NID = "google_meet", "abc-defg-hij"


class _NullRedis:
    async def publish(self, channel, data):
        return None


def _seg(i, text):
    return {"segment_id": f"s{i}", "start": 0.0, "end": 2.0, "text": text,
            "speaker": "Dmitriy", "language": "en"}


def _recurring():
    """TWO meetings on ONE room code — last week's and this week's. The real shape of a recurring
    call, and the shape in which the pair-addressed write is ambiguous."""
    store = InMemoryTranscriptStore()
    older = store.seed_meeting(
        user_id=USER, platform=PLAT, native_meeting_id=NID, meeting_id=1, status="completed",
        created_at="2026-08-28T09:00:00Z",
        segments=[_seg(1, "last week we agreed to revisit the pricing")],
    )
    newer = store.seed_meeting(
        user_id=USER, platform=PLAT, native_meeting_id=NID, meeting_id=2, status="active",
        created_at="2026-09-04T09:00:00Z",
        segments=[_seg(2, "this week the latency numbers look fine")],
    )
    return TestClient(create_app(store, redis=_NullRedis())), store, older, newer


# ── the write · annotate the OLDER meeting and leave the newer alone ─────────────────────────────

def test_annotating_by_db_id_writes_the_row_you_named():
    """THE friction, in one test: two meetings share a room code, the older one is annotated, and
    the newer one is untouched. Pair-addressed, this write would have landed on the newer row."""
    client, store, older, newer = _recurring()
    r = client.post(f"/meetings/{older}/annotate", headers=H,
                    json={"title": "Acme renewal — week 1", "metadata": {"crm_deal": "acme-42"}})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == older

    assert store._meetings[older]["data"]["title"] == "Acme renewal — week 1"
    assert store._meetings[older]["data"]["metadata"] == {"crm_deal": "acme-42"}
    newer_data = store._meetings[newer]["data"]
    assert "title" not in newer_data and "metadata" not in newer_data, (
        "the newest meeting on the room code was written to — the exact defect this route fixes"
    )


def test_the_room_code_route_still_resolves_to_the_newest_meeting():
    """UNCHANGED, and asserted so: the pair route's behaviour is not a bug to be fixed, it is the
    correct answer to a different question ("the meeting on this link"). It is only wrong when a
    caller meant one specific past meeting, which is what the new route is for."""
    client, store, older, newer = _recurring()
    r = client.post(f"/meetings/{PLAT}/{NID}/annotate", headers=H, json={"title": "whichever"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == newer
    assert "title" not in store._meetings[older]["data"]


def test_both_routes_coexist_and_neither_shadows_the_other():
    """A routing collision here would present as the wrong meeting being written, not as an
    error, so it is asserted directly rather than inferred from the tests above passing."""
    client, _store, older, newer = _recurring()
    assert client.post(f"/meetings/{older}/annotate", headers=H,
                       json={"title": "by id"}).json()["id"] == older
    assert client.post(f"/meetings/{PLAT}/{NID}/annotate", headers=H,
                       json={"title": "by room"}).json()["id"] == newer


def test_another_users_meeting_id_is_a_404_not_a_write():
    """Scoping is the store's (`annotate_meeting` matches user_id), and an id is guessable in a
    way a room code is not — so this is the assertion that matters most on this route."""
    client, store, older, _newer = _recurring()
    r = client.post(f"/meetings/{older}/annotate", headers={"x-user-id": str(OTHER)},
                    json={"title": "not mine"})
    assert r.status_code == 404
    assert "title" not in store._meetings[older]["data"]


def test_an_unknown_meeting_id_is_a_404():
    client, _store, _older, _newer = _recurring()
    assert client.post("/meetings/999999/annotate", headers=H,
                       json={"title": "nope"}).status_code == 404


def test_the_body_is_validated_exactly_as_the_room_code_route_validates_it():
    """One shared validator, so the two addressings cannot answer differently — a caller that
    learned the refusals on one route must not meet different ones on the other."""
    client, _store, older, _newer = _recurring()
    for body in ({}, {"title": 42}, {"metadata": "not-an-object"}):
        by_id = client.post(f"/meetings/{older}/annotate", headers=H, json=body)
        by_room = client.post(f"/meetings/{PLAT}/{NID}/annotate", headers=H, json=body)
        assert by_id.status_code == by_room.status_code == 422, body


def test_annotating_by_db_id_works_on_a_live_meeting_too():
    """The property the annotate surface exists for is not lost by the new addressing."""
    client, store, _older, newer = _recurring()
    assert store._meetings[newer]["status"] == "active"
    assert client.post(f"/meetings/{newer}/annotate", headers=H,
                       json={"title": "mid-call"}).status_code == 200
    assert store._meetings[newer]["status"] == "active", "annotating must not touch status"


# ── the read · search restricted to one exact meeting ────────────────────────────────────────────

def test_search_can_be_restricted_to_one_exact_meeting():
    client, _store, older, _newer = _recurring()
    r = client.get("/transcripts/search", headers=H,
                   params={"q": "the", "meeting_id": older})
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert hits, "the older meeting's words must still be findable"
    assert {h["meeting_db_id"] for h in hits} == {older}


def test_the_room_code_filter_still_returns_every_session_on_the_link():
    """`native_meeting_id` names the room, so it matches BOTH — which is the right answer to that
    question, and the reason a caller who meant one meeting needs the other filter."""
    client, _store, older, newer = _recurring()
    hits = client.get("/transcripts/search", headers=H,
                      params={"q": "the", "native_meeting_id": NID}).json()["hits"]
    assert {h["meeting_db_id"] for h in hits} == {older, newer}


def test_the_exact_row_wins_when_a_caller_supplies_both():
    """A caller holding both is asking about one meeting, not about the room it was held in."""
    client, _store, older, _newer = _recurring()
    hits = client.get("/transcripts/search", headers=H,
                      params={"q": "the", "native_meeting_id": NID,
                              "meeting_id": older}).json()["hits"]
    assert {h["meeting_db_id"] for h in hits} == {older}


def test_search_by_meeting_id_is_still_owner_scoped():
    client, _store, older, _newer = _recurring()
    r = client.get("/transcripts/search", headers={"x-user-id": str(OTHER)},
                   params={"q": "the", "meeting_id": older})
    assert r.status_code == 200 and r.json()["hits"] == []
