"""L2 — the 22-fixture oracle corpus, replayed in order, asserted row by row.

``tests/fixtures/ics/oracle/`` is the Stage-0 invitation corpus: real Google-Calendar and
Exchange property sets, including the shapes that break naive parsers — Windows ``TZID`` names,
a ``SUMMARY`` that changes on cancel, a ``LOCATION`` that is sometimes the URL and sometimes the
literal "Microsoft Teams Meeting", conferencing URLs recoverable only from a folded
``DESCRIPTION``, escaped TEXT, non-ASCII ``CN`` folded across a line boundary. Its README states
the expected outcome per file; ``ORACLE`` below is that table as executable rows.

Five of those expectations are product decisions rather than facts about the bytes, and each is
implemented deliberately:

* an **optional** (``ROLE=OPT-PARTICIPANT``) invitation binds like a required one — a notetaker
  is nearly always invited as optional;
* the ``ATTENDEE`` list is authoritative and the SMTP envelope is not — otherwise forwarding an
  invitation puts a bot in a stranger's meeting;
* a **floating** ``DTSTART`` (no ``TZID``, no ``Z``) is refused rather than guessed — a wrong-zone
  binding dials in at the wrong hour and reads as a product failure;
* a ``REQUEST`` for an unknown UID **binds** (RFC 5545 has no "update" method, so refusing
  non-zero ``SEQUENCE`` loses every meeting whose first invitation was lost or reordered);
* a ``CANCEL`` for an unknown UID is **silent** — a notice there trains organizers to ignore us.

The chains matter as much as the rows: update and cancel fixtures only mean what the table says
when their create has been replayed first, into the same store.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import WORKSPACE_ADDRESS, WORKSPACE_ID, FakeMailSource, FakeMeetingApi, envelope

from vexa_mailroom import Mailroom, MemoryStore

ORACLE = Path(__file__).parent / "fixtures" / "ics" / "oracle"
# The corpus dates sit in 2026-08-18…24; the clock is pinned just before them.
NOW = "2026-08-17T09:00:00+00:00"

# file → (action, reason, expected binding fields)
INDEPENDENT: list[tuple[str, str, str | None, dict]] = [
    ("gcal-create-single-bot-only.ics", "created", None,
     {"platform": "google_meet", "participants": 1}),
    ("gcal-create-single-bot-optional.ics", "created", None, {"participants": 4}),
    ("outlook-create-single-bot-only.ics", "created", None,
     {"platform": "teams", "participants": 1}),
    ("outlook-create-single-bot-optional.ics", "created", None, {"participants": 4}),
    # Negatives — no binding, no control-plane call.
    ("neg-malformed-truncated-vevent.ics", "rejected", "unparseable_ics", {}),
    ("neg-no-meeting-url.ics", "rejected", "no_meeting_link", {}),
    ("neg-bot-not-invited.ics", "rejected", "unknown_workspace", {}),
    ("neg-tzless-dtstart.ics", "rejected", "floating_start_time", {}),
    # A REQUEST for a UID we never bound still binds (and says so).
    ("neg-update-unknown-uid.ics", "created", None, {"platform": "teams"}),
    # A CANCEL for a UID we never bound is silent: no binding, no notice.
    ("neg-cancel-unknown-uid.ics", "ignored", "no_binding", {}),
]

# The four stateful chains: create → update → cancel, one binding throughout.
CHAINS: list[tuple[str, list[str], bool]] = [
    ("gcal single", ["gcal-create-single.ics", "gcal-update-single.ics",
                     "gcal-cancel-single.ics"], False),
    ("gcal recurring", ["gcal-create-recurring.ics", "gcal-update-recurring.ics",
                        "gcal-cancel-recurring.ics"], True),
    ("outlook single", ["outlook-create-single.ics", "outlook-update-single.ics",
                        "outlook-cancel-single.ics"], False),
    ("outlook recurring", ["outlook-create-recurring.ics", "outlook-update-recurring.ics",
                           "outlook-cancel-recurring.ics"], True),
]


def _rig(now: str = NOW):
    from datetime import datetime
    source, meetings, store = FakeMailSource(), FakeMeetingApi(), MemoryStore()
    clock = datetime.fromisoformat(now)
    mailroom = Mailroom(source=source, meetings=meetings, store=store, notices=store,
                        workspaces={WORKSPACE_ADDRESS: WORKSPACE_ID}, now=lambda: clock)
    return source, meetings, store, mailroom


def _deliver(source: FakeMailSource, name: str, *, to: str = WORKSPACE_ADDRESS) -> None:
    """Deliver a corpus .ics to the workspace mailbox, as a calendar client would send it."""
    source.add(envelope((ORACLE / name).read_text("utf-8"), to=to, message_id=f"<{name}>"))


@pytest.mark.parametrize("name,action,reason,expect", INDEPENDENT, ids=[r[0] for r in INDEPENDENT])
async def test_independent_row(name, action, reason, expect):
    source, meetings, store, mailroom = _rig()
    _deliver(source, name)
    result = await mailroom.poll_once()

    assert [o.action for o in result.outcomes] == [action], result.as_dict()
    assert result.outcomes[0].reason == reason, result.as_dict()

    if action == "created":
        binding = (await store.all())[0]
        assert binding.meeting_id is not None
        if "platform" in expect:
            assert binding.platform == expect["platform"]
        if "participants" in expect:
            assert len(binding.participants) == expect["participants"]
        assert meetings.of("create")[0]["workspace_id"] == WORKSPACE_ID
    else:
        assert meetings.calls == []
        assert list(await store.all()) == []
        # Rejections notify the organizer; an ignore stays silent.
        assert [n.reason for n in await store.recent()] == ([reason] if action == "rejected" else [])


@pytest.mark.parametrize("label,files,recurring", CHAINS, ids=[c[0] for c in CHAINS])
async def test_chain(label, files, recurring):
    """create → update → cancel: one binding row, moved then retired."""
    source, meetings, store, mailroom = _rig()
    create, update, cancel = files

    _deliver(source, create)
    first = await mailroom.poll_once()
    assert [o.action for o in first.outcomes] == ["created"], first.as_dict()
    binding = (await store.all())[0]
    meeting_id, original_start = binding.meeting_id, binding.scheduled_at
    assert binding.recurring is recurring
    assert len(binding.participants) == 4

    _deliver(source, update)
    second = await mailroom.poll_once()
    assert [o.action for o in second.outcomes] == ["updated"], second.as_dict()
    assert len(await store.all()) == 1, "an update must never create a second binding"
    moved = await store.get(WORKSPACE_ID, binding.uid)
    assert moved.meeting_id == meeting_id
    assert moved.scheduled_at != original_start
    assert meetings.of("update")[-1]["meeting_id"] == meeting_id

    _deliver(source, cancel)
    third = await mailroom.poll_once()
    assert [o.action for o in third.outcomes] == ["cancelled"], third.as_dict()
    assert meetings.of("cancel") == [{"meeting_id": meeting_id}]
    assert (await store.get(WORKSPACE_ID, binding.uid)).state == "cancelled"


@pytest.mark.parametrize("label,files,recurring", CHAINS, ids=[c[0] for c in CHAINS])
async def test_stale_replay_after_cancel_is_ignored(label, files, recurring):
    """A lower SEQUENCE arriving after the cancel is an out-of-order delivery, not a resurrection."""
    source, meetings, store, mailroom = _rig()
    create, update, cancel = files
    for name in (create, update, cancel):
        _deliver(source, name)
    await mailroom.poll_once()
    cancels = len(meetings.of("cancel"))

    _deliver(source, update)                      # SEQUENCE:1, after the SEQUENCE:2 cancel
    replay = await mailroom.poll_once()
    assert [o.action for o in replay.outcomes] == ["duplicate"], replay.as_dict()
    assert len(meetings.of("cancel")) == cancels
    assert len(await store.all()) == 1
    assert (await store.get(WORKSPACE_ID, (await store.all())[0].uid)).state == "cancelled"


@pytest.mark.parametrize("name", [c[1][0] for c in CHAINS])
async def test_creates_are_idempotent(name):
    """Mail is delivered at-least-once; the mailroom must be at-most-once."""
    source, meetings, store, mailroom = _rig()
    source.add(envelope((ORACLE / name).read_text("utf-8"), message_id="<first>"))
    source.add(envelope((ORACLE / name).read_text("utf-8"), message_id="<second>"))
    result = await mailroom.poll_once()
    assert result.counts == {"created": 1, "duplicate": 1}
    assert len(meetings.of("create")) == 1
    assert len(await store.all()) == 1


async def test_recurring_series_attends_the_second_occurrence():
    """AC-3: the series binds, and the row moves to occurrence 2 once occurrence 1 has passed.

    A recurring invitation is sent ONCE, so nothing else would move it: the series sweep
    re-expands the stored RRULE and re-schedules the same row.
    """
    from datetime import datetime, timedelta, timezone

    source, meetings, store, mailroom = _rig()
    _deliver(source, "gcal-create-recurring.ics")
    await mailroom.poll_once()
    binding = (await store.all())[0]
    first = datetime.fromisoformat(binding.scheduled_at)
    assert binding.recurring is True

    # Walk the clock past occurrence 1 (weekly series) and sweep.
    mailroom._now = lambda: first + timedelta(hours=2)
    advanced = await mailroom.advance_series()
    assert [o.reason for o in advanced] == ["series_advanced"]
    second = datetime.fromisoformat((await store.all())[0].scheduled_at)
    assert second == first + timedelta(days=7)
    assert meetings.of("update")[-1] == {"meeting_id": binding.meeting_id,
                                         "scheduled_at": second.astimezone(timezone.utc).isoformat()}


async def test_a_cancelled_series_stops_advancing():
    """Cancel means the bot stops coming — including for occurrences it had not reached yet."""
    from datetime import datetime, timedelta

    source, meetings, store, mailroom = _rig()
    _deliver(source, "gcal-create-recurring.ics")
    _deliver(source, "gcal-cancel-recurring.ics")
    await mailroom.poll_once()
    binding = (await store.all())[0]
    assert binding.state == "cancelled"

    mailroom._now = lambda: datetime.fromisoformat(binding.scheduled_at) + timedelta(days=30)
    assert await mailroom.advance_series() == []


async def test_every_oracle_fixture_is_asserted():
    """The whole corpus is covered — a fixture nobody asserts is not a fixture."""
    on_disk = {p.name for p in ORACLE.glob("*.ics")}
    asserted = {r[0] for r in INDEPENDENT} | {f for _l, files, _r in CHAINS for f in files}
    assert on_disk == asserted
    assert len(on_disk) == 22
