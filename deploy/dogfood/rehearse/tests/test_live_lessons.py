"""What the FIRST live run against the sim lane taught, held as tests.

Three of the six states failed on the stack and passed offline, which is the whole reason a stub
is not enough — each failure was a place where this package had guessed at a shape the product
defines. None of them was a product defect; all three were this tool being confidently wrong.

    attendee-stranger-minutes   422  'source' must be one of ['import', 'seed']
    group-member                422  (same)
    reply-pending               422  (same)
    warm-desk-recurring         scaffold_resolves FAIL — desk state, read as the wrong TYPE

The stub could not have caught any of them: it answered whatever it was asked. So the stub now
enforces the product's vocabulary too — a double that accepts what the real door refuses is a
double that certifies a broken caller.
"""
from __future__ import annotations

import pytest

from rehearse.doors import DoorRefused
from rehearse.engine import rehearse
from rehearse.stub_doors import StubDoors

# The route's closed vocabulary, from its own 422: "'source' must be one of ['import', 'seed']".
IMPORT_SOURCES = ("import", "seed")


def test_the_transcript_import_declares_a_source_the_route_accepts():
    """`source: "rehearse"` was refused 422 on three states. These words came from a fixture via a
    double, which is what `seed` means; the route's vocabulary is right and was not widened."""
    import inspect

    from rehearse import doors
    for door in (doors.Doors, doors.LiveDoors):
        default = inspect.signature(door.seed_meeting).parameters["source"].default
        assert default in IMPORT_SOURCES, (
            f"{door.__name__}.seed_meeting defaults `source` to {default!r}; the import route "
            f"answers 422 with the list {list(IMPORT_SOURCES)} for anything else")
        assert default == "seed", "a fixture through a double is a seed, not an import"


def test_the_stub_refuses_a_source_the_real_route_would_refuse(catalog, env):
    """The stub's job is to fail where the stack fails. It answered 200 to `source: "rehearse"`
    and certified three states that could not run."""
    doors = StubDoors()
    with pytest.raises(DoorRefused, match="must be one of"):
        doors.seed_meeting("1", "native-x", "T", [{"start": 0, "end": 1}], 0.0, source="rehearse")


def test_the_desk_state_in_a_scaffold_is_an_object_not_a_string(catalog, env):
    """`refs.state` is `{"desk": …, "group": …}`. Read as a string the check could never pass, so
    it reported a product defect where the state had actually worked."""
    doors = StubDoors()
    res = rehearse("warm-desk-recurring", "rehearse-warm-desk-recurring@rehearse.test",
                   doors=doors, catalog=catalog, env=env)
    assert res.ok, [v for v in res.verify if not v["ok"]]
    sc = next(iter(doors.scaffolds.values()))
    assert isinstance(sc["refs"]["state"], dict)
    assert sc["refs"]["state"]["desk"] == "warm"


def test_a_wrong_desk_state_still_fails_the_check(catalog, env):
    """The fix must not have turned the check off: a scaffold whose desk state is not what the
    recipe declared is still a FAIL."""
    class Pile(StubDoors):
        def _desk_state(self, uid):                       # every desk reads as a pile
            return "pile"
    res = rehearse("warm-desk-recurring", "rehearse-warm-desk-recurring@rehearse.test",
                   doors=Pile(), catalog=catalog, env=env)
    bad = [v for v in res.verify if v["check"] == "scaffold_resolves"]
    assert bad and not bad[0]["ok"]
    assert "'pile', expected 'warm'" in bad[0]["detail"]


def test_two_meeting_reports_alone_are_a_pile_not_a_warm_desk(catalog, env):
    """The product's classifier, in its own words: meeting entities alone are a `pile` (decision
    22's economics — reports landed, nobody wired them); it takes a non-meeting entity to be
    `warm`. The recipe writes both rather than arguing with the classifier."""
    doors = StubDoors()
    rehearse("warm-desk-recurring", "rehearse-warm-desk-recurring@rehearse.test", doors=doors,
             catalog=catalog, env=env)
    kinds = [c[2] for c in doors.calls if c[0] == "desk_entity"]
    assert kinds.count("meeting") == 2 and "person" in kinds


def test_blank_admin_refuses_on_a_claimed_instance_which_is_the_live_answer(catalog, env):
    """The live run's `blank-admin` failure is this refusal, and it is CORRECT: the dogfood stack
    has an admin and a committed company layer. The state asserts that precondition; it never
    creates it, because creating it means deleting every person on the stack."""
    res = rehearse("blank-admin", "admin@rehearse.test", doors=StubDoors(blank=False),
                   catalog=catalog, env=env)
    assert not res.ok
    assert "NOT blank" in res.error and "blank-instance.sh" in res.error


# ── run 4's lesson: a rehearsal must leave NOTHING armed ─────────────────────────────────────────

def test_every_state_that_drops_an_invite_disarms_its_bot_leg(catalog):
    """`invite_intake` parks on `await_start` until start−2min and then dispatches a REAL bot at
    the invite's URL. These states rehearse the PREPARE TOUCH; the bot leg is not what they
    measure, and leaving it parked arms a live dispatch at a fixture Zoom URL that fires on the
    clock long after the state was reported green.

    It happened on 2026-09-02: meeting 115 reached `joining` at 19:20Z while the catalogue was
    still running, because the run outlived a `+30m` start.
    """
    for name, st in catalog.states.items():
        verbs = [step.do for step in st.steps]
        if "drop_invite" not in verbs:
            continue
        assert "cancel_bot_leg" in verbs, f"{name} drops an invite and never disarms it"
        assert verbs.index("cancel_bot_leg") > verbs.index("drop_invite"), name


def test_the_default_start_cannot_be_reached_by_a_run_s_own_wall_clock():
    """A floor, not the fix. Three states with a 677-segment import and an agent turn each ran for
    well over half an hour; `+30m` put start−2min inside that window."""
    from rehearse.engine import DEFAULT_WHEN, parse_when
    assert parse_when(DEFAULT_WHEN, 0.0) >= 3 * 3600


def test_the_state_actually_cancels_the_parked_reaction(catalog, env):
    doors = StubDoors()
    res = rehearse("organizer-invited", "rehearse-organizer-invited@rehearse.test", doors=doors,
                   catalog=catalog, env=env)
    assert res.ok, res.error
    parked = [r for r in doors.reactions
              if r["flow"] == "invite_intake" and r["state"] != "cancelled"]
    assert not parked, f"the run left a parked invite reaction: {parked}"


def test_a_cancel_that_is_refused_fails_the_state_rather_than_passing_quietly(catalog, env):
    """The one outcome that must never be silent: a reaction we could not disarm is a bot that
    will be dispatched."""
    class NoCancel(StubDoors):
        def cancel_bot_leg(self, flow, source_contains=""):
            raise DoorRefused("cancelled 0, REFUSED ['r1:500']")
    res = rehearse("organizer-invited", "rehearse-organizer-invited@rehearse.test",
                   doors=NoCancel(), catalog=catalog, env=env)
    assert not res.ok
    assert "REFUSED" in res.error
