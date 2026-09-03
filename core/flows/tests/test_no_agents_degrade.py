"""F-D20 — an absent capability DEGRADES a reaction; it does not ABORT it (PRD decision 40.7).

Found live on the dogfood stack, 2026-09-03, on the `no-agents` cut: an invite was admitted, the
iMIP ACCEPT went out, and then NOTHING. 2m43s past DTSTART there were zero rows in `meetings`.

`invite_intake@3`'s third step of nine is `ack_by_email`, declared `needs=("agent",)`, and the
engine's `not_present` handler is TERMINAL — *"the remaining steps are not run"*. So on a
deployment with no agent domain the reaction ended at step 3 with `done / agent:not_present`, and
`emit_prep`, `await_start`, `dispatch_bot`, `emit_started`, `run_meeting` and `emit_completed`
never ran. The bot never joined. `post_meeting@4` died the same way at its FIRST step
(`process_meeting`), so the "your meeting is recorded" mail never went either.

The module header of `flows_defs/production.py` promises, of the no-agents profile, that *"an
invite is still accepted, a bot still joins, a meeting is still recorded"*. It was false.

The fix is a DECLARATION, not a check inside a body (the rule `Registry.step` already states): a
step says what the absence of each domain it needs should DO.

  * `abort`   — the default, and every existing declaration keeps it: terminal, unchanged.
  * `skip`    — the engine does not enter the body, records `skipped: <domain>:not_present` on the
                receipt and in scratch, and ADVANCES to the next step.
  * `degrade` — the body runs and is TOLD (`ctx.absent`), because it can still do most of its job.

Three engine properties and three production ones, plus the two regression guards that matter
more than either: a step still declared `abort` still ends its reaction, and a deployment WITH the
agent domain behaves exactly as it did before this file existed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import flows_defs.production as production
import flows_steps.mailtext as mailtext
import pytest
from flows import Done, EventType, FakeClock, Registry, StepCtx, admit, status, tick
from sqlite_double import SqliteDB

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_link_loop import FakeChannel, FakeScaffolds, _StubDB  # noqa: E402

NO_AGENT = lambda d: d != "agent"          # noqa: E731 — the no-agents profile, as a predicate
EVERYTHING = lambda _d: True               # noqa: E731


# ── the engine ───────────────────────────────────────────────────────────────────────────────────

def _two_step_rig(absent=None):
    """One agent-needing step followed by one that needs nothing. The SECOND step is the whole
    point: `not_present` being terminal is invisible until something is meant to run after it."""
    reg, ran = Registry(), []

    @reg.step(needs=("agent",), absent=absent)
    def touch_the_agent(ctx: StepCtx):
        ran.append(("touch_the_agent", frozenset(ctx.absent)))
        return Done({"ok": True})

    @reg.step
    def afterwards(ctx: StepCtx):
        ran.append(("afterwards", frozenset(ctx.absent)))
        return Done({"ok": True})

    reg.flow(name="one", version=1, on=EventType("t.one"),
             steps=[touch_the_agent, afterwards])
    return reg, ran


def _drive(reg, present):
    db, clock = SqliteDB(), FakeClock()
    admit(db, reg, clock, source_event_id="e1", event_type="t.one", subject_refs={})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    for _ in range(20):
        if not tick(db, reg, clock, present=present):
            break
    return status(db, rid)


def test_a_skippable_absent_domain_is_recorded_and_the_reaction_continues():
    """THE DEFECT, at engine scale. Red before the fix: `ran` is empty and the reaction stops."""
    reg, ran = _two_step_rig(absent="skip")
    st = _drive(reg, NO_AGENT)

    assert [name for name, _ in ran] == ["afterwards"], \
        "the skipped body must not run, and the step after it must"
    assert st["status"] == "done"
    skipped = next(r for r in st["receipts"] if r["step"] == "touch_the_agent")
    assert skipped["state"] == "confirmed", "a skip is a recorded outcome, never an absent receipt"
    assert skipped["result"]["skipped"] == "agent:not_present"
    assert skipped["result"]["domain"] == "agent"


def test_the_skip_is_visible_in_the_reactions_own_scratch():
    """Both surfaces, because they answer different questions: the RECEIPT is what the timeline
    reads, the SCRATCH is what a later step in the same reaction reads (`email_minutes` has to
    know that `process_meeting` never wrote a report)."""
    reg, _ran = _two_step_rig(absent="skip")
    db, clock = SqliteDB(), FakeClock()
    admit(db, reg, clock, source_event_id="e1", event_type="t.one", subject_refs={})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    for _ in range(20):
        if not tick(db, reg, clock, present=NO_AGENT):
            break
    import json
    scratch = json.loads(db.execute(
        "SELECT scratch FROM reaction WHERE reaction_id = :r", {"r": rid})[0][0] or "{}")
    assert scratch["skipped"]["touch_the_agent"] == "agent:not_present"


def test_a_required_absent_domain_still_ends_the_reaction():
    """THE REGRESSION GUARD ON THE TERMINAL PATH. `abort` is the default and stays the default —
    a step that has nothing to degrade to (an agent turn, a bot dispatch) must still stop."""
    reg, ran = _two_step_rig(absent=None)
    st = _drive(reg, NO_AGENT)
    assert ran == [], "the body ran — the absent door was knocked on"
    assert st["status"] == "done" and st["reason"].startswith("agent:not_present")
    assert [r["step"] for r in st["receipts"]] == ["touch_the_agent"], \
        "a terminal not_present must not run the steps after it"


def test_a_degrading_absent_domain_runs_the_body_and_tells_it():
    """`email_minutes` is the reason this third policy exists: it still mails the person, it just
    cannot mint the chat link. A body that must ADAPT has to be entered, and has to be told."""
    reg, ran = _two_step_rig(absent="degrade")
    st = _drive(reg, NO_AGENT)
    assert [name for name, _ in ran] == ["touch_the_agent", "afterwards"]
    assert dict(ran)["touch_the_agent"] == frozenset({"agent"})
    assert dict(ran)["afterwards"] == frozenset(), "a step that declared nothing is told nothing"
    assert st["status"] == "done"


@pytest.mark.parametrize("absent", [None, "skip", "degrade"])
def test_every_policy_is_inert_when_the_domain_is_actually_there(absent):
    """The half a blanket change would break."""
    reg, ran = _two_step_rig(absent=absent)
    st = _drive(reg, EVERYTHING)
    assert [name for name, _ in ran] == ["touch_the_agent", "afterwards"]
    assert dict(ran)["touch_the_agent"] == frozenset()
    assert st["status"] == "done" and st["reason"] is None


def test_the_rename_carries_the_policy_with_the_function():
    """`reg.rename_step` already had to be taught to carry `needs`; the policy is keyed by the same
    name and `production` uses the closure-factory idiom twice."""
    reg = Registry()

    @reg.step(needs=("agent",), absent="skip")
    def _inner(ctx: StepCtx):
        return Done()

    reg.rename_step("_inner", "open_person")
    assert reg.absent_policy("open_person", "agent") == "skip"
    assert "_inner" not in reg.step_absent


def test_a_policy_for_a_domain_the_step_never_declared_is_a_registration_error():
    """A typo in a policy key would otherwise mean silently keeping the terminal behaviour — the
    exact failure mode this whole file is about, reintroduced one layer up."""
    reg = Registry()
    with pytest.raises(ValueError):
        @reg.step(needs=("agent",), absent={"meetings": "skip"})
        def _typo(ctx: StepCtx):
            return Done()


def test_an_unknown_policy_is_a_registration_error():
    reg = Registry()
    with pytest.raises(ValueError):
        @reg.step(needs=("agent",), absent="ignore")
        def _wrong(ctx: StepCtx):
            return Done()


# ── the production flows ─────────────────────────────────────────────────────────────────────────

def _production(monkeypatch):
    """The real registry, with the two domains that are NOT under test standing in for themselves:
    identity (`ensure_platform_user`) and meetings (the three bot steps). Everything the agent
    domain owns is left alone deliberately — a step that reaches it must be short-circuited by the
    engine, and `_forbid_the_agent_door` proves nothing did."""
    reg = Registry()
    production.build(reg, _StubDB())
    monkeypatch.setattr(production, "ensure_platform_user", lambda who: "7")
    monkeypatch.setattr(production, "setting", lambda uid, key: True)
    monkeypatch.setattr(production.mx, "send_rsvp_accept",
                        lambda *a, **k: "<rsvp@test>")
    monkeypatch.setattr(production.mx, "register_thread", lambda *a, **k: None)
    monkeypatch.setattr(mailtext, "ws_file", lambda *_a, **_k: None)

    reg.steps["await_start"] = lambda ctx: Done({"waited": True})
    reg.steps["dispatch_bot"] = lambda ctx: Done({"meeting_id": 41, "native": "abc123"})
    reg.steps["run_meeting"] = lambda ctx: Done({"completed": True})
    return reg


def _forbid_the_agent_door(monkeypatch):
    """Every helper that reaches agent-api, replaced with a landmine. A step the engine should have
    skipped both fails the test and says which door it went for."""
    def _forbidden(*a, **k):
        raise AssertionError(f"the agent door was knocked on: {a[:2]}")

    for attr in ("dispatch_turn", "collect_reply", "collect_outbox", "workspace_init",
                 "head_sha", "head_subjects", "workspace_write", "history"):
        monkeypatch.setattr(production.ag, attr, _forbidden)
    monkeypatch.setattr(production, "mint_scaffold", _forbidden)
    monkeypatch.setattr(production, "ws_file", _forbidden)
    monkeypatch.setattr(production, "scaffolded", _forbidden)


def _run_flow(reg, event: str, refs: dict, present):
    db, clock = SqliteDB(), FakeClock()
    emitted: list[tuple] = []

    def _emit(event_type, source_id, ref):
        emitted.append((event_type, source_id, ref))
        return 0

    admit(db, reg, clock, source_event_id="src-1", event_type=event, subject_refs=refs)
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    for _ in range(300):
        if not tick(db, reg, clock, emit=_emit, present=present):
            nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                             "WHERE status IN ('admitted','retrying')")[0][0]
            if nxt is None:
                break
            clock._t = max(clock._t, nxt)
    return status(db, rid), emitted


INVITE_REFS = {"organizer": "anna@bank.test", "ics_uid": "uid-1", "start": 1_700_000_000,
               "title": "Weekly sync", "url": "https://meet.test/abc"}
DONE_REFS = {"uid": "7", "meeting_id": 41, "native": "abc123", "title": "Weekly sync",
             "organizer": "anna@bank.test", "start": 1_700_000_000}


def test_invite_intake_reaches_emit_completed_with_no_agent_domain(monkeypatch):
    """THE HEADLINE. Red before the fix: the reaction ends at `ack_by_email` and the receipts stop
    there — no bot, no meeting, no `meeting.completed`."""
    reg = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    st, emitted = _run_flow(reg, "invite.received", dict(INVITE_REFS), NO_AGENT)

    steps = [r["step"] for r in st["receipts"]]
    assert st["status"] == "done", f"{st['step']}: {st['reason']}"
    for expected in ("rsvp_accept", "emit_prep", "dispatch_bot", "run_meeting", "emit_completed"):
        assert expected in steps, f"{expected} never ran — the reaction stopped at {st['step']}"
    ack = next(r for r in st["receipts"] if r["step"] == "ack_by_email")
    assert ack["result"]["skipped"] == "agent:not_present"
    assert [e[0] for e in emitted] == ["meeting.upcoming", "meeting.started", "meeting.completed"]


def test_post_meeting_still_mails_the_person_with_no_agent_domain(monkeypatch):
    """Decision 3's promise on the no-agents cut: after a meeting, the person is told it was
    recorded. Red before the fix: `process_meeting` is step one and the reaction ends there."""
    reg = _production(monkeypatch)
    _forbid_the_agent_door(monkeypatch)
    import flows_steps.notify as notify_mod
    ch = FakeChannel()
    notify_mod.use(ch)
    try:
        st, _ = _run_flow(reg, "meeting.completed", dict(DONE_REFS), NO_AGENT)
    finally:
        notify_mod.use(None)

    assert st["status"] == "done", f"{st['step']}: {st['reason']}"
    assert len(ch.sent) == 1, f"the minutes mail did not go: {ch.sent}"
    msg = ch.sent[0]
    assert msg["to"] == "anna@bank.test"
    assert msg["link"] is None, "there is no chat to link to on a deployment with no agent domain"
    # HONEST, and this is the half that is easy to get wrong: the meeting WAS recorded (the
    # meetings domain is deployed), and there are NO written minutes (the agent that writes them
    # is not). A mail that says "Minutes:" here is a claim about something that does not exist.
    assert "minutes" not in msg["subject"].lower()
    assert "recorded" in msg["body"].lower()
    pm = next(r for r in st["receipts"] if r["step"] == "process_meeting")
    assert pm["result"]["skipped"] == "agent:not_present"
    for step in ("email_attendees", "drop_to_attendees"):
        rec = next(r for r in st["receipts"] if r["step"] == step)
        assert rec["result"]["skipped"] == "agent:not_present"


def test_the_agent_present_path_is_unchanged(monkeypatch):
    """THE REGRESSION GUARD THAT DECIDES THE TRAIN. With agents deployed, `post_meeting` mails the
    report VERBATIM, with a minted scaffold link, exactly as it did before this branch."""
    reg = _production(monkeypatch)
    scaffolds = FakeScaffolds()
    monkeypatch.setattr(production, "mint_scaffold", scaffolds)
    monkeypatch.setattr(production, "ws_file", lambda *_a, **_k: None)
    monkeypatch.setattr(production, "scaffolded", lambda *_a, **_k: True)
    monkeypatch.setattr(production.ag, "workspace_init", lambda *_a, **_k: None)
    monkeypatch.setattr(production.ag, "workspace_write", lambda *_a, **_k: None)
    reg.steps["process_meeting"] = lambda ctx: Done({"report": "## Decided\n- ship it",
                                                     "group": ""})
    reg.steps["email_attendees"] = lambda ctx: Done({"sent": 0, "to": [], "drops": []})
    reg.steps["drop_to_attendees"] = lambda ctx: Done({"dropped": 0, "failed": []})

    import flows_steps.notify as notify_mod
    ch = FakeChannel()
    notify_mod.use(ch)
    try:
        st, _ = _run_flow(reg, "meeting.completed", dict(DONE_REFS), EVERYTHING)
    finally:
        notify_mod.use(None)

    assert st["status"] == "done", f"{st['step']}: {st['reason']}"
    assert len(ch.sent) == 1
    msg = ch.sent[0]
    assert msg["subject"].startswith("Minutes:")
    assert "## Decided" in msg["body"]
    assert msg["link"] and scaffolds.minted, "the scaffold link is the agent-present shape"
