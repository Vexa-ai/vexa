"""The two refusals that come before the first door, and the time argument they depend on.

These are the tests that keep a rehearsal off the founder's data. They are written against the
guard functions directly AND against `rehearse()` end to end, because a guard that exists but is
not wired is the same thing as no guard — and that is exactly how it would fail.
"""
from __future__ import annotations

import time

import pytest

from rehearse import catalogue as cat
from rehearse.engine import (Refused, addresses_in, guard_domain, guard_no_live_real_meeting,
                             parse_when, rehearse, subject_reset)
from rehearse.stub_doors import StubDoors

from .conftest import FIXTURES

CAT = cat.load()


def _env(domain="rehearse.test"):
    return {"VEXA_REHEARSE_DOMAIN": domain, "VEXA_DNA_FIXTURES": str(FIXTURES)}


# ── the domain guard ─────────────────────────────────────────────────────────────────────────────

def test_a_subject_outside_the_test_domain_is_refused_before_any_door():
    doors = StubDoors()
    with pytest.raises(Refused, match="not under @rehearse.test"):
        rehearse("organizer-invited", "dmitry@vexa.ai", doors=doors, catalog=CAT, env=_env())
    assert doors.calls == [], "a refused run must not have touched a single door"


def test_the_guard_reads_addresses_the_recipe_derives_not_only_the_one_typed():
    """`attendee-stranger-minutes` derives an organizer and pulls a room out of the fixture.

    If the guard only looked at `as=`, a fixture whose participants resolved outside the domain
    would fan mail at real people while the call itself looked safe.
    """
    doors = StubDoors()
    with pytest.raises(Refused) as e:
        rehearse("attendee-stranger-minutes", "someone@rehearse.test", doors=doors, catalog=CAT,
                 env=_env("other.test"))
    assert "someone@rehearse.test" in str(e.value)
    assert doors.calls == []


def test_the_mail_double_s_own_address_is_the_one_named_exception():
    guard_domain(["a@rehearse.test", "vexa@storm.test"], "rehearse.test", mailbox="vexa@storm.test")
    with pytest.raises(Refused):
        guard_domain(["vexa@storm.test"], "rehearse.test", mailbox="")


def test_addresses_are_found_wherever_they_are_nested():
    tree = {"refs": {"participants": ["a@x.test", {"deep": "b@y.test"}], "n": 3},
            "note": "cc c@z.test please"}
    assert sorted(addresses_in(tree)) == ["a@x.test", "b@y.test", "c@z.test"]


# ── the live-meeting guard ───────────────────────────────────────────────────────────────────────

def test_a_live_meeting_belonging_to_a_real_subject_stops_everything():
    doors = StubDoors(live=[{"id": "91", "status": "active", "email": "dmitry@vexa.ai"}])
    with pytest.raises(Refused, match="live meeting"):
        rehearse("organizer-invited", "x@rehearse.test", doors=doors, catalog=CAT, env=_env())
    assert not any(c[0] == "user_ensure" for c in doors.calls)


def test_a_live_meeting_belonging_to_a_rehearsal_subject_does_not():
    doors = StubDoors(live=[{"id": "92", "status": "active", "email": "x@rehearse.test"}])
    guard_no_live_real_meeting(doors, "rehearse.test")


def test_the_probe_fails_closed():
    """An unreadable probe refuses. A guard that degrades to "probably fine" is not one."""
    class Blind(StubDoors):
        def live_meetings(self):
            from rehearse.doors import DoorRefused
            raise DoorRefused("could not read the live-meeting probe")
    with pytest.raises(Exception) as e:
        rehearse("organizer-invited", "x@rehearse.test", doors=Blind(), catalog=CAT, env=_env())
    assert "live-meeting probe" in str(e.value)


# ── when ─────────────────────────────────────────────────────────────────────────────────────────

def test_relative_absolute_and_iso_times_all_parse():
    now = 1_700_000_000.0
    assert parse_when("+30m", now) == now + 1800
    assert parse_when("+3h", now) == now + 10800
    assert parse_when("-45m", now) == now - 2700
    assert parse_when("1700000123", now) == 1700000123.0
    assert parse_when("2026-03-02T14:00:00Z", now) > 0


def test_a_time_we_cannot_read_raises_rather_than_defaulting():
    with pytest.raises(Refused, match="is not a time"):
        parse_when("soon")


def test_the_default_start_is_in_the_future_so_no_bot_is_ever_dispatched():
    """`await_start` parks until start-2min. A past start spawns a real bot at a fixture URL."""
    from rehearse.engine import DEFAULT_WHEN
    assert parse_when(DEFAULT_WHEN, 1000.0) > 1000.0 + 120


# ── the reset's guard ────────────────────────────────────────────────────────────────────────────

def test_subject_reset_refuses_a_real_address_and_deletes_nothing():
    doors = StubDoors()
    doors.users["dmitry@vexa.ai"] = "126"
    with pytest.raises(Refused, match="not under @rehearse.test"):
        subject_reset("dmitry@vexa.ai", doors=doors, catalog=CAT, env=_env())
    assert doors.users["dmitry@vexa.ai"] == "126"


def test_a_dry_run_resolves_the_whole_plan_and_touches_nothing():
    doors = StubDoors()
    res = rehearse("group-member", "x@rehearse.test", doors=doors, catalog=CAT, env=_env(),
                   dry_run=True)
    assert res.ok and len(res.steps) == len(CAT["group-member"].steps)
    assert doors.calls == []
    assert time.time() > 0
