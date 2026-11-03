"""`subject_reset` — one subject gone, the instance untouched, and the emptiness READ BACK."""
from __future__ import annotations

import pytest

from rehearse.doors import DoorRefused
from rehearse.engine import Refused, rehearse, subject_reset
from rehearse.stub_doors import StubDoors

WHO = "rehearse-organizer-invited@rehearse.test"


def _entered(catalog, env):
    doors = StubDoors()
    rehearse("organizer-invited", WHO, doors=doors, catalog=catalog, env=env)
    return doors


def test_it_removes_the_user_the_desk_the_scaffolds_and_the_mail(catalog, env):
    doors = _entered(catalog, env)
    uid = doors.user_find(WHO)
    assert uid and doors.desk_tree(uid) and doors.mail
    out = subject_reset(WHO, doors=doors, catalog=catalog, env=env)
    assert out["ok"], out["remaining"]
    assert doors.user_find(WHO) is None
    assert doors.desk_tree(uid) == []
    assert not [m for m in doors.mail if WHO in m["to"]]
    assert not [s for s in doors.scaffolds.values() if s["who"] == WHO]


def test_it_leaves_every_other_subject_exactly_where_it_found_them(catalog, env):
    doors = _entered(catalog, env)
    rehearse("group-member", "rehearse-group-member@rehearse.test", doors=doors, catalog=catalog,
             env=env)
    before = {a: u for a, u in doors.users.items() if a != WHO}
    desks_before = {k: list(v) for k, v in doors.desks.items()}
    subject_reset(WHO, doors=doors, catalog=catalog, env=env)
    assert {a: u for a, u in doors.users.items()} == before
    gone_uid = {k for k in desks_before if k not in doors.desks}
    assert len(gone_uid) == 1, "exactly one desk may disappear — the subject's"


def test_it_is_a_no_op_on_a_subject_that_was_never_there(catalog, env):
    doors = StubDoors()
    out = subject_reset("never-existed@rehearse.test", doors=doors, catalog=catalog, env=env)
    assert out["ok"] and out["uid"] is None


def test_it_refuses_a_non_test_domain_before_touching_anything(catalog, env):
    doors = StubDoors()
    doors.users["dmitry@vexa.ai"] = "126"
    doors.desks["126"] = ["README.md"]
    with pytest.raises(Refused):
        subject_reset("dmitry@vexa.ai", doors=doors, catalog=catalog, env=env)
    assert doors.users["dmitry@vexa.ai"] == "126" and doors.desks["126"]


def test_a_door_that_cannot_delete_is_REPORTED_not_swallowed(catalog, env):
    """A reset that half worked and said "done" is the ledger's phantom `_global` write, one
    layer down. `ok` is false and `remaining` names the door."""
    class NoUserDelete(StubDoors):
        def user_delete(self, uid):
            raise DoorRefused("admin-api has no DELETE /admin/users/{id} on the running image")
    doors = NoUserDelete()
    rehearse("organizer-invited", WHO, doors=doors, catalog=catalog, env=env)
    out = subject_reset(WHO, doors=doors, catalog=catalog, env=env)
    assert out["ok"] is False
    assert "DELETE /admin/users" in out["remaining"]["user"]
    assert out["remaining"]["user_still_exists"], "the read-back is what proves it, not the call"


def test_the_state_can_be_re_entered_immediately_after_a_reset(catalog, env):
    """The point of decision 38.3: a state re-entered in seconds, the instance never blanked.

    `fresh=True` resets the room the recipe owns — the subject and the organizer derived from
    them. Both are needed: the fact's id names the meeting row, and that row belongs to the
    organizer, so resetting only the subject would leave the fact deduping the re-entry away and
    the state would look entered while producing no touch at all.
    """
    doors = StubDoors()
    who = "rehearse-attendee-stranger-minutes@rehearse.test"
    first = rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env)
    assert first.ok
    assert not rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog,
                        env=env).ok
    again = rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env,
                     fresh=True)
    assert again.ok, again.error
    assert again.links and again.links != {}, "a re-entered state must produce a NEW touch"


def test_fresh_resets_the_organizer_too_or_the_fact_would_dedup_the_touch_away(catalog, env):
    """The lane's dedup memory is the thing that has to go, and it belongs to BOTH people.

    `admit()` dedups on (source_event_id, flow) and a rehearsal's ids are derived from
    (state, subject, meeting) — deliberately, so a plain re-run is idempotent. The cost is that a
    re-entry needs the earlier row cleared, and the meeting the fact names belongs to the ORGANIZER
    the recipe derives, not to the subject. Reset only the subject and the fact is swallowed as a
    duplicate: no touch is sent, and the step waits out its budget for a mail nothing will produce.
    That is what the sim lane logged as `admitted 0`.
    """
    doors = StubDoors()
    who = "rehearse-attendee-stranger-minutes@rehearse.test"
    first = rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env)
    organizer = f"organizer-{who.split('@')[0]}@rehearse.test"
    assert first.ok and doors.user_find(organizer)
    stale = [f["source_event_id"] for f in doors.facts]
    assert stale

    again = rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env,
                     fresh=True)
    assert again.ok, again.error
    assert again.links, "a re-entered state must produce a touch, not a deduped silence"
    # ONE fact in the lane, not two: the earlier one went with the organizer's reset, and the
    # re-entry admitted its own. Its id differs because it names the meeting ROW, which is new —
    # both halves matter, and either alone would let the touch be swallowed as a duplicate.
    fresh_ids = [f["source_event_id"] for f in doors.facts]
    assert len(fresh_ids) == 1, f"the stale fact survived the reset: {fresh_ids}"
    assert fresh_ids != stale
