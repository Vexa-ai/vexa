"""Every recipe, executed end to end against the door stub, and the artefacts it promises."""
from __future__ import annotations

import pytest

from rehearse.engine import attendee_address, rehearse
from rehearse.stub_doors import StubDoors

STATES = ("blank-admin", "organizer-invited", "attendee-stranger-minutes", "group-member",
          "warm-desk-recurring", "reply-pending")


@pytest.mark.parametrize("state", STATES)
def test_every_state_runs_and_passes_its_own_verify_block(state, catalog, env):
    res = rehearse(state, f"rehearse-{state}@rehearse.test", doors=StubDoors(), catalog=catalog,
                   env=env)
    assert res.ok, f"{state}: {res.error or [v for v in res.verify if not v['ok']]}"
    assert res.verify and all(v["ok"] for v in res.verify)


@pytest.mark.parametrize("state", ("organizer-invited", "attendee-stranger-minutes",
                                   "group-member", "warm-desk-recurring", "reply-pending"))
def test_every_touch_produces_a_link_a_person_could_click(state, catalog, env):
    """A state that ends without a link is a state nobody can walk. `blank-admin` is excluded
    only because its link is a magic link, not a scaffold — it is checked in its own test."""
    res = rehearse(state, f"rehearse-{state}@rehearse.test", doors=StubDoors(), catalog=catalog,
                   env=env)
    assert res.links, state
    assert any("?s=" in u for u in res.links.values()), res.links


def test_blank_admin_produces_a_sign_in_link_and_still_no_user(catalog, env):
    doors = StubDoors()
    res = rehearse("blank-admin", "admin@rehearse.test", doors=doors, catalog=catalog, env=env)
    assert res.ok
    assert res.links["sign_in"].startswith("https://")
    assert doors.user_find("admin@rehearse.test") is None, (
        "the state is ABOUT TO CLAIM; nothing was clicked, so nothing may have been created")


def test_blank_admin_refuses_on_a_claimed_instance_and_says_so(catalog, env):
    res = rehearse("blank-admin", "admin@rehearse.test", doors=StubDoors(blank=False),
                   catalog=catalog, env=env)
    assert not res.ok and "NOT blank" in res.error


def test_the_organizer_state_leaves_a_meeting_parked_not_dispatched(catalog, env):
    doors = StubDoors()
    rehearse("organizer-invited", "olga@rehearse.test", doors=doors, catalog=catalog, env=env)
    assert [m["status"] for m in doors.meetings.values()] == ["scheduled"]


def test_the_stranger_state_gives_the_subject_a_desk_without_a_click(catalog, env):
    """Decision 20: the drop creates the desk. The stranger has still never signed in."""
    doors = StubDoors()
    who = "rehearse-attendee-stranger-minutes@rehearse.test"
    res = rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env)
    assert res.ok
    uid = doors.user_find(who)
    assert uid and doors.desk_tree(uid), "the drop must land somewhere for a person with no desk"


def test_the_group_state_puts_the_report_on_the_group_desk_and_the_member_in_it(catalog, env):
    doors = StubDoors()
    who = "rehearse-group-member@rehearse.test"
    res = rehearse("group-member", who, doors=doors, catalog=catalog, env=env)
    assert res.ok
    (group,) = list(doors.groups)
    assert any(m.get("email") == who for m in doors.group_members("", group))
    assert len(doors.desk_tree("", group)) > 1


def test_the_reply_state_reaches_the_email_chat_turn(catalog, env):
    doors = StubDoors()
    res = rehearse("reply-pending", "rehearse-reply-pending@rehearse.test", doors=doors,
                   catalog=catalog, env=env)
    assert res.ok
    assert any(r["flow"] == "email_chat" for r in doors.reactions)


def test_the_warm_state_puts_history_on_the_desk_before_the_touch_is_composed(catalog, env):
    """Order is the whole state: two reports must exist BEFORE the invite drops, or the prepare
    scaffold is composed against an empty desk and the warm branch never fires."""
    doors = StubDoors()
    rehearse("warm-desk-recurring", "rehearse-warm-desk-recurring@rehearse.test", doors=doors,
             catalog=catalog, env=env)
    kinds = [c[0] for c in doors.calls]
    assert kinds.index("drop_invite") > max(i for i, k in enumerate(kinds) if k == "desk_entity")


# ── idempotence ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ("organizer-invited", "warm-desk-recurring"))
def test_running_a_state_twice_is_the_same_state_not_two(state, catalog, env):
    """Idempotence by DERIVED IDENTITY (engine docstring): same ICS uid, same source_event_id,
    same native id, so the doors that already dedup do the deduping."""
    doors = StubDoors()
    who = f"rehearse-{state}@rehearse.test"
    a = rehearse(state, who, doors=doors, catalog=catalog, env=env)
    users_after_first = dict(doors.users)
    b = rehearse(state, who, doors=doors, catalog=catalog, env=env)
    assert a.ok and b.ok, b.error
    assert doors.users == users_after_first, "a second run must not mint a second account"


def test_re_entering_the_stranger_state_says_the_stranger_is_gone(catalog, env):
    """The one state a second run cannot reproduce, and it SAYS so rather than pretending.

    `attendee-stranger-minutes` needs somebody who has never been seen; the first run gives them a
    desk. Re-entering it is `subject_reset` then `rehearse`, and the refusal names the way out.
    """
    doors = StubDoors()
    who = "rehearse-attendee-stranger-minutes@rehearse.test"
    assert rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env).ok
    again = rehearse("attendee-stranger-minutes", who, doors=doors, catalog=catalog, env=env)
    assert not again.ok and "already has a user" in again.error


def test_a_second_completed_fact_is_a_duplicate_not_a_second_fan_out(catalog, env):
    doors = StubDoors()
    who = "rehearse-reply-pending@rehearse.test"
    rehearse("reply-pending", who, doors=doors, catalog=catalog, env=env)
    mails_after_first = len(doors.mail)
    out = doors.emit_fact("meeting.completed",
                          [f["source_event_id"] for f in doors.facts][-1], {})
    assert out["duplicate"] is True
    assert len(doors.mail) == mails_after_first


# ── derived values ───────────────────────────────────────────────────────────────────────────────

def test_a_speaker_label_becomes_a_test_domain_address_without_their_employer():
    assert attendee_address("Olga Avramenko (Sony Pictures Imageworks)", "rehearse.test") == \
        "olga-avramenko@rehearse.test"
    assert attendee_address("Sam Richards", "rehearse.test") == "sam-richards@rehearse.test"
    assert attendee_address("", "rehearse.test") == "someone@rehearse.test"


def test_a_failing_step_stops_the_recipe_and_names_where(catalog, env):
    class NoMail(StubDoors):
        def await_mail(self, to, subject_contains="", budget_s=180, since=0.0):
            from rehearse.doors import DoorRefused
            raise DoorRefused("no mail arrived")
    res = rehearse("organizer-invited", "x@rehearse.test", doors=NoMail(), catalog=catalog,
                   env=env)
    assert not res.ok
    assert res.steps[-1]["do"] == "(stopped)"
    assert "no mail arrived" in res.error


def test_a_missing_fixture_names_the_ones_that_exist(catalog, env):
    from rehearse.engine import Refused
    with pytest.raises(Refused, match="2026-03-02"):
        rehearse("organizer-invited", "x@rehearse.test", meeting="1999-01-01",
                 doors=StubDoors(), catalog=catalog, env=env)
