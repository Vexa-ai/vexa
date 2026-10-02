"""PRD decision 40.7 — flows runs with the agent domain absent, and says so.

*"We want agents service be optional, all domains must work independently and in any
configuration. Identity is probably the one that everyone depends on… meetings, agents and flows —
independently and together in any configuration."* (founder, 2026-09-03 07:47Z)

The `no-agents` product (decision 40.6) is gateway + meetings + flows + identity. It still receives
meeting-completed facts and still runs the post-meeting flow — and roughly half of that flow's
steps dispatch an agent turn. This file is the contract for what those steps do when there is
nothing to dispatch to: **a typed `not_present` outcome on the reaction, terminal, never an
exception, never a silent skip, and the absent door never knocked on.**
"""
from __future__ import annotations

import agent_half
import flows_config
import pytest
from flows import Done, FakeClock, NotPresent, Registry, StepCtx, admit, status, tick
from sqlite_double import SqliteDB
from flows_steps import common

import flows_defs.production as production


class _StubDB:
    """production.build() only calls execute(); nothing here asserts on storage."""

    def execute(self, *a, **k):
        return []


# ── the presence signal ──────────────────────────────────────────────────────────────────────────

def test_the_agent_door_is_a_capability_not_a_defaulted_url():
    """A DEFAULT URL ASSERTS THE DOMAIN EXISTS. That is the whole bug: absence then arrives as a
    connection error, which is an outage, which retries forever."""
    cls, default, _why = flows_config.DECLARED["VEXA_FLOWS_AGENT_API_URL"]
    assert cls == "capability" and default is None


@pytest.mark.parametrize("value,present", [("", False), ("   ", False),
                                           ("http://agent-api:8100", True)])
def test_domain_present_reads_the_configuration_never_a_probe(monkeypatch, value, present):
    """Presence is a configuration fact. A health probe would make "agent-api is restarting" and
    "there is no agent-api" the same answer — and the second is a shipped product."""
    monkeypatch.setattr(common, "AGENT_API", value)
    assert common.domain_present("agent") is present
    assert common.domain_present("identity") is True     # the one shared dependency, always


# ── the engine ───────────────────────────────────────────────────────────────────────────────────

def _one_step_rig(needs=("agent",)):
    reg, ran = Registry(), []

    @reg.step(needs=needs)
    def touch_the_agent(ctx: StepCtx):
        ran.append(ctx.reaction_id)
        return Done({"ok": True})

    ev = production.COMPLETED if hasattr(production, "COMPLETED") else None
    from flows import EventType
    reg.flow(name="one", version=1, on=EventType("t.one"), steps=[touch_the_agent])
    return reg, ran


def _admit_and_tick(reg, present):
    db, clock = SqliteDB(), FakeClock()
    admit(db, reg, clock, source_event_id="e1", event_type="t.one", subject_refs={})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    for _ in range(10):
        if not tick(db, reg, clock, present=present):
            break
    return status(db, rid)


def test_an_absent_domain_short_circuits_the_step_and_terminates_the_reaction():
    reg, ran = _one_step_rig()
    st = _admit_and_tick(reg, present=lambda d: d != "agent")

    assert ran == [], "the step body ran — the absent door would have been knocked on"
    assert st["status"] == "done", "a not_present reaction must be TERMINAL — nothing retries"
    assert st["reason"].startswith("agent:not_present"), st["reason"]
    assert st["attempt"] == 0
    receipt = st["receipts"][-1]
    assert receipt["state"] == "confirmed"
    assert receipt["result"]["outcome"] == "not_present"
    assert receipt["result"]["domain"] == "agent"


def test_the_same_step_runs_normally_when_the_domain_is_there():
    """The half a blanket refusal would break."""
    reg, ran = _one_step_rig()
    st = _admit_and_tick(reg, present=lambda _d: True)
    assert ran and st["status"] == "done" and st["reason"] is None


def test_no_predicate_means_everything_present_so_every_existing_caller_is_unchanged():
    reg, ran = _one_step_rig()
    st = _admit_and_tick(reg, present=None)
    assert ran and st["status"] == "done"


