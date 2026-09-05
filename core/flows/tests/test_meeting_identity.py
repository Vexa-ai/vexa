"""THE REPORT IS ABOUT THE RIGHT MEETING — the flows-identity rows that are not about the mailbox.

R-B06 · R-B10 · R-B19 from the 2026-09-02 release backlog (R-B02 and R-B17 are proved in
`test_mail_authz.py` and `test_room_read.py`). All three are one shape: a value that is nearly the
right one, used where only the right one works, with no noise when it is wrong.

Each fails on `origin/minutes-mcp-viewer` @ b25733d12: `email_minutes` minted the organiser's link
against `refs["meeting_id"]`, `parse_ics` read a floating `DTSTART` with `time.mktime`, and the
grounding gate read the ref and could not tell an unreadable transcript from a silent meeting.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import flows_defs.production as production
import flows_steps.meeting as mt
import pytest
from flows import Registry, StepError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_link_loop import FakeChannel, FakeScaffolds, _ctx, _StubDB  # noqa: E402
import flows_steps.notify as notify_mod  # noqa: E402


@pytest.fixture(autouse=True)
def scaffolds(monkeypatch):
    fake = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", fake)
    return fake


def teardown_function():
    notify_mod.use(None)


# ── R-B06 · the organiser's link names the ROW ───────────────────────────────────────────────
def test_the_minutes_link_is_minted_against_the_row_not_the_ref(monkeypatch, scaffolds):
    """`email_attendees` resolves the row two steps later and states the reason in capitals — *"By
    ROW id, never by (platform, native)"*, the row-97 incident — and `process_meeting` resolves it
    too. This step, THE ONE MAIL THAT ALWAYS SENDS, was the site that did not: a ref carrying a
    native id mints a link into a chat that cannot see the meeting the mail is about."""
    reg = Registry()
    production.build(reg, _StubDB())
    notify_mod.use(FakeChannel())
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, m, native=None: {"id": 97})
    ctx = _ctx({"uid": "7", "organizer": "a@bank.test", "title": "T",
                "meeting_id": "96088138284", "native": ""},
               prior={"process_meeting": {"report": "the report"}})
    reg.steps["email_minutes"](ctx)
    assert scaffolds.for_("a@bank.test")["meeting"] == "97"


def test_it_degrades_to_the_ref_rather_than_failing_to_send(monkeypatch, scaffolds):
    """A lookup that cannot run is not a reason to withhold the minutes: the ref is a weaker link
    than the row and both beat no mail. (`mint_scaffold` still refuses to mint onto nothing.)"""
    reg = Registry()
    production.build(reg, _StubDB())
    notify_mod.use(FakeChannel())
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production.mt, "meeting_row", lambda uid, m, native=None: None)
    ctx = _ctx({"uid": "7", "organizer": "a@bank.test", "title": "T", "meeting_id": 41,
                "native": "abc"},
               prior={"process_meeting": {"report": "the report"}})
    reg.steps["email_minutes"](ctx)
    assert scaffolds.for_("a@bank.test")["meeting"] == "41"


# ── R-B10 · a floating DTSTART is UTC, never the server's zone ───────────────────────────────
FLOATING = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:u-1\nDTSTART:20300302T140000\n"
            "ORGANIZER:mailto:a@bank.test\nSUMMARY:Pilot\n"
            "LOCATION:https://meet.google.com/jrn-qwko-mqp\nEND:VEVENT\nEND:VCALENDAR\n")


@pytest.mark.parametrize("tz", ["UTC", "Pacific/Kiritimati", "Pacific/Niue", "Europe/Vienna"])
def test_a_floating_dtstart_reads_the_same_wherever_the_worker_runs(monkeypatch, tz):
    """`refs.start` drives the bot dispatch and the note filename — the two things that must not
    move when the process does. `time.mktime` read the tuple in whatever zone the worker happened
    to be in, so the same invite parsed 26 hours apart across the two ends of the map."""
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        from flows_integrations.mailbox import parse_ics
        assert parse_ics(FLOATING, "vexa@acme.test")["start"] == 1_898_690_400.0
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_a_zoned_and_a_zulu_dtstart_are_untouched():
    """The floating branch is the only one that moved; the guarded ones were already right."""
    from flows_integrations.mailbox import parse_ics
    zulu = FLOATING.replace("DTSTART:20300302T140000", "DTSTART:20300302T140000Z")
    vienna = FLOATING.replace("DTSTART:20300302T140000",
                              "DTSTART;TZID=Europe/Vienna:20300302T140000")
    assert parse_ics(zulu, "v@a.test")["start"] == 1_898_690_400.0
    assert parse_ics(vienna, "v@a.test")["start"] == 1_898_686_800.0


# ── R-B19 · the grounding gate ───────────────────────────────────────────────────────────────
def _process_rig(monkeypatch, *, transcript, row_id=412):
    reg = Registry()
    production.build(reg, _StubDB())
    read = {}
    monkeypatch.setattr(production, "setting", lambda uid, key: "")
    monkeypatch.setattr(production.mt, "meeting_row",
                        lambda uid, m, native=None: {"id": row_id} if row_id else None)
    monkeypatch.setattr(production.mt, "room_order", lambda uid, mid, p, n, cap=0: [])
    def fake_transcript(uid, mid):
        read["asked"] = mid
        return transcript

    monkeypatch.setattr(production.mt, "transcript_text", fake_transcript)
    monkeypatch.setattr(production.ag, "dispatch_turn", lambda *a, **k: 0)
    monkeypatch.setattr(production.ag, "head_sha", lambda uid: "sha")
    monkeypatch.setattr(production.ag, "collect_reply",
                        lambda uid, s, base: "we agreed to defer the vote until next quarter")
    return reg, read


def test_an_unreadable_transcript_is_loud_and_not_a_pass(monkeypatch):
    """`transcript_text` answered `""` for BOTH "no speech captured" and "the read failed", and
    `grounded_in("")` answers True by design. So on precisely the broken-identity meetings the
    gate exists for — `platform='unknown'`, empty native, no pair addressing the row — the gate
    passed silently and the report was mailed."""
    reg, _read = _process_rig(monkeypatch, transcript=None)
    ctx = _ctx({"uid": "7", "meeting_id": "96088138284", "native": "", "organizer": "a@b.test",
                "title": "T", "start": 1_700_003_600.0},
               scratch={"baseline": 0, "row_id": 412, "head_before": "sha"})
    with pytest.raises(StepError) as e:
        reg.steps["process_meeting"](ctx)
    assert "could not be read" in str(e.value)
    assert "not a quiet meeting" in str(e.value)


def test_a_genuinely_silent_meeting_is_still_writable(monkeypatch):
    """The other half, and it must not regress: absence of evidence is not evidence of
    fabrication, and a meeting with no captured speech must still produce a report."""
    reg, _read = _process_rig(monkeypatch, transcript="")
    ctx = _ctx({"uid": "7", "meeting_id": 412, "native": "abc", "organizer": "a@b.test",
                "title": "T", "start": 1_700_003_600.0},
               scratch={"baseline": 0, "row_id": 412, "head_before": "sha"})
    out = reg.steps["process_meeting"](ctx)
    assert out.result["report"].startswith("we agreed")


def test_the_gate_reads_the_resolved_row_not_the_ref(monkeypatch):
    """The dispatch two screens up already resolved a row and said why. This line kept using the
    ref — so it asked the transcript store for a Zoom number."""
    reg, read = _process_rig(monkeypatch, transcript="we agreed to defer the vote until next "
                                                     "quarter, minuted")
    ctx = _ctx({"uid": "7", "meeting_id": "96088138284", "native": "", "organizer": "a@b.test",
                "title": "T", "start": 1_700_003_600.0},
               scratch={"baseline": 0, "row_id": 412, "head_before": "sha"})
    reg.steps["process_meeting"](ctx)
    assert read["asked"] == 412, "the gate asked about the ref, not the row"


def test_transcript_text_separates_unreadable_from_empty(monkeypatch):
    """The unit under both branches above."""
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")
    monkeypatch.setattr(mt, "http", lambda *a, **k: (200, {"segments": []}))
    assert mt.transcript_text("7", 1) == ""
    monkeypatch.setattr(mt, "http", lambda *a, **k: (404, {"detail": "no such meeting"}))
    assert mt.transcript_text("7", 1) is None

    def boom(*a, **k):
        raise StepError("gateway down")
    monkeypatch.setattr(mt, "http", boom)
    assert mt.transcript_text("7", 1) is None
