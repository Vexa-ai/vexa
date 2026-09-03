"""PRD decision 40.7 and decision 5 — flows runs with the meetings domain absent, and says so.

*"We want agents service be optional, all domains must work independently and in any
configuration… meetings, agents and flows — independently and together in any configuration."*
(founder, 2026-09-03 07:47Z.) #1453 delivered the agent half. This is meetings.

WHAT IT IS NOT, and the distinction is the whole design (#1453's own rule, restated because this
file is where it would be lost): presence is a CONFIGURATION FACT, never a probe. A health check
makes *"meeting-api is restarting"* and *"there is no meeting-api"* the same answer, and only the
second is a supported product — the first is an outage that must retry. **A meeting service that
is DOWN keeps the retry path it has today.** Nothing here touches it.

The shape being removed is two defects deep:

  * `VEXA_FLOWS_GATEWAY_URL` was `required-explicit`, so a flows deployment with no meetings
    REFUSED TO BOOT — `preflight()` names the door and exits, before anything about meetings is
    asked;
  * and `flows_steps/meeting.py` bound the door AT IMPORT, so even a process that got past the
    preflight could not import its own step vocabulary.

Neither is a degrade. A domain that is optional has to be absent-able at boot, at import, and at
the step — and this file is the contract for all three.
"""
from __future__ import annotations

import ast
import pathlib

import flows_config
import pytest
from flows import FakeClock, SqliteDB, admit, status, tick
from flows_steps import common
from flows_steps import meeting as mt

import flows_defs.production as production

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

#: Every production step whose body reaches the meetings domain — directly (`mt.*`) or through
#: `_meeting_stamp` / `_scaffold_refs`, which call `mt.meeting_start`. Written out rather than
#: derived, for the reason `test_no_agents.AGENT_STEPS` gives: the point of the list is to be READ
#: in review, and the contract test below is the net underneath it.
MEETINGS_STEPS = {
    "await_start", "dispatch_bot", "run_meeting",
    "process_meeting", "email_minutes", "email_attendees", "drop_to_attendees", "prepare_meeting",
}


class _StubDB:
    def execute(self, *a, **k):
        return []


# ── the declaration ──────────────────────────────────────────────────────────────────────────

def test_the_meetings_door_is_a_capability_not_a_required_one():
    """`required-explicit` is a refusal to boot. It is the right class for a door the process
    cannot work without and the wrong one for a domain that is optional by design."""
    cls, default, _why = flows_config.DECLARED[common.MEETINGS_DOOR]
    assert cls == "capability" and default is None


def test_an_unnamed_meetings_door_does_not_stop_the_process_booting():
    """THE FIRST DEFECT. `missing_doors()` filters on the class, so the reclass is the whole fix —
    but it is the half nothing else would notice, because every deployment in the tree names it."""
    assert common.MEETINGS_DOOR not in flows_config.missing_doors()


@pytest.mark.parametrize("value,present", [("", False), ("   ", False), ("http://gw:8000", True)])
def test_domain_present_reads_the_configuration_never_a_probe(monkeypatch, value, present):
    monkeypatch.setattr(common, "MEETINGS_API", value)
    assert common.domain_present("meetings") is present
    assert common.domain_present("identity") is True


def test_the_door_helper_raises_the_typed_absence_not_a_config_error(monkeypatch):
    """THE SECOND LINE OF DEFENCE, and it should never fire — the engine answers for a declared
    step without entering it. It exists because the first line is a DECLARATION, and an undeclared
    step would otherwise hand an empty base to urllib and raise about a URL, three frames from the
    cause. `ConfigError` would be wrong here for the same reason: it says *misconfigured*, and an
    absent optional domain is a supported configuration."""
    monkeypatch.setattr(common, "MEETINGS_API", "")
    with pytest.raises(common.MeetingsDomainAbsent):
        common.meetings_door()
    monkeypatch.setattr(common, "MEETINGS_API", "http://gw:8000/")
    assert common.meetings_door() == "http://gw:8000"


# ── the import ───────────────────────────────────────────────────────────────────────────────

