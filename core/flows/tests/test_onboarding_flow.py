"""THE ONBOARDING FLOW — a new person's first step, as a queue row.

Founder, 2026-09-04: *"whats_waiting — that's the one agent must call after got installed and that
one will prompt them to try a meeting"* · *"we want them to try a meeting so they are activated.
Activated meaning 1 meeting with transcription"*.

Every test here is about one of the two things that can silently be wrong: WHO the row is about
(a scoping bug returns somebody else's meeting and clears the item for a person who has done
nothing), and WHAT counts as done (a completion with no transcript clears the item on the one run
that proved least).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flows import Done, FakeClock, Reaction, StepCtx, Wait, admit
from flows_defs import production

#: PATCHED BY NAME, never through a module object bound at import.
#: `tests/test_flows_api_service.py` deletes every `flows_defs*` / `flows_steps*` entry from
#: `sys.modules` to prove `production` composes from a cold start — so a `from flows_steps import
#: meeting as mt` here would hold the module that existed at COLLECTION time while the `registry`
#: fixture, running later, imports a fresh one. The patch would land on a module nothing calls, and
#: the step would answer `Wait` for a reason no assertion mentions. The string form resolves the
#: import at patch time, which is the same module `production.build` just took.
SEGMENT_COUNT = "flows_steps.meeting.transcript_segment_count"


ONBOARDED = "onboarding.completed"
COMPLETED = "meeting.completed"


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────
def _admit(db, registry, clock, *, event_type, source_event_id, refs):
    return admit(db, registry, clock, source_event_id=source_event_id,
                 event_type=event_type, subject_refs=refs)


def _row(db, flow):
    rows = db.execute("SELECT reaction_id, step, status, subject_refs FROM reaction "
                      "WHERE flow = :f", {"f": flow})
    return rows[0] if rows else None


def _ctx(db, flow, *, clock, scratch=None):
    """A StepCtx over the real admitted row — the same shape `flows.loop` builds."""
    rid, step, status, refs = _row(db, flow)
    reaction = Reaction(reaction_id=rid, source_event_id="", event_type=ONBOARDED,
                        subject_refs=json.loads(refs), flow=flow, flow_version=1, step=step,
                        status=status, attempt=0, next_run_at=0.0, blocked_deadline=None,
                        lease_until=None, reason=None)
    return StepCtx(reaction=reaction, effect_key="k", prior={}, clock_now=clock.now(),
                   scratch={} if scratch is None else scratch)


def _seed_completion(db, clock, *, meeting_id, uid, source=None):
    """A `meeting.completed` reaction row, with the refs meeting-api actually publishes
    (`meeting_api/events.py meeting_completed_refs`) — `uid`, never `subject`."""
    db.execute(
        "INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs, flow, "
        "flow_version, step, status, attempt, next_run_at, created_at, updated_at) "
        "VALUES (:r,:s,:e,:refs,'post_meeting',4,'process_meeting','admitted',0,:t,:t,:t)",
        {"r": f"r-{meeting_id}", "s": source or f"done-{meeting_id}::post_meeting",
         "e": COMPLETED, "t": clock.now(),
         "refs": json.dumps({"uid": str(uid), "meeting_id": str(meeting_id),
                             "native": "abc-def-ghi", "platform": "google_meet"})})


# ── registration ─────────────────────────────────────────────────────────────────────────────────
def test_the_flow_is_registered_on_onboarding_completed(registry):
    flow = registry.flows[("onboarding", 1)]
    assert flow.on.name == ONBOARDED
    assert flow.steps == ("first_meeting",)


def test_it_needs_no_agent_domain(registry):
    """THE PROPERTY THAT PUTS IT IN THIS PRODUCT. The `registry` fixture composes the registry
    with no agent door named, so a `needs=("agent",)` here would answer `agent:not_present` on the
    first tick of every new person in the no-agents deployment this flow exists for."""
    assert "agent" not in registry.needs("first_meeting")
    assert "meetings" in registry.needs("first_meeting")


# ── admission ────────────────────────────────────────────────────────────────────────────────────
def test_admits_on_onboarding_completed(db, registry, clock):
    n = _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
               refs={"subject": "77", "org": "", "seat": "member"})
    assert n == 1
    rid, step, status, _refs = _row(db, "onboarding")
    assert (step, status) == ("first_meeting", "admitted")


def test_a_redelivery_admits_nothing(db, registry, clock):
    for _ in range(3):
        _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
               refs={"subject": "77"})
    assert len(db.execute("SELECT 1 FROM reaction WHERE flow = 'onboarding'", {})) == 1


# ── the step ─────────────────────────────────────────────────────────────────────────────────────
def test_pending_while_the_person_has_no_meeting(db, registry, clock):
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    out = registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock))
    assert isinstance(out, Wait)
    assert out.seconds == production.ONBOARDING_POLL_S


def test_completes_when_their_meeting_transcribed(db, registry, clock, monkeypatch):
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    _seed_completion(db, clock, meeting_id="501", uid="77")
    monkeypatch.setattr(SEGMENT_COUNT, lambda uid, mid: 42)
    out = registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock))
    assert isinstance(out, Done)
    assert out.result == {"meeting_id": "501", "segments": 42, "activated": True}


def test_a_completion_with_no_transcript_does_not_activate(db, registry, clock, monkeypatch):
    """ACTIVATION IS A TRANSCRIPT, NOT A MEETING. A bot that joined an empty room and left showed
    this person nothing, and clearing their first-step item on it tells them they are finished on
    the one run that proved least."""
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    _seed_completion(db, clock, meeting_id="501", uid="77")
    monkeypatch.setattr(SEGMENT_COUNT, lambda uid, mid: 0)
    assert isinstance(registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock)), Wait)


def test_a_silent_meeting_is_read_once_and_remembered(db, registry, clock, monkeypatch):
    """The scratch is durable, so a meeting already read as silent must never be read again — over
    the days this item can be pending that is one read per meeting instead of one per tick."""
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    _seed_completion(db, clock, meeting_id="501", uid="77")
    calls = []
    monkeypatch.setattr(SEGMENT_COUNT,
                        lambda uid, mid: calls.append(mid) or 0)
    scratch: dict = {}
    for _ in range(4):
        registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock, scratch=scratch))
    assert calls == ["501"]
    assert scratch["silent_meetings"] == {"501": 0}


def test_an_unreadable_transcript_is_not_a_silent_meeting(db, registry, clock, monkeypatch):
    """`None` is a gateway that was restarting, not a person who never tried — so it is asked
    again, and a later successful read still activates them."""
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    _seed_completion(db, clock, meeting_id="501", uid="77")
    answers = [None, None, 7]
    monkeypatch.setattr(SEGMENT_COUNT, lambda uid, mid: answers.pop(0))
    scratch: dict = {}
    assert isinstance(registry.steps["first_meeting"](
        _ctx(db, "onboarding", clock=clock, scratch=scratch)), Wait)
    assert isinstance(registry.steps["first_meeting"](
        _ctx(db, "onboarding", clock=clock, scratch=scratch)), Wait)
    assert scratch.get("silent_meetings") == {}
    out = registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock, scratch=scratch))
    assert isinstance(out, Done) and out.result["segments"] == 7


def test_another_persons_meeting_does_not_complete_it(db, registry, clock, monkeypatch):
    """THE SCOPING TEST. A queue that clears one person's first step on somebody else's meeting is
    also a queue that would SHOW them somebody else's meeting."""
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    _seed_completion(db, clock, meeting_id="900", uid="88")
    monkeypatch.setattr(SEGMENT_COUNT, lambda uid, mid: 99)
    assert isinstance(registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock)), Wait)


