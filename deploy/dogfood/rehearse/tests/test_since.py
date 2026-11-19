"""A touch is evidence only if it is THIS run's touch — run 2's lesson, held as a test.

`await_mail` had no floor, so it matched a message an earlier run had sent. On the sim lane that
made `warm-desk-recurring` verify run 1's Prepare mail and run 1's scaffold, and report on a state
run 2 had not produced. It looked like a product defect (`desk state 'pile', expected 'warm'`) and
it was a reader accepting stale evidence — the same class as a hash comparison that goes stale, and
the reason the doctrine is *positive evidence*, never inequality against a remembered value.
"""
from __future__ import annotations

import time

import pytest

from rehearse.doors import DoorRefused
from rehearse.engine import rehearse, subject_reset
from rehearse.stub_doors import StubDoors

WHO = "rehearse-organizer-invited@rehearse.test"


def test_a_previous_run_s_mail_is_not_this_run_s_evidence(catalog, env):
    """The reproduction. The mail is there, addressed correctly, with the right subject — and it
    is old, so the step must keep waiting rather than accept it."""
    class SilentInvite(StubDoors):
        """The invite lands but the lane sends nothing back — a parked or broken flow. The ONLY
        candidate for `await_mail` is then the stale message, which is the whole point."""
        def drop_invite(self, organizer, title, start, attendees=(), ics_uid="", group="",
                        url=""):
            return {"ics_uid": ics_uid, "to": "vexa@sim.test", "organizer": organizer,
                    "start": start, "attendees": [a for _, a in attendees]}

    doors = SilentInvite()
    doors._send(WHO, "Prepare: DNA TSC 2026-03-02", "an earlier run's touch")
    doors.mail[-1]["at"] = time.time() - 3600
    res = rehearse("organizer-invited", WHO, doors=doors, catalog=catalog, env=env)
    assert not res.ok
    assert "no mail" in res.error


def test_a_fresh_touch_in_the_same_run_is_accepted(catalog, env):
    res = rehearse("organizer-invited", WHO, doors=StubDoors(), catalog=catalog, env=env)
    assert res.ok, res.error


def test_the_floor_is_the_run_s_own_start_not_the_process_s(catalog, env):
    """Two runs back to back: the second must verify the second's mail, not the first's."""
    doors = StubDoors()
    first = rehearse("organizer-invited", WHO, doors=doors, catalog=catalog, env=env)
    second = rehearse("organizer-invited", WHO, doors=doors, catalog=catalog, env=env)
    assert first.ok and second.ok
    assert first.mails[0]["id"] != second.mails[0]["id"], (
        "the second run verified the first run's mail — the floor did not move")


def test_subject_reset_removes_the_meetings_that_would_block_re_entry(catalog, env):
    """Run 2's other lesson. A run that fails BETWEEN `POST /meetings` and the import leaves a
    non-terminal row, and the next attempt is refused — correctly — with a 409. Nothing else on
    today's stack clears it: `DELETE /admin/users/{id}` is not on the running admin-api image."""
    doors = StubDoors()
    rehearse("reply-pending", "rehearse-reply-pending@rehearse.test", doors=doors,
             catalog=catalog, env=env)
    assert doors.meetings
    out = subject_reset("rehearse-reply-pending@rehearse.test", doors=doors, catalog=catalog,
                        env=env)
    assert out["removed"]["meetings"] >= 1
    assert doors.meetings == {}


def test_it_only_removes_that_subject_s_meetings(catalog, env):
    doors = StubDoors()
    rehearse("reply-pending", "rehearse-reply-pending@rehearse.test", doors=doors,
             catalog=catalog, env=env)
    rehearse("group-member", "rehearse-group-member@rehearse.test", doors=doors, catalog=catalog,
             env=env)
    before = len(doors.meetings)
    subject_reset("rehearse-reply-pending@rehearse.test", doors=doors, catalog=catalog, env=env)
    assert 0 < len(doors.meetings) < before


def test_a_meeting_delete_that_refuses_is_reported_not_swallowed(catalog, env):
    class NoDelete(StubDoors):
        def meetings_delete_for(self, subject):
            raise DoorRefused("the gateway refused DELETE /meetings/{id}")
    doors = NoDelete()
    rehearse("reply-pending", "rehearse-reply-pending@rehearse.test", doors=doors,
             catalog=catalog, env=env)
    out = subject_reset("rehearse-reply-pending@rehearse.test", doors=doors, catalog=catalog,
                        env=env)
    assert out["ok"] is False
    assert "DELETE /meetings" in out["remaining"]["meetings"]


