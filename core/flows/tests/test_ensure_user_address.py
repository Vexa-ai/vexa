"""F94 — an account was created for a timestamp, and neither lock existed.

On 2026-09-02 the instance gained user 131 with the email `20260902t183213z`. It is the DTSTAMP of
the first rehearsal invite, five seconds after the invite landed. The chain:

    ICS UID contains "organizer"  →  parse_ics matched the word inside the UID line and captured
                                     the next colon's value (the DTSTAMP)
    invite_intake.ensure_user     →  created a platform account for that string

Three fixes, and they are at three different depths:

  * the CAUSE — `parse_ics` anchors its property patterns (`core/flows/tests/test_ics_property_anchor.py`)
  * the LAST PLACE THAT CAN TELL — `ensure_user` refuses a non-address, here
  * the REHEARSAL'S OWN GUARD — `guard_domain` checks the shape, not just the suffix
    (`deploy/dogfood/rehearse/tests/test_guards.py`)

The middle one matters most: everything after `ensure_user` works with a uid and has no way to
know the account behind it came from a parse artefact.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import FakeClock, Registry, SqliteDB, StepError, admit  # noqa: E402
from flows_defs import production  # noqa: E402


def _step(name: str):
    db = SqliteDB(":memory:")
    db.executescript((Path(__file__).resolve().parents[1] / "schema.sql").read_text())
    reg = Registry()
    production.build(reg, db)
    return reg.steps[name], db


class _Ctx:
    def __init__(self, refs):
        self.refs, self.prior, self.scratch, self.clock_now = refs, {}, {}, 0.0
        self.flow = None


@pytest.mark.parametrize("organizer", [
    "20260902t183213z",          # THE ONE. An invite's DTSTAMP, read as its organizer.
    "",                          # no ORGANIZER line at all
    "@rehearse.test",            # a domain with nobody in front of it
    "someone@",                  # the other half missing
    "someone@localhost",         # no dot in the host — not routable, not an address
    "Real Person <a@b.test>",    # the display form, unparsed
])
def test_an_invite_never_mints_an_account_for_something_that_is_not_an_address(organizer):
    step, _db = _step("ensure_user")
    with pytest.raises(StepError) as e:
        step(_Ctx({"organizer": organizer}))
    assert "not an email address" in str(e.value)
    assert e.value.retryable is False, (
        "the refs are frozen at admission, so retrying delivers the same malformed value")


def test_an_ordinary_organizer_is_untouched(monkeypatch):
    step, _db = _step("ensure_user")
    seen = {}
    monkeypatch.setattr(production, "ensure_platform_user",
                        lambda who: seen.setdefault("who", who) and "42" or "42",
                        raising=False)
    # The step resolves `ensure_platform_user` from its own module globals at call time; patching
    # the name on the module is what a real caller's environment does.
    try:
        step(_Ctx({"organizer": "real.person@rehearse.test"}))
    except StepError as e:                      # a service call may still fail offline
        assert "not an email address" not in str(e), e