def test_an_unmarked_step_is_never_short_circuited():
    reg, ran = _one_step_rig(needs=())
    st = _admit_and_tick(reg, present=lambda _d: False)
    assert ran and st["status"] == "done"


def test_not_present_returned_by_a_body_lands_the_same_way():
    """The engine short-circuits marked steps, and a body may also answer `NotPresent` itself —
    both reach the one terminal handler, so there is one shape of this outcome and not two."""
    reg = Registry()
    from flows import EventType

    @reg.step
    def touch_the_agent(ctx: StepCtx):
        return NotPresent("agent", detail="no agent-api in this deployment")

    reg.flow(name="one", version=1, on=EventType("t.one"), steps=[touch_the_agent])
    st = _admit_and_tick(reg, present=None)
    assert st["status"] == "done"
    assert st["reason"] == "agent:not_present — no agent-api in this deployment"
    assert st["receipts"][-1]["result"]["outcome"] == "not_present"


def test_the_rename_carries_the_declaration_with_the_function():
    """`reg.steps[new] = reg.steps.pop(old)` moved the FUNCTION and dropped everything else keyed
    by the name. There is metadata keyed by the name now, and `production` uses that idiom twice."""
    reg = Registry()

    @reg.step(needs=("agent",))
    def _inner(ctx: StepCtx):
        return Done()

    reg.rename_step("_inner", "open_person")
    assert reg.needs("open_person") == frozenset({"agent"})
    assert "_inner" not in reg.step_needs


# ── the production definitions ───────────────────────────────────────────────────────────────────

#: Every production step whose body reaches the agent domain — `ag.*`, `mint_scaffold`, `ws_file`
#: or `scaffolded`. Written out rather than derived, because the point of the list is to be READ in
#: review; `test_no_production_step_reaches_the_agent_domain_undeclared` is the net underneath it.
#:
#: `production.py`'s own half — registered in every tree.
AGENT_STEPS_CORE = {
    "ack_by_email", "open_person", "drive_person", "open_group", "drive_group",
    "process_meeting", "email_minutes", "email_attendees", "drop_to_attendees",
}
#: `production_agent.py`'s half — registered only where that OPTIONAL module is in the tree, and
#: `agent_half` is the same `find_spec` signal `production._register_agent_flows` reads. The two
#: desk cards (PRD decision 42.2) RE-READ the desk before asking — the fact is old the moment it is
#: published — and the desk is agent state. A deployment without the agent domain has no card to
#: show and, because agent-api is what publishes the events these two react to, no reaction either:
#: absent twice over, which is the correct absence.
AGENT_STEPS_AGENT_HALF = {"prepare_meeting", "feedback_turn", "await_scaffold", "await_claim"}
assert AGENT_STEPS_AGENT_HALF == set(agent_half.STEPS)      # one list, two readers
#: The set this tree must register. Composed, never loosened: `==` still holds on both trees.
AGENT_STEPS = AGENT_STEPS_CORE | agent_half.only_if_present(AGENT_STEPS_AGENT_HALF)


def _production_registry() -> Registry:
    reg = Registry()
    production.build(reg, _StubDB())
    return reg


def test_every_agent_dispatching_production_step_declares_it():
    reg = _production_registry()
    assert {s for s, n in reg.step_needs.items() if "agent" in n} == AGENT_STEPS


def test_no_production_step_reaches_the_agent_domain_undeclared():
    """The net under the list above: a new step that calls `ag.*` and forgets `needs=` would run
    its body in a no-agents deployment and reach a door that is not there."""
    import inspect

    reg = _production_registry()
    src = inspect.getsource(production)
    # The declaration must exist for every step name that appears in the file next to an agent call
    # — a cheap structural check, deliberately not a parser: it fails loudly on a shape it cannot
    # read rather than passing quietly.
    for name in AGENT_STEPS:
        assert name in reg.steps, f"{name} is not a registered step any more — update AGENT_STEPS"
    assert "ag." in src