# ── run 2's third lesson: a refusal is not an empty list ─────────────────────────────────────────

def test_the_meetings_list_never_reads_a_refusal_as_no_meetings():
    """`GET /meetings` caps `limit` at 100 and answers 422 above it. The list was requested with
    `?limit=200`, and both readers did `(b or {}).get("meetings", [])` — so the 422's `{"detail":
    …}` became "this person has no meetings".

    `subject_reset` then reported `meetings: 0` with a row sitting right there, and the next run of
    that state was refused with a 409 nobody could explain. The other reader is worse:
    `seed_meeting` asks it whether a completed row already exists, so a swallowed refusal would
    have minted a SECOND completed meeting and mailed the whole room twice.
    """
    import ast
    import inspect

    from rehearse import doors

    # Read the CODE, not the prose: the fix's own docstring quotes `?limit=200` as the defect, and
    # a grep over source text cannot tell an explanation from an instruction.
    tree = ast.parse(inspect.getsource(doors))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = (node.body or [None])[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            assert "/meetings?limit=200" not in node.value, (
                "a meetings request still asks for more than that route's cap")
    assert doors.MEETINGS_PAGE_MAX == 100, "the route's own cap, not a number we prefer"

    # Exactly one place reads that list, and it RAISES rather than defaulting to empty.
    assert "raise DoorRefused" in inspect.getsource(doors.LiveDoors._meetings_of)
    for reader in (doors.LiveDoors._find_meeting, doors.LiveDoors.meetings_delete_for):
        body = inspect.getsource(reader)
        assert "_meetings_of" in body
        assert '.get("meetings", [])' not in body


def test_a_meeting_that_refused_deletion_is_not_counted_as_deleted(catalog, env):
    """`meetings_delete_for` returned a count, and a count cannot say "one of these did not go".
    A delete that did not happen must reach `remaining`, or the next run meets a 409."""
    import inspect

    from rehearse import doors
    src = inspect.getsource(doors.LiveDoors.meetings_delete_for)
    assert "refused" in src and "raise DoorRefused" in src


# ── run 4: the filter rejected the mail it then named in its own refusal ─────────────────────────

@pytest.mark.parametrize('created,ok', [
    ('2026-09-02T18:50:41.503Z', True),
    ('2026-09-02T18:50:41.5Z', True),        # Go trims trailing zeros — .5Z beside .503Z
    ('2026-09-02T18:50:41Z', True),
    ('2026-09-02T18:50:41.503123456Z', True),  # RFC3339Nano, more digits than %f takes
    ('2026-09-02T18:50:41+02:00', True),
    ('', False),
    ('not a timestamp', False),
])
def test_mailpit_stamps_parse_and_an_unreadable_one_is_None_not_zero(created, ok):
    '''NONE IS NOT ZERO. It returned 0.0 on an unparseable stamp and the caller filtered
    `epoch < since`, so every message read as older than the run — run 4 failed three states
    while listing, in its own refusal, the mail it had just discarded.'''
    from rehearse.doors import _mail_epoch
    got = _mail_epoch({'Created': created})
    assert (got is not None) is ok
    if ok:
        assert got > 1_000_000_000


def test_a_message_we_cannot_place_in_time_is_INCLUDED_not_dropped():
    '''A false accept is a check that needs tightening; a false reject is a touch reported as
    never sent. The second is the one that wastes a person's afternoon.'''
    import time as _t

    from rehearse.doors import LiveDoors
    import rehearse.doors as D

    calls = []
    def fake_http(method, url, headers=None, body=None, timeout=40):
        calls.append(url)
        if '/search' in url:
            return 200, {'messages': [{'ID': 'm1', 'Subject': 'Prepare: X', 'Created': 'garbage'}]}
        return 200, {'Subject': 'Prepare: X', 'To': [{'Address': 'a@rehearse.test'}],
                     'From': {'Address': 'v@sim.test'}, 'MessageID': '<x@y>', 'Text': 'body',
                     'HTML': ''}
    old = D._http
    D._http = fake_http
    try:
        d = LiveDoors.__new__(LiveDoors)
        msg = LiveDoors.await_mail(d, 'a@rehearse.test', 'Prepare', budget_s=5, since=_t.time())
        assert msg['subject'] == 'Prepare: X'
    finally:
        D._http = old
