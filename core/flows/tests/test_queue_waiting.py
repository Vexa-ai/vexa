"""PRD decision 42.2 — what is waiting IS flows, and a live call is a reaction like any other.

*"what is waiting — maybe it's flows?"* (founder, 2026-09-03 07:43Z; agreed.)

The queue a person's own agent reads used to be assembled at the edge from four sources — flows
reactions, the gateway's `/bots/status`, agent-api workspace files and a local friction log — with
two hundred lines of product copy in the tool body. Two of those four are agent-api reads, so the
verb could not ship in the `no-agents` product at all.

This file is the contract for the replacement, in the order the issue's acceptance table states it:

  A1  a finished meeting's reaction is in the queue — and NOTHING is, before the meeting finished
  A2  a live call is a pending reaction, and it is not in anybody else's queue
  A3  with the agent domain absent the queue still answers, and says which domain was absent
  A4  the subject is the authenticated caller's — a stamped identity beats a query argument
  A5  every item traces to a reaction id: the route cannot invent a row (the dropped friction ask)

Offline like the rest of the suite: real sqlite rows written the way the engine writes them, the
production definitions built against them, and — for A4 — the real app through `TestClient`, the
composition `tests/test_health.py` already uses.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_queue  # noqa: E402
from flows import FakeClock, Registry, SqliteDB, admit, status, tick  # noqa: E402

import flows_defs.production as production  # noqa: E402

T0 = 1_788_000_000.0
BEHAVIOR = Path(__file__).resolve().parents[3] / "behavior" / "queue"

MINE = {"uid": "126", "meeting_id": "104", "organizer": "dima@vexa.ai"}
THEIRS = {"uid": "999", "meeting_id": "7", "organizer": "someone@else.test"}


def _identity(subject):
    return ("126", "dima@vexa.ai") if str(subject) in ("126", "dima@vexa.ai") else ("999", "")


def _row(db, rid, refs, *, flow="post_meeting", step="process_meeting", status_="retrying",
         reason=None, at=T0):
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        reason, created_at, updated_at)
                  VALUES (:rid,:sid,'meeting.completed',:refs,:flow,4,:step,:st,0,0,:why,:c,:c)""",
               {"rid": rid, "sid": f"{rid}::{flow}", "refs": json.dumps(refs), "flow": flow,
                "step": step, "st": status_, "why": reason, "c": at})


# ── the typed reason: structural, never a keyword list ────────────────────────────────────────

def test_the_reason_type_comes_from_the_status_and_the_engine_s_own_prefix():
    """The rig's tool decided "ours or theirs" by searching the free-text reason for `smtp`,
    `timeout`, `535`… — English load-bearing inside a Python file. The type is derived from the
    row's STATUS and from the shape `flows/model.NotPresent` writes, and from nothing else."""
    assert flows_queue.typed_reason("blocked", "answer me")["type"] == flows_queue.TYPE_HUMAN
    assert flows_queue.typed_reason("failed", "smtp 535")["type"] == flows_queue.TYPE_FAILED
    assert flows_queue.typed_reason("retrying", None)["type"] == flows_queue.TYPE_PENDING

    got = flows_queue.typed_reason("done", "agent:not_present — this deployment does not run agent")
    assert got["type"] == flows_queue.TYPE_NOT_PRESENT
    assert got["domain"] == "agent"
    assert "does not run" in got["detail"]


def test_a_bare_not_present_reason_still_names_its_domain():
    got = flows_queue.typed_reason("done", "agent:not_present")
    assert (got["type"], got["domain"], got["detail"]) == (flows_queue.TYPE_NOT_PRESENT,
                                                           "agent", "")


# ── A1 · a finished meeting, and the negative control ─────────────────────────────────────────

def test_a_finished_meeting_puts_its_flow_in_the_queue():
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    out = flows_queue.waiting(db, subject="126", now=T0, identity=_identity)

    assert out["waiting"] == 1
    item = out["items"][0]
    assert item["id"] == "r-mine"
    assert item["flow"] == "post_meeting", "the item must name the flow that produced it"
    assert item["reason"]["type"] == flows_queue.TYPE_PENDING
    assert item["say"], "behavior/queue/post_meeting.pending.md is what a person hears"


def test_before_the_meeting_finished_nothing_is_waiting_and_nothing_is_invented():
    """THE NEGATIVE CONTROL for A1. An empty queue must be empty — never a summary of a meeting
    that has not happened, and never a greeting the route made up."""
    out = flows_queue.waiting(SqliteDB(), subject="126", now=T0, identity=_identity)
    assert out["waiting"] == 0 and out["items"] == [] and out["quiet"] == 0


