"""Shape diversity — the workflow forms n8n proves users need, expressed in THIS engine:
  IF/branch        → a step decides and no-ops one arm (or picks the next event to emit)
  fan-out per item → per-target effect keys INSIDE one step (bounded, e.g. recipients)
  sub-workflow     → a step EMITS A FACT; admission starts the child flow (composition)
  error workflow   → a flow triggered by the `reaction.failed` fact of another flow
  versioning       → v1 keeps running v1 steps while new events select v2
Rejoining PARALLEL branches is the documented Dapr tripwire — deliberately not expressible."""
from __future__ import annotations

from fixtures import drain, rig
from flows import EventType, Done, admit, status, tick


def test_branch_step_decides_and_skips():
    db, reg, clock, world = rig()

    @reg.step
    def maybe_confirm(ctx):
        if ctx.refs.get("wants_confirm"):
            world.emails.append((ctx.refs["inviter"], "confirm"))
        return Done({"confirmed": bool(ctx.refs.get("wants_confirm"))})

    ev = EventType("branchy.requested")
    reg.flow(name="branchy", version=1, on=ev,
             steps=[reg.steps["maybe_confirm"], reg.steps["commit_summary"]])
    admit(db, reg, clock, source_event_id="b-1", event_type=ev.name,
          subject_refs={"meeting": "mb", "inviter": "a@bank.com", "wants_confirm": False})
    admit(db, reg, clock, source_event_id="b-2", event_type=ev.name,
          subject_refs={"meeting": "mc", "inviter": "a@bank.com", "wants_confirm": True})
    drain(db, reg, clock)
    assert world.emails.count(("a@bank.com", "confirm")) == 1     # one arm confirmed, one skipped
    assert sorted(world.commits) == ["sha-mb", "sha-mc"]


def test_subflow_composition_step_emits_fact():
    """The n8n 'Execute Workflow' equivalent: a parent step emits a fact; the child flow reacts.
    Parent and child are independently durable, independently inspectable."""
    db, reg, clock, world = rig()

    child_ev = EventType("child.requested")

    @reg.step
    def spawn_child(ctx):
        n = admit(db, reg, clock, source_event_id=f"child-of-{ctx.reaction.source_event_id}",
                  event_type=child_ev.name, subject_refs=ctx.refs)
        return Done({"children": n})

    reg.flow(name="child", version=1, on=child_ev, steps=[reg.steps["commit_summary"]])
    parent_ev = EventType("parent.requested")
    reg.flow(name="parent", version=1, on=parent_ev, steps=[reg.steps["spawn_child"]])

    admit(db, reg, clock, source_event_id="p-1", event_type=parent_ev.name,
          subject_refs={"meeting": "mp"})
    drain(db, reg, clock)
    rows = {f: s for _, f, s in
            [(r[0], r[1], r[2]) for r in db.execute("SELECT reaction_id, flow, status FROM reaction")]}
    assert rows == {"parent": "done", "child": "done"}
    assert world.commits == ["sha-mp"]
    # and the child is deduped like any fact: re-running the parent step can't double it
    admit(db, reg, clock, source_event_id="p-1", event_type=parent_ev.name, subject_refs={"meeting": "mp"})
    drain(db, reg, clock)
    assert world.commits == ["sha-mp"]


def test_error_workflow_reacts_to_failure_fact():
    """n8n's error workflow: when a reaction fails, the projection/reconciler layer can emit a
    `reaction.failed` fact; a NOTIFY flow reacts. Here the emission is explicit (the seam exists)."""
    db, reg, clock, world = rig()
    fail_ev = EventType("reaction.failed")

    @reg.step
    def notify_operator(ctx):
        world.emails.append(("ops@bank.com", f"failed:{ctx.refs['failed_flow']}"))
        return Done({})

    reg.flow(name="on_failure", version=1, on=fail_ev, steps=[reg.steps["notify_operator"]])

    world.fail_next["process_transcript"] = 99
    from fixtures import INVITE_REFS
    admit(db, reg, clock, source_event_id="e-1", event_type="invite.received", subject_refs=INVITE_REFS)
    world.meeting_state["m-1"] = {"completed": True, "final": True}
    drain(db, reg, clock)
    failed = db.execute("SELECT reaction_id, flow FROM reaction WHERE status='failed'")
    assert len(failed) == 1
    admit(db, reg, clock, source_event_id=f"fail-{failed[0][0]}", event_type=fail_ev.name,
          subject_refs={"failed_flow": failed[0][1]})
    drain(db, reg, clock)
    assert ("ops@bank.com", "failed:invite_to_summary") in world.emails


def test_version_coexistence_v1_runs_v1_while_new_events_take_v2():
    db, reg, clock, world = rig()
    ev = EventType("versioned.requested")

    @reg.step
    def v1_only(ctx):
        world.emails.append(("v1@bank.com", "v1"))
        return Done({})

    @reg.step
    def v2_only(ctx):
        world.emails.append(("v2@bank.com", "v2"))
        return Done({})

    reg.flow(name="vflow", version=1, on=ev, steps=[reg.steps["v1_only"], reg.steps["needs_approval"]])
    admit(db, reg, clock, source_event_id="v-1", event_type=ev.name, subject_refs={"meeting": "x"})
    tick(db, reg, clock)                                   # v1 runs step 1, blocks on approval
    # deploy v2: NEW registrations; the in-flight v1 reaction must keep v1 meaning
    reg.flow(name="vflow", version=2, on=ev, steps=[reg.steps["v2_only"]])
    admit(db, reg, clock, source_event_id="v-2", event_type=ev.name, subject_refs={"meeting": "y"})
    drain(db, reg, clock)
    assert ("v2@bank.com", "v2") in world.emails           # new event took v2
    from flows import resume
    rid = db.execute("SELECT reaction_id FROM reaction WHERE status='blocked'")[0][0]
    resume(db, rid, actor="owner", clock=clock)
    drain(db, reg, clock)
    assert status(db, rid)["flow"] == "vflow@1"            # old run finished under old meaning
    assert status(db, rid)["status"] == "done"
