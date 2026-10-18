"""The WORKER fires keyed schedules (ADR-0050) — not just `keyed.fire_due` called by hand.

On 2026-10-14 (`b609a085f7`) the worker's keyed pass passed `**limits` to the daily-cap check, a
name defined nowhere. Every due row raised a NameError, the loop printed "keyed pass failed:
NameError" and moved on, so no keyed schedule ever fired from the worker — including the seeded
`invite_join` (the bot one minute before a meeting). `test_invite_follows_meeting` stayed green
because it calls `keyed.fire_due` without the worker's `admissible`. These tests drive the worker's
own pass, and check the worker module has no undefined global names at all."""
from __future__ import annotations

import builtins
import calendar
import json
import logging
import symtable
from pathlib import Path

import pytest

from flows import FakeClock, Registry
from flows_defs import production
from flows_integrations.mailbox import handle
from sqlite_double import SqliteDB

import flows_worker.__main__ as worker

START = calendar.timegm((2030, 8, 23, 15, 30, 0))
ICS = """BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nDTSTART:20300823T153000Z\nDTEND:20300823T170000Z
ORGANIZER;CN=Org:mailto:org@vexa.test\nUID:meet-0001@google.com\nSEQUENCE:0
LOCATION:https://meet.google.com/abc-defg-hij\nSUMMARY:Sync\nEND:VEVENT\nEND:VCALENDAR"""
LIMITS = {"loop_limit": 3, "max_hops": 8, "runs_per_day": 100, "messages_per_day": 100}


class _Msg:
    frm, headers, ics, message_id, ext_id = "org@vexa.test", {}, ICS, "m1", "m1"


def _armed_rig():
    db, clock, reg = SqliteDB(), FakeClock(START - 86400), Registry()
    production.build(reg, db)
    production.seed(db)
    reg.refresh_from_db(db)
    handle(db, reg, clock, "info@vexa.test", _Msg(), known_uid=lambda e: "7",
           is_scaffolded=lambda u: True, provision=None, rules={"mail_domains": ("vexa.test",)})
    return db, clock, reg


def test_the_worker_fires_a_keyed_schedule_at_its_time():
    db, clock, reg = _armed_rig()
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 0, "not before its time"
    clock.advance(86400 - 60 + 1)                       # one minute before the meeting, plus a second
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 1
    assert worker.keyed_pass(db, reg, clock, LIMITS) == 0, "and only once"
    flow, refs = db.execute("SELECT flow, subject_refs FROM reaction WHERE event_type = 'keyed.due'")[0]
    assert flow == "invite_join"
    assert json.loads(refs)["url"] == "https://meet.google.com/abc-defg-hij"
    assert [s for (s,) in db.execute("SELECT status FROM keyed_schedule")] == ["fired"]


def test_a_failing_pass_fails_loudly_and_does_not_escape_the_worker(caplog, monkeypatch):
    def broken(*a, **k):
        return undefined_on_purpose  # noqa: F821 — the 2026-10-14 bug class

    with caplog.at_level(logging.ERROR, logger="flows_worker"):
        with pytest.raises(worker.PassFailed):
            worker.guarded("keyed", broken)
    rec = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert rec and "BUG" in rec[0].getMessage() and "NameError" in rec[0].getMessage()
    assert rec[0].exc_info, "the traceback is logged, not a one-word print"
    assert issubclass(worker.PassFailed, Exception) and not issubclass(worker.PassFailed, NameError)


def test_the_worker_module_has_no_undefined_global_names():
    path = Path(worker.__file__)
    top = symtable.symtable(path.read_text(), str(path), "exec")
    defined = {s.get_name() for s in top.get_symbols() if s.is_assigned() or s.is_imported()}
    known = defined | set(dir(builtins)) | {"__file__", "__name__"}
    missing = set()

    def walk(t):
        for s in t.get_symbols():
            if s.is_global() and s.is_referenced() and s.get_name() not in known:
                missing.add(f"{t.get_name()}:{s.get_name()}")
        for c in t.get_children():
            walk(c)

    walk(top)
    assert not missing, f"undefined names in flows_worker/__main__.py: {sorted(missing)}"
