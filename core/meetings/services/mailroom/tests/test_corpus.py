"""L2 — the corpus IS the specification: every fixture, its expected outcome, asserted end to end.

``CORPUS`` below is the table ``tests/fixtures/README.md` renders in prose: one row per fixture,
the action the mailroom must take, and (where the row is a rejection) the exact reason. Each row
runs the WHOLE path — envelope → parse → resolve → act — through the shipped ``Mailroom`` against
the in-process fakes, so a row proves what the service does, not what the parser returns.

The ordered rows matter as much as the individual ones: the recurring series is created by one
fixture, re-scheduled by its SEQUENCE:2 sibling, and stopped by a cancel — the same three-message
sequence a real invitation produces over a week.
"""
from __future__ import annotations

import pytest
from conftest import (ICS_DIR, NOW, WORKSPACE_ID, FakeMailSource, FakeMeetingApi, envelope,
                      read_eml, read_ics)

from vexa_mailroom import Mailroom, MemoryStore

# fixture → (action, reason-or-None, extra assertions)
CORPUS: list[tuple[str, str, str | None, dict]] = [
    ("google-request-meet.ics", "created", None,
     {"platform": "google_meet", "native_meeting_id": "abc-defg-hij",
      "scheduled_at": "2026-08-18T14:00:00+00:00", "title": "Quarterly sync"}),
    ("google-request-description-link.ics", "created", None,
     {"platform": "google_meet", "native_meeting_id": "xyz-mnop-qrs"}),
    ("outlook-request-teams.ics", "created", None,
     {"platform": "teams", "native_meeting_id": "19:meeting_ZjQxYjkwMjEt@thread.v2",
      "scheduled_at": "2026-08-20T09:00:00+00:00"}),
    ("outlook-request-teams-short.ics", "created", None,
     {"platform": "teams", "native_meeting_id": "9361792952021"}),
    ("zoom-request.ics", "created", None,
     {"platform": "zoom", "native_meeting_id": "85512345678"}),
    # A weekly series: ONE binding, marked recurring, scheduled at the NEXT occurrence
    # (2026-08-19, the Wednesday after the pinned clock) — never one row per occurrence.
    ("google-recurring-weekly.ics", "created", None,
     {"recurring": True, "scheduled_at": "2026-08-19T10:00:00+00:00"}),
    # EXDATE removes that Wednesday, so the next occurrence is the one after it.
    ("google-recurring-exdate.ics", "created", None,
     {"recurring": True, "scheduled_at": "2026-08-26T10:00:00+00:00"}),
    ("plus-tagged-address.ics", "created", None, {"invited_address": "mk-dev@dev.vexa.ai"}),
    ("negative-no-link.ics", "rejected", "no_meeting_link", {}),
    ("negative-no-uid.ics", "rejected", "no_uid", {}),
    ("negative-malformed.ics", "rejected", "unparseable_ics", {}),
    ("negative-reply-rsvp.ics", "rejected", "unsupported_method", {}),
    ("negative-not-invited.ics", "rejected", "unknown_workspace", {}),
]

# Every row is DELIVERED to the workspace mailbox: what differs is whether the invitation NAMES
# the workspace on its ATTENDEE list. `negative-not-invited.ics` is the forwarded/BCC'd shape —
# it reached us, so it earns an explanation, but it never invited us, so it binds nothing.
NOT_ADDRESSED: set[str] = set()


def _mailroom(source: FakeMailSource, meetings: FakeMeetingApi, store: MemoryStore) -> Mailroom:
    from conftest import WORKSPACE_ADDRESS
    return Mailroom(source=source, meetings=meetings, store=store, notices=store,
                    workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: NOW)


@pytest.mark.parametrize("name,action,reason,expect", CORPUS, ids=[r[0] for r in CORPUS])
async def test_corpus_row(name, action, reason, expect):
    source, meetings, store = FakeMailSource(), FakeMeetingApi(), MemoryStore()
    mailroom = _mailroom(source, meetings, store)
    to = "someone@example.com" if name in NOT_ADDRESSED else "mk-dev@dev.vexa.ai"
    source.add(envelope(read_ics(name), to=to, message_id=f"<{name}>"))

    result = await mailroom.poll_once()
    assert len(result.outcomes) == 1, result.as_dict()
    outcome = result.outcomes[0]
    assert outcome.action == action, f"{name}: {outcome.as_dict()}"
    assert outcome.reason == reason, f"{name}: {outcome.as_dict()}"

    if action == "created":
        assert len(meetings.of("create")) == 1
        binding = (await store.all())[0]
        for key, value in expect.items():
            assert getattr(binding, key) == value, f"{name}.{key}"
        # The control plane is asked to plan the meeting, with the workspace and the link.
        call = meetings.of("create")[0]
        assert call["workspace_id"] == WORKSPACE_ID
        assert call["meeting_url"] == binding.meeting_url
        assert call["auto_join"] is True
    else:
        assert meetings.calls == [], f"{name}: a rejected message must touch no control plane"
        assert list(await store.all()) == [], f"{name}: a rejected message must bind nothing"
        notices = await store.recent()
        assert [n.reason for n in notices] == [reason]