def test_a_finished_reaction_that_simply_succeeded_is_not_waiting():
    db = SqliteDB()
    _row(db, "r-done", MINE, status_="done")
    assert flows_queue.waiting(db, subject="126", now=T0, identity=_identity)["waiting"] == 0


# ── A2 · scoping ──────────────────────────────────────────────────────────────────────────────

def test_a_subject_never_sees_another_person_s_queue():
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    _row(db, "r-theirs", THEIRS, status_="failed", reason="smtp 535 on their mailbox")

    mine = flows_queue.waiting(db, subject="126", now=T0, identity=_identity)
    assert [i["id"] for i in mine["items"]] == ["r-mine"]
    assert all("smtp" not in json.dumps(i) for i in mine["items"])


def test_an_unresolvable_subject_is_unresolved_not_everything():
    """FAIL CLOSED, exactly as `flows_timeline.list_reactions` does: falling back to the unscoped
    read is how a scoping bug becomes the leak the scope was added to close (R-D07/R-D12)."""
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    out = flows_queue.waiting(db, subject="nobody@nowhere.test", now=T0,
                              identity=lambda _s: ("", ""))
    assert out["unresolved"] is True and out["items"] == []


# ── A5 · the route cannot invent a row ────────────────────────────────────────────────────────

def test_every_item_traces_to_a_reaction_that_exists():
    """THE ENFORCEMENT of the dropped friction first-run ask (ruling 8/9). The old tool built a
    `tell_us` item out of a local file's line count — an item no reaction produced. Nothing here
    can: an item is a row, and it carries the row's id."""
    db = SqliteDB()
    _row(db, "r-mine", MINE)
    _row(db, "r-blocked", MINE, flow="desk_claim", step="await_claim", status_="blocked",
         reason="they use 'the rig' for the dogfood stack")
    out = flows_queue.waiting(db, subject="126", now=T0, identity=_identity)

    live = {r[0] for r in db.execute("SELECT reaction_id FROM reaction")}
    assert out["items"], "nothing to check"
    for item in out["items"]:
        assert item["id"] in live, f"{item['id']} is not a reaction on this instance"
    assert not any("friction" in json.dumps(i) for i in out["items"])


def test_no_copy_file_means_counted_not_spoken():
    """SILENCE IS THE FILTER. A parked `invite_intake` waiting for a call three hours from now is
    pending and is nobody's business; behavior decides that by having nothing to say, not a
    keyword list in here."""
    db = SqliteDB()
    _row(db, "r-plumbing", MINE, flow="invite_intake", step="await_start")
    out = flows_queue.waiting(db, subject="126", now=T0, identity=_identity)
    assert out["waiting"] == 0 and out["quiet"] == 1


def test_the_copy_comes_from_behavior_and_the_private_mount_wins(monkeypatch, tmp_path):
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "post_meeting.pending.md").write_text("the private tree's words")
    monkeypatch.setenv("VEXA_BEHAVIOR_DIR", str(tmp_path))
    assert flows_queue.say("post_meeting", "pending") == "the private tree's words"
    monkeypatch.delenv("VEXA_BEHAVIOR_DIR")
    assert flows_queue.say("post_meeting", "pending") != "the private tree's words"


def test_the_showcase_carries_a_file_for_every_reason_type_and_no_first_run_ask():
    for t in (flows_queue.TYPE_HUMAN, flows_queue.TYPE_FAILED, flows_queue.TYPE_NOT_PRESENT):
        assert (BEHAVIOR / f"_{t}.md").is_file(), t
    assert not (BEHAVIOR / "_pending.md").is_file(), (
        "a blanket `pending` file would speak every parked reaction — silence is the filter")
    assert not list(BEHAVIOR.glob("*friction*")), "the first-run friction ask is dropped (ruling 8)"


# ── the flows ─────────────────────────────────────────────────────────────────────────────────

class _StubDB:
    def execute(self, *a, **k):
        return []


def _registry(db) -> Registry:
    reg = Registry()
    production.build(reg, db)
    return reg


def _drain(db, reg, clock, present=None, budget=1000):
    for _ in range(budget):
        if tick(db, reg, clock, present=present):
            continue
        nxt = db.execute("SELECT MIN(next_run_at) FROM reaction "
                         "WHERE status IN ('admitted','retrying')")[0][0]
        if nxt is None:
            return
        clock._t = max(clock._t, nxt)