def test_every_production_reaction_reaches_a_terminal_state_with_agents_absent(monkeypatch):
    """THE CONTRACT (PRD decision 40.7). Every flow the production definitions register, admitted
    with the agent domain absent, must END — and the agent-dispatching steps must end `done` with
    a `not_present` reason rather than `failed` after N retries against a door that is not there.

    The proof that nothing was knocked on is `calls`: the agent-domain HTTP helpers are replaced
    with functions that record and raise, so a step that reached one both fails the test and says
    which step it was."""
    reg = _production_registry()
    calls: list[str] = []

    def _forbidden(*a, **k):
        calls.append(str(a[:2]))
        raise AssertionError(f"the agent door was knocked on: {a[:2]}")

    for attr in ("dispatch_turn", "collect_reply", "collect_outbox", "workspace_init",
                 "head_sha", "head_subjects", "workspace_write", "history"):
        monkeypatch.setattr(production.ag, attr, _forbidden)
    monkeypatch.setattr(production, "mint_scaffold", _forbidden)
    monkeypatch.setattr(production, "ws_file", _forbidden)
    monkeypatch.setattr(production, "scaffolded", _forbidden)

    absent = lambda d: d != "agent"                                          # noqa: E731
    terminal = {"done", "failed", "cancelled"}
    # FLOWS THAT DO NOT END, AND MUST NOT — the exemption is about what they wait FOR.
    # This contract is that no flow parks BECAUSE THE AGENT DOMAIN IS ABSENT; `onboarding` parks
    # because it is waiting for the PERSON, and it is uncapped on purpose (founder, 2026-09-04:
    # activation is one meeting with transcription). A cap here would take the offer away from
    # exactly the people who have not taken it up yet. So it is exempted from ending and held to
    # the two properties that still matter with agents absent: it must not FAIL, and — via the
    # shared `calls` assertion below — it must not have knocked on the agent door to get there.
    unbounded_by_design = {"onboarding"}
    for (name, version), flow in reg.flows.items():
        db, clock = SqliteDB(), FakeClock()
        admit(db, reg, clock, source_event_id=f"e-{name}", event_type=flow.on.name,
              subject_refs={"meeting": "m-1", "organizer": "a@b.test", "uid": "7"})
        rows = db.execute("SELECT reaction_id FROM reaction")
        assert rows, f"{name} admitted nothing for {flow.on.name}"
        rid = rows[0][0]
        for _ in range(200):
            if not tick(db, reg, clock, present=absent):
                nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                                 "WHERE status IN ('admitted','retrying')")[0][0]
                if nxt is None:
                    break
                clock._t = max(clock._t, nxt)
        st = status(db, rid)
        if name in unbounded_by_design:
            assert st["status"] not in {"failed", "cancelled"}, (
                f"{name}@{version} is meant to wait, not to give up: {st['reason']}")
            continue
        assert st["status"] in terminal, f"{name}@{version} parked in {st['status']}: {st['reason']}"
        if flow.steps[0] in AGENT_STEPS:
            assert st["status"] == "done", f"{name} FAILED instead of degrading: {st['reason']}"
            assert (st["reason"] or "").startswith("agent:not_present"), st["reason"]
    assert calls == []


# ── the behavior prompt tree is CONTENT, not a build dependency ──────────────────────────────────
#
# PRD decision 18(a). `behavior/` is a top-level content tree, mounted per deployment (decision
# 43.12) — and the three agent kickoffs in it were read at MODULE IMPORT:
# `PROCESS_KICKOFF = _prompt("process-meeting.md")`. A tree without `behavior/prompts/` therefore
# could not IMPORT `flows_defs.production` at all: fourteen test modules failed to collect, and the
# `no-agents` product — which never dispatches a turn and never reads a kickoff — still could not
# load the flow definitions that describe its own meetings. Absent content is a fact about the
# deployment, not a broken build.