def test_a_fact_with_no_subject_is_refused_loudly(db, registry, clock):
    from flows import StepError
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-x",
           refs={"org": "", "seat": "member"})
    with pytest.raises(StepError) as e:
        registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock))
    assert not e.value.retryable


def test_the_subject_may_also_arrive_spelled_uid(db, registry, clock, monkeypatch):
    """`onboarding.completed` spells it `subject` (identity's `onboarding_refs`) and every meetings
    fact spells the same value `uid`. Reading one name only is how a flow scopes to nobody."""
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"uid": "77"})
    _seed_completion(db, clock, meeting_id="501", uid="77")
    monkeypatch.setattr(SEGMENT_COUNT, lambda uid, mid: 3)
    assert isinstance(registry.steps["first_meeting"](_ctx(db, "onboarding", clock=clock)), Done)


# ── the words ────────────────────────────────────────────────────────────────────────────────────
def test_the_queue_resolves_words_for_this_flow():
    """`flows_queue.say` is what turns a pending row into an item a person hears about, and a
    SILENT flow is COUNTED rather than spoken — so a missing file here is not a missing sentence,
    it is an item that never appears at all."""
    import flows_queue
    words = flows_queue.say("onboarding", flows_queue.TYPE_PENDING)
    assert words, "behavior/queue/onboarding.pending.md resolved to nothing"
    lowered = words.lower()
    for token in ("meet.new", "request_meeting_bot", "get_meeting_transcript",
                  "since_index", "stop_bot"):
        assert token in lowered, f"the onboarding words never name {token}"