def test_meeting_started_is_a_registered_trigger_with_a_flow():
    """It was emitted by meetings and reacted to by nothing: `production.py` registered
    `invite.received`, `meeting.completed`, `meeting.upcoming` and `mail.reply` and no more."""
    reg = _registry(_StubDB())
    flows = reg.by_event.get(production.STARTED.name) or []
    assert [f.name for f in flows] == ["live_meeting"]
    assert reg.needs("attend_live") == frozenset(), (
        "the live step must reach no domain — it exists in every profile")


def test_invite_intake_publishes_meeting_started_at_a_new_version():
    """A step list is changed by ADDING a version — `match()` is newest-wins and a reaction in
    flight keeps the version it was admitted on."""
    reg = _registry(_StubDB())
    intake = [f for f in reg.by_event[production.INVITE.name] if f.name == "invite_intake"]
    newest = max(intake, key=lambda f: f.version)
    assert newest.version == 3
    assert "emit_started" in newest.steps
    assert newest.steps.index("emit_started") == newest.steps.index("dispatch_bot") + 1


def test_a_live_call_is_a_pending_reaction_and_it_clears_on_completion():
    """A2. While the call runs the reaction is pending and the queue speaks it; when the
    completion fact is admitted the reaction ends and the queue goes quiet."""
    db, clock = SqliteDB(), FakeClock()
    reg = _registry(db)
    # The agent domain absent, so the post_meeting reaction the completion also admits degrades
    # instead of dialling a door — this test is about the live one and must not depend on a socket.
    absent = lambda d: d != "agent"                                          # noqa: E731
    admit(db, reg, clock, source_event_id="live-104", event_type=production.STARTED.name,
          subject_refs={"uid": "126", "meeting_id": "104"})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]

    tick(db, reg, clock, present=absent)
    assert status(db, rid)["status"] == "retrying", "the step must stay pending while the call runs"

    out = flows_queue.waiting(db, subject="126", now=clock.now(), identity=_identity)
    assert [i["flow"] for i in out["items"]] == ["live_meeting"]
    assert out["items"][0]["say"], "behavior/queue/live_meeting.pending.md is what a person hears"

    # the negative control: it is not in anybody else's queue
    assert flows_queue.waiting(db, subject="999", now=clock.now(),
                               identity=_identity)["waiting"] == 0

    admit(db, reg, clock, source_event_id="done-104", event_type=production.COMPLETED.name,
          subject_refs={"uid": "126", "meeting_id": "104"})
    clock._t += production.LIVE_POLL_S + 1
    # More than one tick: the completion admitted a post_meeting reaction too, and  claims
    # ONE due reaction per call — which one is the claim order's business, not this test's.
    for _ in range(6):
        tick(db, reg, clock, present=absent)
    assert status(db, rid)["status"] == "done"


def test_a_completion_that_never_arrives_ends_the_live_reaction_rather_than_parking_forever():
    db, clock = SqliteDB(), FakeClock()
    reg = _registry(db)
    admit(db, reg, clock, source_event_id="live-999", event_type=production.STARTED.name,
          subject_refs={"uid": "126", "meeting_id": "999"})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    _drain(db, reg, clock)
    st = status(db, rid)
    assert st["status"] == "done" and st["receipts"][-1]["result"]["outcome"] == "lapsed"


def test_the_desk_cards_are_flows_and_they_need_the_agent_domain():
    reg = _registry(_StubDB())
    assert [f.name for f in reg.by_event[production.DESK_UNSCAFFOLDED.name]] == ["desk_setup"]
    assert [f.name for f in reg.by_event[production.CLAIM_PROPOSED.name]] == ["desk_claim"]
    assert reg.needs("await_scaffold") == frozenset({"agent"})
    assert reg.needs("await_claim") == frozenset({"agent"})


def test_a_desk_card_blocks_when_the_card_is_still_open(monkeypatch):
    db, clock = SqliteDB(), FakeClock()
    reg = _registry(db)
    monkeypatch.setattr(production, "scaffolded", lambda *a, **k: False)
    admit(db, reg, clock, source_event_id="desk-126",
          event_type=production.DESK_UNSCAFFOLDED.name, subject_refs={"uid": "126"})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    tick(db, reg, clock)
    assert status(db, rid)["status"] == "blocked"

    out = flows_queue.waiting(db, subject="126", now=clock.now(), identity=_identity)
    assert [i["reason"]["type"] for i in out["items"]] == [flows_queue.TYPE_HUMAN]
    assert out["items"][0]["say"]