def _blind_to_the_prompt_tree(monkeypatch):
    """Every `behavior/prompts` path answers "not there", wherever it is resolved from — the
    image bakes `/behavior`, the checkout has `<root>/behavior`, and a deployment may mount
    neither."""
    from pathlib import Path
    real_is_dir, real_is_file, real_read = Path.is_dir, Path.is_file, Path.read_text

    def _gone(self) -> bool:
        return "behavior/prompts" in self.as_posix()

    def _read(self, *a, **k):
        # READ_TEXT TOO, and this is what makes the test red against the old code: `_prompt` did
        # not ask whether the file was there, it just read it — so blinding only the predicates
        # left the real repo checkout answering, and the absence was never simulated at all.
        if _gone(self):
            raise FileNotFoundError(self.as_posix())
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "is_dir", lambda self: False if _gone(self) else real_is_dir(self))
    monkeypatch.setattr(Path, "is_file", lambda self: False if _gone(self) else real_is_file(self))
    monkeypatch.setattr(Path, "read_text", _read)


def test_production_imports_with_no_behavior_prompts_on_disk(monkeypatch):
    """THE IMPORT. Red before decision 18(a): the reload raised FileNotFoundError at module scope,
    from `ONBOARD_KICKOFF = _prompt("onboard-person.md")`."""
    import importlib
    import sys
    # IMPORT FRESH, never `reload`. `importlib.reload` requires the module object passed to it to
    # still BE the entry in sys.modules, and a sibling suite (tests/test_flows_api_service.py)
    # deletes `flows_defs.production` from sys.modules — so reload turned this invariant into an
    # order-dependent ImportError that says nothing about prompts. Dropping the entry and importing
    # re-executes module scope, which is the thing under test, and leaves the shared module object
    # other test modules already hold untouched.
    _blind_to_the_prompt_tree(monkeypatch)
    saved = sys.modules.get("flows_defs.production")
    sys.modules.pop("flows_defs.production", None)
    try:
        fresh = importlib.import_module("flows_defs.production")   # must not raise
        assert callable(fresh.build)
    finally:
        monkeypatch.undo()
        if saved is not None:                             # leave sys.modules as the suite found it
            sys.modules["flows_defs.production"] = saved
        else:
            sys.modules.pop("flows_defs.production", None)


def test_a_missing_prompt_is_a_typed_absence_at_read_time(monkeypatch):
    monkeypatch.delenv("VEXA_BEHAVIOR_DIR", raising=False)
    _blind_to_the_prompt_tree(monkeypatch)
    with pytest.raises(production.PromptAbsent) as e:
        production._prompt("process-meeting.md")
    assert "process-meeting.md" in str(e.value)


def test_the_private_behavior_mount_still_wins_when_it_is_there(monkeypatch, tmp_path):
    """The half a blanket refusal would break: absence-TOLERANT, never absence-assuming."""
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "process-meeting.md").write_text("PRIVATE VOICE")
    monkeypatch.setenv("VEXA_BEHAVIOR_DIR", str(tmp_path))
    _blind_to_the_prompt_tree(monkeypatch)                 # the showcase is gone; the mount is not
    assert production._prompt("process-meeting.md") == "PRIVATE VOICE"


def test_post_meeting_answers_not_present_when_the_kickoff_is_absent(monkeypatch):
    """THE STEP. Agents deployed, no prompt tree mounted: `process_meeting` must reach the SAME
    typed terminal outcome #1453 gave an absent domain — never an exception, and never a turn
    dispatched with an empty kick."""
    from flows import Reaction
    monkeypatch.delenv("VEXA_BEHAVIOR_DIR", raising=False)
    _blind_to_the_prompt_tree(monkeypatch)

    def _forbidden(*a, **k):
        raise AssertionError("a turn was dispatched with no kickoff")

    monkeypatch.setattr(production.ag, "dispatch_turn", _forbidden)
    reg = _production_registry()
    r = Reaction("rid", "sid", "e", {"uid": "7", "meeting_id": 97, "native": "abc",
                                     "organizer": "a@x.test", "title": "T"},
                 "post_meeting", 1, "process_meeting", "running", 1, 0.0, None, None, None)
    out = reg.steps["process_meeting"](
        StepCtx(reaction=r, effect_key="k", prior={}, clock_now=1.0, scratch={}, flow=None))
    assert isinstance(out, NotPresent), out
    assert out.domain == "behavior"
    assert "process-meeting.md" in out.reason