def test_the_words_file_is_where_the_queue_looks():
    root = Path(__file__).resolve().parents[3] / "behavior" / "queue"
    assert (root / "onboarding.pending.md").is_file()


def test_a_private_tree_overrides_the_words(tmp_path, monkeypatch):
    """EXTENSION POINT (2/3): a deployment's own queue words, no image rebuild. It already worked —
    `flows_queue._roots()` reads `$VEXA_BEHAVIOR_DIR/queue/` before the baked showcase — and this
    test exists so a private pack can rely on it rather than discover it."""
    import flows_queue
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "onboarding.pending.md").write_text("PRIVATE WORDS", encoding="utf-8")
    monkeypatch.setenv("VEXA_BEHAVIOR_DIR", str(tmp_path))
    assert flows_queue.say("onboarding", flows_queue.TYPE_PENDING) == "PRIVATE WORDS"


def test_a_pending_onboarding_row_becomes_one_spoken_item(db, registry, clock):
    """END TO END OVER THE PROJECTION `whats_waiting` serves: admitted fact → pending row →
    exactly one item, carrying the flow, the step and the words."""
    import flows_queue
    _admit(db, registry, clock, event_type=ONBOARDED, source_event_id="onboarding-77",
           refs={"subject": "77"})
    out = flows_queue.waiting(db, subject="77", now=clock.now(),
                              identity=lambda s: (str(s), ""))
    assert out["waiting"] == 1
    item = out["items"][0]
    assert item["flow"] == "onboarding"
    assert item["step"] == "first_meeting"
    assert item["reason"]["type"] == flows_queue.TYPE_PENDING
    assert "meet.new" in item["say"].lower()


# ── the schema this suite's double stands in for ─────────────────────────────────────────────────
def test_the_double_carries_the_reactions_columns_the_engine_writes(db):
    """`sqlite_double` builds itself FROM `schema.sql` through one textual mapping (`double
    precision` → `REAL`), so the two cannot drift by editing — but they can still drift by that
    mapping dropping a column on a spelling sqlite parses differently. The column set is what every
    test above stands on, so it is read back off the live table rather than off the DDL text."""
    import re
    ddl = (Path(__file__).resolve().parents[1] / "schema.sql").read_text()
    body = re.search(r"CREATE TABLE IF NOT EXISTS reaction \((.*?)\n\);", ddl, re.S).group(1)
    real = {l.strip().split()[0] for l in body.splitlines()
            if l.strip() and not l.strip().startswith(("PRIMARY", "CONSTRAINT", "UNIQUE"))}
    built = {r[1] for r in db.execute("PRAGMA table_info(reaction)")}
    assert real == built