def test_a_desk_card_that_was_answered_in_the_meantime_does_not_ask_again(monkeypatch):
    """The fact is old the moment it is published; the step re-reads the desk."""
    db, clock = SqliteDB(), FakeClock()
    reg = _registry(db)
    monkeypatch.setattr(production, "scaffolded", lambda *a, **k: True)
    admit(db, reg, clock, source_event_id="desk-126",
          event_type=production.DESK_UNSCAFFOLDED.name, subject_refs={"uid": "126"})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    tick(db, reg, clock)
    assert status(db, rid)["status"] == "done"


# ── A3 · the no-agents deployment ─────────────────────────────────────────────────────────────

def test_with_the_agent_domain_absent_the_queue_still_answers_and_says_which_domain(monkeypatch):
    """The row that decides whether this ships in the `no-agents` product at all (decision 40.6).
    Two of the old tool's four sources were agent-api reads."""
    db, clock = SqliteDB(), FakeClock()
    reg = _registry(db)

    def _forbidden(*a, **k):
        raise AssertionError("the agent door was knocked on")

    monkeypatch.setattr(production, "scaffolded", _forbidden)
    admit(db, reg, clock, source_event_id="desk-126",
          event_type=production.DESK_UNSCAFFOLDED.name, subject_refs={"uid": "126"})
    rid = db.execute("SELECT reaction_id FROM reaction")[0][0]
    tick(db, reg, clock, present=lambda d: d != "agent")

    st = status(db, rid)
    assert st["status"] == "done" and (st["reason"] or "").startswith("agent:not_present")

    out = flows_queue.waiting(db, subject="126", now=clock.now(), identity=_identity)
    assert [i["reason"]["type"] for i in out["items"]] == [flows_queue.TYPE_NOT_PRESENT]
    assert out["items"][0]["reason"]["domain"] == "agent"
    assert out["items"][0]["say"], "a person is owed a sentence about what did not happen"


def test_an_old_absence_stops_being_news():
    """Without a horizon a deployment that simply does not run a domain would tell the same person
    the same absence forever, which is noise rather than information."""
    db = SqliteDB()
    _row(db, "r-old", MINE, status_="done", reason="agent:not_present",
         at=T0 - flows_queue.NOT_PRESENT_WINDOW_S - 60)
    assert flows_queue.waiting(db, subject="126", now=T0, identity=_identity)["waiting"] == 0


# ── A4 · the subject is the authenticated caller's — through the real app ─────────────────────

_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "sqlite://"}


@pytest.fixture(scope="module")
def api():
    """The real app on the offline dialect — the composition `tests/test_health.py` documents.
    SET, IMPORT, RESTORE: the module reads these at import and keeps them as constants."""
    from fastapi.testclient import TestClient

    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ.update(_ENV)
    try:
        from flows_integrations import flows_api
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return flows_api, TestClient(flows_api.app)


def _seed(flows_api, monkeypatch):
    flows_api.db.execute("DELETE FROM reaction")
    _row(flows_api.db, "r-mine", MINE)
    _row(flows_api.db, "r-theirs", THEIRS, flow="post_meeting")
    monkeypatch.setattr(flows_queue, "resolve_identity", _identity)


def test_the_stamped_identity_wins_over_a_query_argument(api, monkeypatch):
    """A4, and it is the security row. The edge resolves identity once and stamps `X-User-Id`
    (the assembly design, § 7). A caller carrying their own bearer who names somebody else in a
    query argument must read their OWN queue."""
    flows_api, client = api
    _seed(flows_api, monkeypatch)
    r = client.get("/queue/waiting?subject=999",
                   headers={"X-Flows-Admin-Key": _ENV["VEXA_FLOWS_API_KEY"],
                            "X-User-Id": "126"})
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == ["r-mine"]


def test_the_unstamped_operator_read_still_reads_one_person(api, monkeypatch):
    flows_api, client = api
    _seed(flows_api, monkeypatch)
    r = client.get("/queue/waiting?subject=126",
                   headers={"X-Flows-Admin-Key": _ENV["VEXA_FLOWS_API_KEY"]})
    assert [i["id"] for i in r.json()["items"]] == ["r-mine"]


def test_no_subject_at_all_is_refused_not_answered_with_the_instance(api):
    _flows_api, client = api
    r = client.get("/queue/waiting",
                   headers={"X-Flows-Admin-Key": _ENV["VEXA_FLOWS_API_KEY"]})
    assert r.status_code == 400


def test_the_queue_is_behind_the_operator_key(api):
    _flows_api, client = api
    assert client.get("/queue/waiting?subject=126").status_code == 401


