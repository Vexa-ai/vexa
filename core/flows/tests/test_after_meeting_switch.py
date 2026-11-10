"""THE AFTER-MEETING SWITCH (`VEXA_FLOWS_POST_MEETING`, Helm `flows.postMeeting.enabled`).

Founder, 10 Nov 2026 (decision 337): «default off». `post_meeting` opens with one model turn, then
mails the minutes and the attendees and creates accounts for attendees in the organiser's domain.
An install that never asked for that must do none of it: unset, a completed meeting is recorded and
nothing else happens. Set, the flow is today's, step for step.

What these hold:
  * unset (and any value that is not a yes) registers no `post_meeting`; `meeting.completed` matches
    ONE flow, `meeting_held`, whose single step reaches no domain and produces no effect;
  * the reaction row is still created, because the timeline and the first-meeting item read the
    completion from flows' own table;
  * `1` registers `post_meeting@5` with its five steps and no `meeting_held` beside it.
Offline: the registry is composed, no step of `post_meeting` is run.
"""
from __future__ import annotations

import pytest

KEY = "VEXA_FLOWS_POST_MEETING"
COMPLETED = "meeting.completed"
ACTING = ["process_meeting", "email_minutes", "email_attendees", "propose_call_changes",
          "drop_to_attendees"]


def _build(db, monkeypatch, value):
    from flows import Registry
    from flows_defs import production
    from flows_steps import common
    monkeypatch.setattr(common, "AGENT_API", "", raising=False)
    monkeypatch.setattr(common, "MEETINGS_API", "http://meetings.invalid", raising=False)
    monkeypatch.delenv("VEXA_FLOWS_DEFS_EXTRA", raising=False)
    monkeypatch.delenv("VEXA_BEHAVIOR_DIR", raising=False)
    if value is None:
        monkeypatch.delenv(KEY, raising=False)
    else:
        monkeypatch.setenv(KEY, value)
    reg = Registry()
    production.build(reg, db)
    return reg


@pytest.mark.parametrize("value", [None, "", "0", "false", "off", "no", "maybe"])
def test_unset_or_not_a_yes_runs_no_after_meeting_step(db, monkeypatch, value):
    reg = _build(db, monkeypatch, value)
    assert not [k for k in reg.flows if k[0] == "post_meeting"], \
        "post_meeting is registered with the switch off: a completed meeting would start a model turn"
    matched = reg.match(COMPLETED)
    assert [f.name for f in matched] == ["meeting_held"]
    names = list(matched[0].steps)
    assert names == ["record_meeting_held"]
    assert not set(names) & set(ACTING)
    assert "record_meeting_held" not in reg.step_needs, "the record step must reach no domain"


def test_off_still_records_the_completion_and_does_nothing_else(db, monkeypatch):
    """The row is the whole product of the off state: the timeline's «meeting held» and the
    first-meeting item both read `reaction` by event type."""
    from flows import Done
    reg = _build(db, monkeypatch, None)
    out = reg.steps["record_meeting_held"](None)
    assert isinstance(out, Done)
    assert out.result == {"recorded": True, "after_meeting": "off"}
    assert getattr(out, "provider_ref", None) in (None, "")


@pytest.mark.parametrize("value", ["1", "true", "on", "yes", "TRUE"])
def test_on_is_todays_flow_step_for_step(db, monkeypatch, value):
    reg = _build(db, monkeypatch, value)
    assert list(reg.flows[("post_meeting", 5)].steps) == ACTING
    assert [f.name for f in reg.match(COMPLETED)] == ["post_meeting"]
    assert not [k for k in reg.flows if k[0] == "meeting_held"], \
        "two reactions on one completed meeting"


def test_the_key_is_a_capability_and_unset_is_off(monkeypatch):
    import flows_config
    cls, default, why = flows_config.DECLARED[KEY]
    assert (cls, default) == ("capability", None) and "OFF" in why
    monkeypatch.delenv(KEY, raising=False)
    assert flows_config.get_bool(KEY) is False