def test_the_step_module_binds_no_door_at_import():
    """THE SECOND DEFECT, and a source assertion because an import that already succeeded cannot
    be asked whether it would have. `from .common import GATEWAY` resolves through
    `common.__getattr__` → `flows_config.require` **while the module is being imported**, so an
    unset door was an ImportError for the whole step vocabulary — including the steps that have
    nothing to do with meetings."""
    tree = ast.parse((SRC / "flows_steps" / "meeting.py").read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    assert "GATEWAY" not in imported, (
        "meeting.py imports a door constant at module scope — that resolves the door at IMPORT, "
        "which is what made an optional domain impossible")
    assert "meetings_door" in imported, "the door must be resolved at ACCESS, per call"


def test_every_meetings_url_is_built_from_the_access_time_door():
    """The net under the assertion above: a site that went back to a module constant would import
    cleanly and fail at the first call in a deployment nobody tests."""
    text = (SRC / "flows_steps" / "meeting.py").read_text()
    assert "{GATEWAY}" not in text, "a call site still names the module-level door constant"
    assert text.count("meetings_door()") >= 11, "the twelve sites resolve the door per call"


# ── the declarations on the steps ────────────────────────────────────────────────────────────

def _production_registry():
    reg = production.Registry()
    production.build(reg, _StubDB())
    return reg


def test_every_meetings_reaching_production_step_declares_it():
    reg = _production_registry()
    assert {s for s, n in reg.step_needs.items() if "meetings" in n} == MEETINGS_STEPS


def test_a_step_may_need_two_domains_and_five_of_these_do():
    """`process_meeting` and its four siblings dispatch an agent turn AND read the meeting. Both
    declarations ride the same decorator; the engine answers on the first absent one."""
    reg = _production_registry()
    both = {s for s, n in reg.step_needs.items() if {"agent", "meetings"} <= set(n)}
    assert both == {"process_meeting", "email_minutes", "email_attendees",
                    "drop_to_attendees", "prepare_meeting"}


# ── the contract ─────────────────────────────────────────────────────────────────────────────

def test_every_production_reaction_reaches_a_terminal_state_with_meetings_absent(monkeypatch):
    """THE CONTRACT. Every flow the production definitions register, admitted with the meetings
    domain absent, must END — and the meetings-reaching steps must end `done` with a
    `meetings:not_present` reason rather than `failed` after N retries against a door that is not
    there.

    The proof that nothing was knocked on is `calls`: every meetings helper is replaced with one
    that records and raises, so a step that reached one both fails the test and says which."""
    reg = _production_registry()
    calls: list[str] = []

    def _forbidden(*a, **k):
        calls.append(str(a[:2]))
        raise AssertionError(f"the meetings door was knocked on: {a[:2]}")

    for attr in ("meeting_start", "meeting_row", "ensure_meeting_row", "transcript_text",
                 "room_order", "mint_transcript_share", "speaking_seconds"):
        monkeypatch.setattr(production.mt, attr, _forbidden)
    monkeypatch.setattr(common, "MEETINGS_API", "")

    absent = lambda d: d != "meetings"                                       # noqa: E731
    terminal = {"done", "failed", "cancelled"}
    for (name, version), flow in reg.flows.items():
        db, clock = SqliteDB(), FakeClock()
        admit(db, reg, clock, source_event_id=f"e-{name}", event_type=flow.on.name,
              subject_refs={"meeting_id": "m-1", "organizer": "a@b.test", "uid": "7",
                            "claim_id": "c-1"})
        rows = db.execute("SELECT reaction_id FROM reaction")
        assert rows, f"{name} admitted nothing for {flow.on.name}"
        rid = rows[0][0]
        for _ in range(400):
            if not tick(db, reg, clock, present=absent):
                nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                                 "WHERE status IN ('admitted','retrying')")[0][0]
                if nxt is None:
                    break
                clock._t = max(clock._t, nxt)
        st = status(db, rid)
        assert st["status"] in terminal, f"{name}@{version} parked in {st['status']}: {st['reason']}"
        if flow.steps[0] in MEETINGS_STEPS:
            assert st["status"] == "done", f"{name} FAILED instead of degrading: {st['reason']}"
            assert (st["reason"] or "").startswith("meetings:not_present"), st["reason"]
    assert calls == []


def test_the_agent_contract_still_holds_alongside_it():
    """Two optional domains, not one that replaced the other: the same registry still answers
    `agent:not_present` when the agent domain is the absent one."""
    reg = _production_registry()
    assert {s for s, n in reg.step_needs.items() if "agent" in n} >= {
        "process_meeting", "email_minutes", "feedback_turn"}