def test_the_answer_carries_the_flow_list_that_makes_a_short_queue_legible(api, monkeypatch):
    """§ 9 of the destination design: "nothing is waiting" and "that domain is not deployed" are
    answered by the flow list rather than by a field the tool invents."""
    flows_api, client = api
    _seed(flows_api, monkeypatch)
    body = client.get("/queue/waiting?subject=126",
                      headers={"X-Flows-Admin-Key": _ENV["VEXA_FLOWS_API_KEY"]}).json()
    assert "live_meeting" in {f["name"] for f in body["flows"]}


#: What this domain's manifest may claim to publish: an event some code in `src/` actually
#: produces. Producing is `ctx.emit(<EventType>.name, …)` in a flow definition or `admit(…,
#: event_type="…")` in an integration — the two ways a fact enters this engine from inside it.
def _produced() -> set:
    import re
    src = (Path(__file__).resolve().parents[1] / "src")
    text = "\n".join(f.read_text() for f in src.rglob("*.py"))
    consts = dict(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=\s*EventType\("([^"]+)"\)', text, re.M))
    out = set(re.findall(r'event_type="([^"]+)"', text))
    out |= {consts[c] for c in re.findall(r'\.emit\(\s*([A-Z][A-Z0-9_]*)\.name', text)
            if c in consts}
    return out


def test_the_manifest_only_claims_to_publish_what_this_domain_actually_produces():
    """A CONSUMED event in `publishes_events` is a lie about ownership, and it is the one this
    branch told: `desk.unscaffolded` and `claim.proposed` are the AGENT domain's to publish, and
    listing them here would register flows as their producer everywhere that field is read.

    The carrier census (`core/flows/contracts/flows.v1/carriers.json`) is where one-owner-per-event
    is enforced repository-wide once it lands; this is the same rule inside the domain, where it is
    cheaper to be wrong, and it is the check that would have caught it.

    F168/F181 moved `meeting.started` / `meeting.completed` out of `claimed` again, the day after
    this test started requiring `meeting.started` be in it. Both are still true at once: flows'
    OWN `invite_intake` v3 still runs `emit_started` / `emit_completed` (`_produced()` below still
    finds both — that code did not change), but `publishes_events` claims a DOMAIN, and
    `gate:config-contract` (scripts/gates.mjs) requires a manifest's claimed domain to equal the
    carrier census owner. meeting-api now ALSO publishes both (its own config.v1 publish-edge,
    `meeting_api/events.py` — the fix for an ad hoc, MCP-dispatched meeting, which ran no
    `invite_intake` reaction and so told flows nothing at all), and the census now records
    `meetings` as owner for both — a carrier has exactly one producing domain, on paper, even
    while flows' code keeps redundantly emitting the same fact under a `source_event_id` that
    dedups the two producers into one reaction. `handed_off_events` is where that ongoing,
    deliberately redundant production is written down instead."""
    doc = json.loads((Path(__file__).resolve().parents[1] / "mcp.tools.v1.json").read_text())
    claimed = {e["event"] for e in doc.get("publishes_events") or []}
    produced = _produced()
    assert claimed <= produced, (
        f"declared as published by flows but produced by nothing in core/flows/src: "
        f"{sorted(claimed - produced)}. An event this domain only REACTS to belongs in a flow "
        f"registration, never in publishes_events.")
    for consumed in ("desk.unscaffolded", "claim.proposed"):
        assert consumed not in claimed, f"{consumed} is the agent domain's to publish"
    handed_off = {e["event"] for e in doc.get("handed_off_events") or []}
    assert handed_off == {"meeting.started", "meeting.completed"}, (
        "the meetings handover (F168/F181) is not recorded where this test expects it")
    assert handed_off <= produced, (
        "a handed-off event is not produced by anything in core/flows/src any more — "
        "invite_intake's redundant emit_started/emit_completed was removed; drop it from "
        "handed_off_events too, since there is now exactly one producer and nothing to dedupe")
    assert not (claimed & handed_off), "an event cannot be both currently claimed and handed off"


def test_the_manifest_declares_the_tool_with_no_subject_argument():
    """`auth: subject` — the person is the authenticated caller, so there is no argument naming
    one. The field itself arrives with decision 15; the manifest carries it either way."""
    doc = json.loads((Path(__file__).resolve().parents[1] / "mcp.tools.v1.json").read_text())
    tool = next(t for t in doc["tools"] if t["name"] == "whats_waiting")
    assert tool["route"] == {"method": "GET", "path": "/queue/waiting"}
    assert tool["identity"] == "user" and tool["auth"] == "subject"
    assert "subject" not in (tool.get("arguments") or [])