async def test_corpus_is_complete():
    """Every .ics in the corpus has a row — a fixture nobody asserts is not a fixture."""
    on_disk = {p.name for p in ICS_DIR.glob("*.ics")}
    asserted = {row[0] for row in CORPUS}
    covered_elsewhere = {"google-recurring-update-seq2.ics", "google-cancel.ics",
                         "outlook-cancel-status-only.ics"}   # the sequenced rows below
    assert on_disk == asserted | covered_elsewhere


async def test_series_update_then_cancel(source, meetings, store, mailroom):
    """The three-message life of a recurring invitation: invite → moved → cancelled."""
    source.add(envelope(read_ics("google-recurring-weekly.ics"), message_id="<a>"))
    first = await mailroom.poll_once()
    assert first.counts == {"created": 1}
    binding = (await store.all())[0]
    meeting_id = binding.meeting_id

    # SEQUENCE:2 moves the series to 11:30 — the SAME row is re-scheduled, never a second row.
    source.add(envelope(read_ics("google-recurring-update-seq2.ics"), message_id="<b>"))
    second = await mailroom.poll_once()
    assert second.counts == {"updated": 1}
    assert meetings.of("update") == [{"meeting_id": meeting_id,
                                      "scheduled_at": "2026-08-19T11:30:00+00:00",
                                      "title": "Weekly team sync (moved to 11:30)"}]
    assert len(await store.all()) == 1
    assert (await store.get("ws-mk-dev", "google-recurring-006@google.com")).sequence == 2

    # Re-delivering the same SEQUENCE changes nothing (idempotent).
    source.add(envelope(read_ics("google-recurring-update-seq2.ics"), message_id="<c>"))
    third = await mailroom.poll_once()
    assert third.counts == {"duplicate": 1}
    assert len(meetings.of("update")) == 1

    # A cancel for the series stops attendance: the planned row is deleted, the binding retires.
    cancel = read_ics("google-cancel.ics").replace("google-one-off-001@google.com",
                                                   "google-recurring-006@google.com")
    source.add(envelope(cancel, message_id="<d>"))
    fourth = await mailroom.poll_once()
    assert fourth.counts == {"cancelled": 1}
    assert meetings.of("cancel") == [{"meeting_id": meeting_id}]
    assert (await store.get("ws-mk-dev", "google-recurring-006@google.com")).state == "cancelled"


async def test_status_cancelled_without_method_is_a_cancel(source, meetings, store, mailroom):
    """Exchange retracts with STATUS:CANCELLED and no METHOD — honoured as a cancellation."""
    source.add(envelope(read_ics("outlook-request-teams.ics"), message_id="<a>"))
    await mailroom.poll_once()
    meeting_id = (await store.all())[0].meeting_id

    source.add(envelope(read_ics("outlook-cancel-status-only.ics"), message_id="<b>"))
    result = await mailroom.poll_once()
    assert result.counts == {"cancelled": 1}
    assert meetings.of("cancel") == [{"meeting_id": meeting_id}]


@pytest.mark.parametrize("name,action,reason", [
    ("google-invitation.eml", "created", None),
    ("outlook-attachment-only.eml", "created", None),
    ("negative-plain-email.eml", "rejected", "no_calendar_part"),
])
async def test_full_mime_messages(name, action, reason):
    """The real wire shapes: Google's multipart/alternative, Exchange's base64 invite.ics, and an
    ordinary email — the mailbox is public, so most of what lands in it is not an invitation."""
    source, meetings, store = FakeMailSource(), FakeMeetingApi(), MemoryStore()
    mailroom = _mailroom(source, meetings, store)
    source.add(read_eml(name))
    result = await mailroom.poll_once()
    assert [o.action for o in result.outcomes] == [action], result.as_dict()
    assert result.outcomes[0].reason == reason


async def test_external_corpus(external_corpus):
    """Wire the out-of-repo ICS corpus when one is pointed at (``MAILROOM_ICS_CORPUS=<dir>``).

    Every file must parse to a DECIDED outcome — acted on, or rejected with a reason from the
    closed vocabulary. A file that throws, or that lands in ``failed``, is a corpus row the parser
    does not understand, and that is the finding this test exists to surface.
    """
    if external_corpus is None:
        pytest.skip("MAILROOM_ICS_CORPUS not set")
    files = sorted(external_corpus.glob("*.ics"))
    assert files, f"no .ics files in {external_corpus}"
    source, meetings, store = FakeMailSource(), FakeMeetingApi(), MemoryStore()
    mailroom = _mailroom(source, meetings, store)
    for f in files:
        to = "someone@example.com" if "not-invited" in f.name or "unknown" in f.name else "mk-dev@dev.vexa.ai"
        source.add(envelope(f.read_text("utf-8", errors="replace"), to=to,
                            message_id=f"<{f.name}>"))
    result = await mailroom.poll_once()
    assert len(result.outcomes) == len(files)
    undecided = [o.as_dict() for o in result.outcomes if o.action == "failed"]
    assert not undecided, f"corpus rows the mailroom could not decide: {undecided}"
