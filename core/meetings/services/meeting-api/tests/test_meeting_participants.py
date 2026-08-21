"""#451 — GET /meetings/{platform}/{native}/participants: who was in the meeting, honestly.

The demand (Vexa-ai/vexa#451, two independent external reporters) is a post-meeting attendance
list "including participants who did not speak". The 0.12 core cannot serve that, and these tests
pin BOTH halves of that sentence: what the route does answer, and the fact that it refuses to
pretend about the rest.

What is persisted, and therefore what this route may return:
  * `meeting.data['attendees']` — the calendar invitation's ATTENDEE lines (calendar_sync). Real
    people, silent ones included, for calendar-imported meetings only.
  * DISTINCT `transcriptions.speaker` — people HEARD and named.

What is not persisted, and therefore what this route must never invent: an observed roster. The
platform modules observe only tiles that emit a speaking signal, and no 0.12 producer writes a
roster anywhere (Vexa-ai/vexa#861). `observed_roster: "not_recorded"` is the machine-readable form
of that, and `test_absence_is_not_an_empty_room` is the negative control that keeps it honest —
without it, an empty `participants` would read as "nobody attended", which this data can never say.

The authorization negative control (`test_another_users_meeting_is_404_not_an_empty_roster`) is the
one that matters most: this is a data-exposure path, and 404 rather than an empty 200 is what stops
it confirming that another tenant's meeting exists.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
OTHER_USER = 99
H = {"x-user-id": str(USER)}
PLAT, NATIVE = "google_meet", "abc-defg-hij"
PATH = f"/meetings/{PLAT}/{NATIVE}/participants"

INVITE = [
    {"email": "alice@example.com", "name": "Alice Example", "partstat": "ACCEPTED"},
    {"email": "bob@example.com", "name": "Bob Silent", "partstat": "ACCEPTED"},
]


class _CaptureRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, data):
        self.published.append((channel, data))


def _client():
    store = InMemoryTranscriptStore()
    return TestClient(create_app(store, redis=_CaptureRedis())), store


def _seg(segment_id: str, speaker, start: float):
    return {
        "segment_id": segment_id, "speaker": speaker, "text": "hello",
        "start": start, "end": start + 1.0, "completed": True,
    }


# ── authorization: the boundary this endpoint exists behind ───────────────────────────────────
def test_another_users_meeting_is_404_not_an_empty_roster():
    """THE data-exposure control. A caller asking for someone else's meeting must be REFUSED, not
    handed an empty list — an empty 200 confirms the meeting exists, which is itself the leak."""
    client, store = _client()
    store.seed_meeting(user_id=OTHER_USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": INVITE}, segments=[_seg("s1", "Alice Example", 0.0)])

    r = client.get(PATH, headers=H)

    assert r.status_code == 404
    body = r.json()
    assert "participants" not in body          # no roster shape at all on the refusal
    assert "Alice" not in r.text and "alice@example.com" not in r.text


def test_unknown_meeting_is_404():
    client, _store = _client()
    assert client.get(PATH, headers=H).status_code == 404


def test_missing_identity_header_is_refused():
    """No x-user-id → the gateway did not authorize this hop; there is no owner to scope to."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    assert client.get(PATH).status_code == 401


def test_owner_of_a_same_native_id_meeting_sees_only_their_own():
    """Two tenants, same meeting link. Each owner's read is scoped to their own row."""
    client, store = _client()
    store.seed_meeting(user_id=OTHER_USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": [{"email": "carol@other.example", "name": "Carol"}]})
    mine = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                              data={"attendees": [{"email": "alice@example.com", "name": "Alice"}]})

    body = client.get(PATH, headers=H).json()

    assert body["meeting_id"] == mine
    assert [p["email"] for p in body["participants"]] == ["alice@example.com"]
    assert "carol@other.example" not in client.get(PATH, headers=H).text


