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


# ── the switch covers STORED versions too ────────────────────────────────────────────────────────
# `match()` is newest-wins across the image and `flow_version`. A switch that only skipped the
# image's registration left a stored `post_meeting` running beside `meeting_held`.

def _store(db, name, version, steps, *, scope="instance", on_event=COMPLETED):
    import json
    db.execute(
        "INSERT INTO flow_version (scope, name, version, on_event, steps, params, status, "
        "created_by, created_at) VALUES (:s, :n, :v, :e, :st, '{}', 'active', 'test', 0)",
        {"s": scope, "n": name, "v": version, "e": on_event, "st": json.dumps(steps)})


def test_off_a_stored_version_is_not_chosen(db, monkeypatch, caplog):
    reg = _build(db, monkeypatch, None)
    _store(db, "post_meeting", 6, ACTING)
    with caplog.at_level("WARNING"):
        reg.refresh_from_db(db)
    assert [f"{f.name}@{f.version}" for f in reg.match(COMPLETED)] == ["meeting_held@1"]
    assert ("post_meeting", 6) not in reg.flows
    assert reg.newest("instance", "post_meeting") is None
    assert "switched_off=1" in caplog.text, "a stored row that is not loaded must be named in the log"


def test_off_a_version_stored_later_is_not_chosen_either(db, monkeypatch):
    reg = _build(db, monkeypatch, None)
    reg.refresh_from_db(db)
    _store(db, "post_meeting", 7, ["process_meeting"])
    reg.refresh_from_db(db)
    assert [f.name for f in reg.match(COMPLETED)] == ["meeting_held"]


def test_on_a_stored_version_supersedes_the_image_as_before(db, monkeypatch):
    reg = _build(db, monkeypatch, "1")
    _store(db, "post_meeting", 6, ACTING)
    reg.refresh_from_db(db)
    assert [f"{f.name}@{f.version}" for f in reg.match(COMPLETED)] == ["post_meeting@6"]


def test_off_other_stored_flows_and_a_persons_own_flow_still_load(db, monkeypatch):
    """Only the instance's `post_meeting` is switched off: another instance flow on the same event
    loads, and so does a person's own flow that happens to share the name."""
    reg = _build(db, monkeypatch, None)
    reg.row_check = None
    _store(db, "house_record", 1, ["record_meeting_held"])
    _store(db, "post_meeting", 1, ["record_meeting_held"], scope="user:7", on_event="x.own")
    reg.refresh_from_db(db)
    assert sorted(f.name for f in reg.match(COMPLETED)) == ["house_record", "meeting_held"]
    assert [f.ident for f in reg.match("x.own")] == ["user:7/post_meeting"]


def test_the_registry_refuses_to_register_a_switched_off_flow():
    from flows import Done, EventType, Registry
    reg = Registry()

    @reg.step
    def noop(ctx):
        return Done({})

    reg.switch_off("gone", "TEST_KEY is off")
    with pytest.raises(ValueError, match="switched off"):
        reg.flow(name="gone", version=1, on=EventType("x.y"), steps=[noop])
    with pytest.raises(ValueError, match="switched off"):
        reg.flow_by_names(name="gone", version=2, on_event="x.y", step_names=["noop"])
    reg.flow_by_names(name="gone", version=2, on_event="x.y", step_names=["noop"], scope="user:1")


def test_switching_off_unmatches_what_was_already_chosen():
    from flows import Done, EventType, Registry
    reg = Registry()

    @reg.step
    def noop(ctx):
        return Done({})

    reg.flow(name="later_off", version=1, on=EventType("x.y"), steps=[noop])
    assert [f.name for f in reg.match("x.y")] == ["later_off"]
    reg.switch_off("later_off", "TEST_KEY is off")
    assert reg.match("x.y") == []
    assert reg.get("later_off", 1).steps == ("noop",), "the definition stays for runs in flight"


def test_submitting_a_switched_off_instance_flow_is_refused_with_the_reason(db, monkeypatch):
    """The API must not file a row the registry will never choose and say nothing."""
    from fastapi import HTTPException
    from flows_integrations import flows_api
    reg = _build(db, monkeypatch, None)
    monkeypatch.setattr(flows_api, "vocab", reg)
    with pytest.raises(HTTPException) as e:
        flows_api._refuse_switched_off("instance", "post_meeting")
    assert e.value.status_code == 409
    assert e.value.detail["refused"] == "flow_switched_off"
    assert "VEXA_FLOWS_POST_MEETING" in e.value.detail["note"]
    flows_api._refuse_switched_off("user:7", "post_meeting")
    flows_api._refuse_switched_off("instance", "meeting_held")
