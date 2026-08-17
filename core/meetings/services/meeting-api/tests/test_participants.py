"""The ROSTER layer of the meeting record — participants, and what the record refuses to guess.

Drives the SHIPPED ``create_app`` over the in-memory store, OFFLINE (TestClient, no docker, no DB):

  * **attach + read-back** — ``PUT /meetings/{id}/participants`` with the shape the invitation lane
    captures (``{email, name?, role?, partstat?}``), then ``GET /meetings/{id}``;
  * **the filter** — ``GET /meetings?participant=<email>``, over the relation AND over the
    calendar-sourced ``data['attendees']`` a feed already wrote;
  * **absence ≠ empty** — a meeting that never had a roster is distinguishable from one whose
    captured roster was empty. Three meetings in the current corpus have no roster at all, and
    "we never captured one" must not read as "nobody was there";
  * **two layers, not resolved** — the speaker layer is served unchanged beside the roster and no
    field anywhere claims which voice belongs to which identity;
  * **additive contract** — every meeting body still conforms to the SEALED api.v1
    ``MeetingResponse`` / ``MeetingListResponse``.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.collector.participants import (
    SOURCE_NONE,
    compose,
    normalize_participant,
    normalize_roster,
)

from collector_contracts import assert_api_conforms

USER = 7
OTHER = 99
HEADERS = {"x-user-id": str(USER)}

# Exactly the shape `vexa_mailroom.invite._roster` emits off an .ics ATTENDEE line — email, CN,
# iCalendar ROLE, PARTSTAT. It is pasted here verbatim (not adapted) because "the mailroom's
# captured roster maps onto this contract with no transformation" is the claim under test.
MAILROOM_ROSTER = [
    {"email": "Ada@Example.com", "name": "Ada Lovelace", "role": "CHAIR", "partstat": "ACCEPTED"},
    {"email": "grace@example.com", "name": "Grace Hopper", "role": "REQ-PARTICIPANT",
     "partstat": "TENTATIVE"},
    {"email": "notetaker@vexa.ai", "role": "OPT-PARTICIPANT", "partstat": "NEEDS-ACTION"},
]


def _client(store=None):
    store = store or InMemoryTranscriptStore()
    return TestClient(create_app(store, redis=None)), store


def _seed(store, **kw):
    kw.setdefault("user_id", USER)
    kw.setdefault("platform", "google_meet")
    kw.setdefault("native_meeting_id", "abc-defg-hij")
    kw.setdefault("status", "completed")
    return store.seed_meeting(**kw)


def _attach(client, mid, participants, source="invite", headers=None):
    return client.put(
        f"/meetings/{mid}/participants",
        headers=headers or HEADERS,
        json={"source": source, "participants": participants},
    )


# ── attach + read-back ────────────────────────────────────────────────────────────────────────

def test_attach_roster_then_read_it_back_on_the_meeting():
    client, store = _client()
    mid = _seed(store)

    r = _attach(client, mid, MAILROOM_ROSTER)
    assert r.status_code == 200, r.text
    assert r.json()["participants_source"] == "invite"

    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert body["participants_source"] == "invite"
    by_email = {p["email"]: p for p in body["participants"]}
    assert set(by_email) == {"ada@example.com", "grace@example.com", "notetaker@vexa.ai"}

    ada = by_email["ada@example.com"]
    assert ada["name"] == "Ada Lovelace"
    assert ada["role"] == "organizer"          # CHAIR normalizes; the raw value is kept below
    assert ada["role_raw"] == "CHAIR"
    assert ada["partstat"] == "ACCEPTED"
    assert ada["source"] == "invite"
    # An attendee with no CN is a real row — an email identity with no display name, not a skip.
    assert by_email["notetaker@vexa.ai"]["name"] is None
    assert by_email["grace@example.com"]["role"] == "required"


def test_email_is_lowercased_so_it_is_a_stable_identity_key():
    client, store = _client()
    mid = _seed(store)
    _attach(client, mid, [{"email": "  MAILTO:Ada@Example.COM  ", "name": "Ada"}])
    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert [p["email"] for p in body["participants"]] == ["ada@example.com"]


def test_join_and_leave_times_ride_when_the_platform_gives_them():
    client, store = _client()
    mid = _seed(store)
    r = _attach(client, mid, [{
        "name": "Ada Lovelace", "joined_at": "2026-08-16T09:00:05Z",
        "left_at": "2026-08-16T09:47:12Z",
    }], source="platform")
    assert r.status_code == 200, r.text
    p = r.json()["participants"][0]
    assert p["source"] == "platform"
    assert p["email"] is None             # a participant panel gives a name, not an address
    assert p["joined_at"] == "2026-08-16T09:00:05Z"
    assert p["left_at"] == "2026-08-16T09:47:12Z"


def test_attach_replaces_that_source_and_leaves_other_sources_alone():
    """A re-delivered invitation, or a series whose attendee list changed, must CONVERGE — and must
    not clobber what the platform observed."""
    client, store = _client()
    mid = _seed(store)
    _attach(client, mid, MAILROOM_ROSTER)
    _attach(client, mid, [{"name": "Ada Lovelace"}], source="platform")

    r = _attach(client, mid, [{"email": "ada@example.com", "name": "Ada Lovelace"}])
    assert r.status_code == 200
    rows = r.json()["participants"]
    invited = [p for p in rows if p["source"] == "invite"]
    observed = [p for p in rows if p["source"] == "platform"]
    assert [p["email"] for p in invited] == ["ada@example.com"]   # grace + notetaker are gone
    assert [p["name"] for p in observed] == ["Ada Lovelace"]      # the other source survived
    assert r.json()["participants_source"] == "mixed"


def test_attach_is_owner_scoped():
    client, store = _client()
    mid = _seed(store)
    r = _attach(client, mid, MAILROOM_ROSTER, headers={"x-user-id": str(OTHER)})
    assert r.status_code == 404


def test_attach_refuses_an_unknown_source_and_a_missing_participants_key():
    client, store = _client()
    mid = _seed(store)

    r = client.put(f"/meetings/{mid}/participants", headers=HEADERS,
                   json={"source": "vibes", "participants": []})
    assert r.status_code == 400 and "source" in r.json()["detail"]

    # An ABSENT key must not be read as an empty roster — that would silently assert "nobody was
    # in this meeting" on a malformed call.
    r = client.put(f"/meetings/{mid}/participants", headers=HEADERS, json={"source": "invite"})
    assert r.status_code == 400 and "participants" in r.json()["detail"]


def test_attach_refuses_an_entry_that_identifies_nobody():
    """Dropping it silently would make the stored roster quieter than the one the caller sent."""
    client, store = _client()
    mid = _seed(store)
    r = _attach(client, mid, [{"email": "ada@example.com"}, {"partstat": "ACCEPTED"}])
    assert r.status_code == 400
    assert "participants[1]" in r.json()["detail"]


# ── absence is not an empty roster ────────────────────────────────────────────────────────────

def test_a_meeting_with_no_roster_says_so_explicitly():
    """The corpus case: capture happened, no roster was ever available. `participants: []` alone
    would be indistinguishable from an invitation with nobody on it."""
    client, store = _client()
    mid = _seed(store)
    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert body["participants"] == []
    assert body["participants_source"] == SOURCE_NONE


def test_a_captured_but_empty_roster_is_distinguishable_from_absence():
    client, store = _client()
    absent = _seed(store)
    empty = _seed(store, native_meeting_id="xyz-1234-abc")

    r = _attach(client, empty, [])
    assert r.status_code == 200
    assert r.json()["participants"] == [] and r.json()["participants_source"] == "invite"

    absent_body = client.get(f"/meetings/{absent}", headers=HEADERS).json()
    empty_body = client.get(f"/meetings/{empty}", headers=HEADERS).json()
    assert absent_body["participants"] == empty_body["participants"] == []
    # Same list, different facts — and the record says which.
    assert absent_body["participants_source"] == SOURCE_NONE
    assert empty_body["participants_source"] == "invite"


# ── the pre-existing stores are surfaced, not duplicated ──────────────────────────────────────

def test_a_calendar_feeds_attendees_surface_as_invite_participants_without_being_copied():
    """`data['attendees']` is calendar_sync's store and keeps its writer. The read path projects
    it rather than requiring a re-attach, so rosters captured before this relation existed are not
    invisible."""
    client, store = _client()
    mid = _seed(store, data={"attendees": [
        {"email": "marvin@example.com", "name": "Marvin", "partstat": "accepted"},
    ]})
    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert body["participants_source"] == "invite"
    assert [p["email"] for p in body["participants"]] == ["marvin@example.com"]
    assert body["participants"][0]["partstat"] == "ACCEPTED"
    # Not copied: the JSONB store is untouched by a read.
    assert body["data"]["attendees"][0]["email"] == "marvin@example.com"


def test_an_attached_invite_roster_supersedes_the_projected_one_rather_than_doubling_it():
    client, store = _client()
    mid = _seed(store, data={"attendees": [{"email": "stale@example.com"}]})
    _attach(client, mid, [{"email": "fresh@example.com"}])
    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert [p["email"] for p in body["participants"]] == ["fresh@example.com"]


def test_platform_observed_names_surface_as_platform_participants():
    client, store = _client()
    mid = _seed(store, data={"participants": ["Ada Lovelace", "Grace Hopper"]})
    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert body["participants_source"] == "platform"
    assert [p["name"] for p in body["participants"]] == ["Ada Lovelace", "Grace Hopper"]
    assert all(p["email"] is None for p in body["participants"])


# ── the participant filter ────────────────────────────────────────────────────────────────────

def test_filter_meetings_by_participant_email():
    """"Every meeting with anyone @example.com" — the query that was unanswerable."""
    client, store = _client()
    with_ada = _seed(store, native_meeting_id="m-1")
    without = _seed(store, native_meeting_id="m-2")
    _attach(client, with_ada, MAILROOM_ROSTER)
    _attach(client, without, [{"email": "someone-else@example.org"}])

    r = client.get("/meetings", headers=HEADERS, params={"participant": "ada@example.com"})
    assert r.status_code == 200, r.text
    assert [m["id"] for m in r.json()["meetings"]] == [with_ada]


def test_filter_is_case_insensitive_and_reaches_the_calendar_sourced_store():
    client, store = _client()
    attached = _seed(store, native_meeting_id="m-1")
    from_feed = _seed(store, native_meeting_id="m-2",
                      data={"attendees": [{"email": "grace@example.com"}]})
    _seed(store, native_meeting_id="m-3")
    _attach(client, attached, [{"email": "grace@example.com", "name": "Grace"}])

    r = client.get("/meetings", headers=HEADERS, params={"participant": "GRACE@Example.COM"})
    assert sorted(m["id"] for m in r.json()["meetings"]) == sorted([attached, from_feed])


def test_filter_does_not_cross_the_ownership_boundary():
    client, store = _client()
    mine = _seed(store, native_meeting_id="m-1")
    theirs = _seed(store, user_id=OTHER, native_meeting_id="m-2")
    _attach(client, mine, [{"email": "ada@example.com"}])
    _attach(client, theirs, [{"email": "ada@example.com"}],
            headers={"x-user-id": str(OTHER)})

    r = client.get("/meetings", headers=HEADERS, params={"participant": "ada@example.com"})
    assert [m["id"] for m in r.json()["meetings"]] == [mine]


def test_a_participant_that_is_not_an_email_is_refused_not_silently_empty():
    """A 200 with an empty list would read as "no meetings with this person" — the same
    looks-exactly-like-success failure an ignored filter produces."""
    client, store = _client()
    _seed(store)
    r = client.get("/meetings", headers=HEADERS, params={"participant": "Ada Lovelace"})
    assert r.status_code == 400
    assert "email" in r.json()["detail"]


def test_the_unfiltered_list_is_unchanged():
    client, store = _client()
    a = _seed(store, native_meeting_id="m-1")
    b = _seed(store, native_meeting_id="m-2")
    _attach(client, a, MAILROOM_ROSTER)
    r = client.get("/meetings", headers=HEADERS)
    assert sorted(m["id"] for m in r.json()["meetings"]) == sorted([a, b])


# ── two layers, side by side, NOT resolved ────────────────────────────────────────────────────

def test_the_speaker_layer_is_untouched_and_nothing_maps_it_to_the_roster():
    """The founder ruling made executable: the record carries the roster AND the attributed voices,
    and claims no correspondence between them. A real 10-person call has collapsed to one speaker
    label before now — a resolver here would have invented nine people."""
    client, store = _client()
    mid = _seed(store, segments=[{
        "segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "This is Anna.",
        "language": "en", "speaker": "Speaker 1", "completed": True,
    }])
    _attach(client, mid, MAILROOM_ROSTER)

    transcript = client.get("/transcripts/google_meet/abc-defg-hij", headers=HEADERS).json()
    assert [s["speaker"] for s in transcript["segments"]] == ["Speaker 1"]

    body = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    # Three identities, one voice label, and NO field anywhere joining them.
    assert len(body["participants"]) == 3
    for p in body["participants"]:
        assert not any(k in p for k in ("speaker", "speaker_label", "speaker_id", "confidence"))


def test_participants_do_not_leak_onto_the_list_view():
    """The roster is a detail-view fact. The list already had to learn (#584) not to carry
    per-meeting payloads that grow with the meeting."""
    client, store = _client()
    mid = _seed(store)
    _attach(client, mid, MAILROOM_ROSTER)
    row = client.get("/meetings", headers=HEADERS).json()["meetings"][0]
    assert "participants" not in row


def test_participants_are_not_visible_to_a_non_owner():
    client, store = _client()
    mid = _seed(store)
    _attach(client, mid, MAILROOM_ROSTER)
    assert client.get(f"/meetings/{mid}", headers={"x-user-id": str(OTHER)}).status_code == 404


# ── the sealed contract stays additive ────────────────────────────────────────────────────────

def test_meeting_bodies_still_conform_to_the_sealed_api_v1_shapes():
    client, store = _client()
    mid = _seed(store)
    _attach(client, mid, MAILROOM_ROSTER)

    detail = client.get(f"/meetings/{mid}", headers=HEADERS).json()
    assert_api_conforms("MeetingResponse", detail)
    listing = client.get("/meetings", headers=HEADERS).json()
    assert_api_conforms("MeetingListResponse", listing)


def test_the_roster_never_lands_in_the_meetings_data_blob():
    """`data` is the blob the list view had to learn to project away; a roster that grows with
    attendee count belongs in the relation, not in it. Only the small capture STAMP is in `data`."""
    client, store = _client()
    mid = _seed(store)
    _attach(client, mid, MAILROOM_ROSTER)
    data = client.get(f"/meetings/{mid}", headers=HEADERS).json()["data"]
    assert "participants" not in data and "attendees" not in data
    assert data["roster_capture"]["invite"]["count"] == 3


# ── the pure layer ────────────────────────────────────────────────────────────────────────────

def test_unknown_role_values_are_preserved_rather_than_guessed():
    row = normalize_participant({"email": "a@b.com", "role": "SCRIBE"}, source="invite")
    assert row["role"] is None                     # not forced into a category it does not fit
    assert row["data"]["role_raw"] == "SCRIBE"


def test_roster_dedups_by_email_within_a_source():
    rows = normalize_roster(
        [{"email": "a@b.com", "name": "First"}, {"email": "A@B.com", "name": "Second"}],
        source="invite",
    )
    assert [r["name"] for r in rows] == ["Second"]


def test_compose_reports_none_only_when_nothing_was_ever_captured():
    assert compose({}, [])[1] == SOURCE_NONE
    assert compose({"roster_capture": {"invite": {"count": 0, "at": "x"}}}, [])[1] == "invite"
    assert compose({"attendees": [{"email": "a@b.com"}]}, [])[1] == "invite"