# ── the invitation source: silent people, but only for calendar-imported meetings ──────────────
def test_invited_attendees_include_someone_who_never_spoke():
    """The #451 ask — a participant who did not speak — IS answerable, for a calendar-imported
    meeting: Bob is on the invitation and in no transcript segment, and he is in the response."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": INVITE},
                       segments=[_seg("s1", "Alice Example", 0.0)])

    body = client.get(PATH, headers=H).json()

    invited = [p for p in body["participants"] if p["source"] == "invite"]
    assert [p["email"] for p in invited] == ["alice@example.com", "bob@example.com"]
    bob = next(p for p in invited if p["email"] == "bob@example.com")
    assert bob["name"] == "Bob Silent"
    assert bob["response_status"] == "accepted"
    assert "Bob Silent" not in [p["name"] for p in body["participants"] if p["source"] == "speaker"]


def test_partstat_is_omitted_when_the_feed_carried_none():
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": [{"email": "dana@example.com"}]})

    row = client.get(PATH, headers=H).json()["participants"][0]

    assert row == {"name": None, "email": "dana@example.com", "source": "invite"}


# ── the speaker source: heard and named, in first-heard order ──────────────────────────────────
def test_speakers_are_distinct_and_ordered_by_first_utterance():
    client, store = _client()
    store.seed_meeting(
        user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
        segments=[
            _seg("s1", "Bob", 30.0),
            _seg("s2", "Alice", 5.0),
            _seg("s3", "Bob", 40.0),      # same speaker again → ONE row
            _seg("s4", "Alice", 60.0),
        ],
    )

    body = client.get(PATH, headers=H).json()

    assert [p["name"] for p in body["participants"]] == ["Alice", "Bob"]
    assert all(p["source"] == "speaker" and p["email"] is None for p in body["participants"])
    assert body["sources"] == ["speaker"]


def test_unattributed_segments_do_not_become_a_participant():
    """A segment whose speaker never resolved is a hole in attribution, not a person. Null and
    blank speakers must not appear as a nameless attendee."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       segments=[_seg("s1", None, 0.0), _seg("s2", "  ", 1.0),
                                 _seg("s3", "Alice", 2.0)])

    body = client.get(PATH, headers=H).json()

    assert [p["name"] for p in body["participants"]] == ["Alice"]


# ── the two sources side by side, and the refusal to resolve them ──────────────────────────────
def test_both_sources_are_labelled_and_no_identity_resolution_happens():
    """Alice is BOTH invited and heard, and appears twice — once per source. That is deliberate:
    matching a voice label to an invitee is a guess, and the wrong guess silently merges two
    humans. Vexa-ai/vexa#861's preparation forbids promoting transcript speakers into a roster."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": INVITE},
                       segments=[_seg("s1", "Alice Example", 0.0)])

    body = client.get(PATH, headers=H).json()

    assert body["sources"] == ["invite", "speaker"]
    alice_rows = [p for p in body["participants"] if p["name"] == "Alice Example"]
    assert len(alice_rows) == 2
    assert sorted(p["source"] for p in alice_rows) == ["invite", "speaker"]
    # No resolver artefacts: nothing claims these two rows are the same person.
    for p in body["participants"]:
        assert set(p) <= {"name", "email", "source", "response_status"}
        assert "confidence" not in p and "participant_id" not in p


def test_invite_rows_come_before_speaker_rows():
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": INVITE}, segments=[_seg("s1", "Zoe", 0.0)])

    sources = [p["source"] for p in client.get(PATH, headers=H).json()["participants"]]

    assert sources == ["invite", "invite", "speaker"]


# ── the honesty controls: what this route refuses to claim ─────────────────────────────────────
def test_absence_is_not_an_empty_room():
    """A meeting with no invitation and no attributed speech returns an EMPTY list — and says, in
    the same breath, that no attendance was ever recorded. Without `observed_roster` a consumer
    would read `participants: []` as "nobody was there", which this data can never support."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)

    body = client.get(PATH, headers=H).json()

    assert body["participants"] == []
    assert body["sources"] == []
    assert body["observed_roster"] == "not_recorded"


def test_observed_roster_is_not_recorded_even_when_people_were_heard():
    """Hearing four people does not mean four people attended, and the field says so regardless of
    how full the list looks. It flips only when a producer actually records presence (#861)."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": INVITE},
                       segments=[_seg(f"s{i}", f"Speaker {i}", float(i)) for i in range(4)])

    body = client.get(PATH, headers=H).json()

    assert len(body["participants"]) == 6
    assert body["observed_roster"] == "not_recorded"


def test_response_identifies_the_meeting_it_answered_for():
    client, store = _client()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)

    body = client.get(PATH, headers=H).json()

    assert body["meeting_id"] == mid
    assert body["platform"] == PLAT
    assert body["native_meeting_id"] == NATIVE


def test_malformed_attendees_do_not_break_the_read():
    """`data` is a JSONB blob written by more than one path; a non-list or a list of junk must
    degrade to "no invitation", never to a 500 on a read endpoint."""
    client, store = _client()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                       data={"attendees": "not-a-list"})
    assert client.get(PATH, headers=H).json()["participants"] == []

    client2, store2 = _client()
    store2.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE,
                        data={"attendees": ["alice@example.com", None, 7]})
    assert client2.get(PATH, headers=H).json()["participants"] == []
