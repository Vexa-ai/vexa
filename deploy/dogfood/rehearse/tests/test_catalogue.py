"""The catalogue validates itself — and the `door:` column is proved not to be decoration."""
from __future__ import annotations

import inspect

import pytest

from rehearse import catalogue as cat
from rehearse.doors import Doors

STATES = ("blank-admin", "organizer-invited", "attendee-stranger-minutes", "group-member",
          "warm-desk-recurring", "reply-pending")


def test_the_six_states_of_decision_38_are_all_here():
    c = cat.load()
    assert sorted(c.states) == sorted(STATES)


def test_every_state_names_its_story_and_can_be_checked():
    c = cat.load()
    for name, st in c.states.items():
        assert st.summary, name
        assert st.story, f"{name} does not say which part of the script it is"
        assert st.verify, f"{name} has no verify block — nobody could tell it worked"
        assert st.steps, name


def test_every_verb_has_a_door_method_and_every_door_method_is_a_verb():
    """The two halves cannot drift.

    A verb in the vocabulary with no method behind it is a recipe nothing can execute; a method
    with no verb is a door nothing reaches. Both are silent until someone writes the recipe that
    needs them, which is the wrong moment to find out.
    """
    methods = {n for n, _ in inspect.getmembers(Doors, inspect.isfunction)
               if not n.startswith("_")}
    # Doors that are NOT recipe verbs, each with the caller that needs it: the verify block's
    # reads, the two guards, the reset's per-store deletes, and the per-subject harness binding
    # (which is a flag on the call, never a step — a recipe describes a STATE, and which model
    # runs the turns is not part of what state somebody is in).
    reads = {"user_find", "meeting_get", "desk_tree", "group_members", "scaffold_get",
             "live_meetings", "user_delete", "desk_delete", "session_keys_delete",
             "scaffold_keys_delete", "friction_delete_for", "mail_delete_for",
             "bind_runner", "meetings_delete_for", "lane_rows_delete_for"}
    assert set(cat.VERBS) <= methods
    assert methods - reads == set(cat.VERBS)


def test_every_step_declares_the_door_that_actually_answers_it():
    c = cat.load()
    for name, st in c.states.items():
        for step in st.steps:
            assert step.door == cat.VERBS[step.do].door, f"{name} step {step.index}"


def test_a_wrong_door_is_refused_at_load(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text(_yaml(steps=[{"do": "user_ensure", "door": "gateway",
                                 "address": "a@t.test", "as": "u"}]))
    with pytest.raises(cat.CatalogueError, match="declares door"):
        cat.load(bad)


def test_an_unknown_verb_is_refused_at_load(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text(_yaml(steps=[{"do": "teleport", "door": "gateway"}]))
    with pytest.raises(cat.CatalogueError, match="not a verb"):
        cat.load(bad)


def test_an_unknown_argument_is_refused_at_load(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text(_yaml(steps=[{"do": "user_ensure", "door": "admin-api",
                                 "address": "a@t.test", "as": "u", "hurry": True}]))
    with pytest.raises(cat.CatalogueError, match="unknown argument"):
        cat.load(bad)


def test_a_verify_row_naming_a_capture_nobody_makes_is_refused(tmp_path):
    """The vacuous-check rule: a check pointing at nothing passes for free."""
    bad = tmp_path / "s.yaml"
    bad.write_text(_yaml(
        steps=[{"do": "user_ensure", "door": "admin-api", "address": "a@t.test", "as": "u"}],
        verify=[{"check": "mail_present", "of": "never_captured"}]))
    with pytest.raises(cat.CatalogueError, match="which no step captures"):
        cat.load(bad)


def test_a_state_with_no_verify_block_is_refused(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text(_yaml(
        steps=[{"do": "user_ensure", "door": "admin-api", "address": "a@t.test", "as": "u"}],
        verify=[]))
    with pytest.raises(cat.CatalogueError, match="no `verify`"):
        cat.load(bad)


# ── interpolation ────────────────────────────────────────────────────────────────────────────────

def test_a_whole_string_token_keeps_the_value_s_type():
    """`participants: "{{fixture_attendees}}"` must reach the intake as a LIST.

    A fact whose participants arrived as the string "['a@x']" admits fine, fans out to nobody, and
    reports success. That is the failure this rule exists for.
    """
    out = cat.interpolate({"participants": "{{who}}", "n": "{{count}}"},
                          {"who": ["a@t.test", "b@t.test"], "count": 3})
    assert out == {"participants": ["a@t.test", "b@t.test"], "n": 3}


def test_a_token_inside_a_sentence_renders_as_text():
    assert cat.interpolate("Prepare: {{title}} now", {"title": "DNA TSC"}) == "Prepare: DNA TSC now"


def test_an_unbound_token_raises_and_says_what_is_bound():
    with pytest.raises(cat.CatalogueError, match="nothing is bound to"):
        cat.interpolate("{{meeting_row.meeting_id}}", {"title": "x"})


def test_lenient_interpolation_leaves_a_marker_that_is_not_address_shaped():
    """The pre-flight guard reads the plan; its placeholder must not read as an address."""
    out = cat.interpolate({"a": "{{subject}}", "b": "{{later.id}}"}, {"subject": "x@t.test"},
                          lenient=True)
    assert out == {"a": "x@t.test", "b": cat.UNBOUND}
    assert "@" not in cat.UNBOUND


def _yaml(steps, verify=None) -> str:
    import json
    doc = {"version": 1, "domain_env": "VEXA_REHEARSE_DOMAIN", "default_domain": "t.test",
           "states": {"x": {"summary": "s", "story": "st", "steps": steps,
                            "verify": verify if verify is not None
                            else [{"check": "user_exists", "address": "a@t.test"}]}}}
    return json.dumps(doc)      # JSON is valid YAML, and it keeps these fixtures unambiguous
