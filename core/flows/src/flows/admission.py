"""A fact becomes exactly one reaction PER MATCHING FLOW — idempotently, by constraint.

Three refusals sit in front of the insert since ADR-0046, and each is logged with counts — a fact
that started nothing must never be indistinguishable from one that started something:

* SCOPE (D8, PRD 0.13.4; was decision 4). A person's flow is admitted for an event whose subject is
  that person — `refs["uid"]` — OR whose AUDIENCE names that person — `refs["_audience"]`. The
  audience is computed by the producing domain and sealed into an event.v2 fact envelope
  (`fact_envelope.py`); the intake verifies the seal and only then sets `_audience` (and `_owner`,
  `_visibility`, `_lifecycle`). `admit()` strips those keys from whatever refs it is handed unless
  they arrive through `sealed=`, so no caller — the operator intake, a re-emitting step, the
  mailbox — can claim an audience. `ws:<slug>` entries name a workspace principal and never match a
  person. An event naming nobody, or somebody else, starts none of a person's flows.
* LOOP (decision 8). An event a flow's own step emitted carries `_origin`, the chain of flows that
  produced it. It cannot start a flow already in that chain more than `loop_limit` times, nor any
  flow once the chain is `max_hops` long.
* RUNS PER DAY (decision 9). A person's flows together start at most `runs_per_day` reactions in
  any 24 hours; a run over the cap is not admitted. Schedule fires and event-started runs are two
  buckets of that size: events about a person — another person's agent messaging them among
  them — can use up the event bucket and never the one their scheduled routines run from."""
from __future__ import annotations

import logging
import uuid
from collections import Counter

from .clock import Clock
from .db import DB, dumps, loads
from . import tickets
from .registry import Flow, Registry

_log = logging.getLogger(__name__)

#: The defaults the worker overrides from `config.v1` (`VEXA_FLOWS_LOOP_LIMIT`, `_MAX_HOPS`,
#: `_USER_RUNS_PER_DAY`). Here so the engine core keeps importing nothing but the stdlib.
LOOP_LIMIT = 1
MAX_HOPS = 8
RUNS_PER_DAY = 48
#: A person's runs a day of flows with NO agent step (ADR-0050 decision 4.4, `flow_light_runs_per_day`):
#: a bot sent at a time, a card, an event re-emitted — no model call, so a far larger budget.
LIGHT_RUNS_PER_DAY = 2000
LIGHT = "_light"
#: The steps that never call a model. A flow made only of these runs on the light budget.
LIGHT_STEPS = frozenset({"request_meeting_bot", "stop_bot", "post_message", "emit_event"})
MESSAGES_PER_DAY = 100
DAY_S = 86_400.0
#: The carrier an agent writes to another agent on (ADR-0046 decision 13), and how its id is keyed.
MESSAGE_EVENT = "thread.message"

#: What admission refused since this process started, by reason — counts, never row data.
REFUSED: Counter = Counter()

ORIGIN = "_origin"


def subject_of(refs: dict) -> str:
    """The person an event is about — the uid every carrier that names one names it by."""
    return str((refs or {}).get("uid") or "").strip()


#: The refs keys only a VERIFIED event.v2 envelope may set (D8). Stripped from every caller's refs.
AUDIENCE, OWNER, VISIBILITY, LIFECYCLE = "_audience", "_owner", "_visibility", "_lifecycle"
SEALED_KEYS = frozenset({AUDIENCE, OWNER, VISIBILITY, LIFECYCLE})
#: A workspace principal in an audience. It is not a person and admits no person's flow.
WORKSPACE_PREFIX = "ws:"


def strip_sealed(refs: dict) -> dict:
    """`refs` without the keys only a verified envelope may set — a caller cannot claim an audience."""
    return {k: v for k, v in (refs or {}).items() if k not in SEALED_KEYS}


def sealed_refs(envelope: dict) -> dict:
    """The refs a VERIFIED envelope contributes (`fact_envelope.verify`'s answer, never raw input)."""
    out = {AUDIENCE: [str(a) for a in envelope.get("audience") or []],
           OWNER: str(envelope.get("owner") or ""),
           VISIBILITY: str(envelope.get("visibility") or "")}
    if envelope.get("object") == "card" and isinstance(envelope.get("lifecycle"), dict):
        out[LIFECYCLE] = dict(envelope["lifecycle"])
    return out


def audience_of(refs: dict) -> list[str]:
    """The PERSONS a sealed envelope says this fact reaches — `ws:` principals excluded."""
    aud = (refs or {}).get(AUDIENCE) or []
    if not isinstance(aud, list):
        return []
    return [str(a).strip() for a in aud
            if isinstance(a, (str, int)) and str(a).strip() and not str(a).startswith(WORKSPACE_PREFIX)]


def owner_of(refs: dict) -> str:
    """Whose fact this is: the sealed `_owner` when the producer sealed one (D8), else the subject
    (`refs.uid`) — the interim rule (BL-4) for producers that do not seal yet."""
    return str((refs or {}).get(OWNER) or "").strip() or subject_of(refs)


def reaches(person: str, refs: dict) -> bool:
    """Does this fact reach `person`? Subject ∪ audience (D8)."""
    person = str(person or "").strip()
    return bool(person) and (subject_of(refs) == person or person in audience_of(refs))


def origin_chain(refs: dict) -> list[str]:
    chain = (refs or {}).get(ORIGIN) or []
    return [str(x) for x in chain] if isinstance(chain, list) else []


def _refuse(reason: str, flow: Flow, event_type: str, refusals: "list | None" = None) -> None:
    REFUSED[reason] += 1
    if refusals is not None:
        refusals.append(reason)
    _log.warning("admission refused %s for %s@%d on %s — %d refused for this reason so far",
                 reason, flow.ident, flow.version, event_type, REFUSED[reason])


SCHEDULE_EVENT = "schedule.due"


def is_light(flow: Flow) -> bool:
    """A person's flow none of whose steps thinks: it runs on the light budget, never the agent's."""
    return bool(flow.owner) and bool(flow.steps) and all(s in LIGHT_STEPS for s in flow.steps)


def light_runs_today(db: DB, owner: str, now: float) -> int:
    rows = db.execute("SELECT COUNT(*) FROM reaction WHERE flow LIKE :p AND created_at > :since "
                      "AND subject_refs LIKE :light",
                      {"p": f"user:{owner}/%", "since": now - DAY_S, "light": '%"_light":true%'})
    return int(rows[0][0] or 0) if rows else 0


def runs_today(db: DB, owner: str, now: float, *, scheduled: "bool | None" = None) -> int:
    """How many reactions this person's flows started in the last 24 hours — all of them, or only
    the schedule fires (`scheduled=True`), or only the event-started runs (`scheduled=False`)."""
    sql = ("SELECT COUNT(*) FROM reaction WHERE flow LIKE :p AND created_at > :since "
           "AND event_type <> 'schedule.refused' "
           # a sink's event card is not a run; only the wakes it caused are
           "AND NOT (step = 'post_event_card' AND COALESCE(scratch, '') NOT LIKE :woke) "
           # a light run (no agent step) counts on its own budget, never this one
           "AND subject_refs NOT LIKE :light")
    if scheduled is True:
        sql += " AND event_type = :sched"
    elif scheduled is False:
        sql += " AND event_type <> :sched"
    # the pattern is BOUND, never inlined: a literal `:true` in the SQL text is a bind parameter to
    # SQLAlchemy, and GET /flows answered 500 on Postgres for it (the SQLite double did not mind)
    params = {"p": f"user:{owner}/%", "since": now - DAY_S, "woke": '%"woke":true%',
              "light": '%"_light":true%'}
    if scheduled is not None:
        params["sched"] = SCHEDULE_EVENT
    rows = db.execute(sql, params)
    return int(rows[0][0] or 0) if rows else 0


def next_run_possible(db: DB, owner: str, now: float, *, runs_per_day: int,
                      scheduled: bool = True) -> "float | None":
    """When this person's next run fits under the daily cap again: the moment the oldest run still
    inside the 24-hour window leaves it. None when the cap is not reached."""
    sql = ("SELECT created_at FROM reaction WHERE flow LIKE :p AND created_at > :since "
           "AND event_type " + ("= :sched" if scheduled else "<> :sched") +
           " ORDER BY created_at DESC LIMIT 1 OFFSET :k")
    rows = db.execute(sql, {"p": f"user:{owner}/%", "since": now - DAY_S, "sched": SCHEDULE_EVENT,
                            "k": max(int(runs_per_day) - 1, 0)})
    return float(rows[0][0]) + DAY_S if rows else None


def messages_today(db: DB, sender: str, now: float) -> int:
    """How many agent-to-agent messages this person's agents sent in the last 24 hours."""
    rows = db.execute("SELECT COUNT(*) FROM reaction WHERE event_type = :et AND "
                      "source_event_id LIKE :p AND created_at > :since",
                      {"et": MESSAGE_EVENT, "p": f"msg:{sender}:%", "since": now - DAY_S})
    return int(rows[0][0] or 0) if rows else 0


def admissible(db: DB, flow: Flow, clock: Clock, *, event_type: str, subject_refs: dict,
               loop_limit: int = LOOP_LIMIT, max_hops: int = MAX_HOPS,
               runs_per_day: int = RUNS_PER_DAY, messages_per_day: int = MESSAGES_PER_DAY,
               refusals: "list | None" = None,
               light_runs_per_day: int = LIGHT_RUNS_PER_DAY) -> bool:
    """May this fact start this flow? Every `False` is counted and logged, and named in
    `refusals` when the caller passes a list to hear the reason."""
    chain = origin_chain(subject_refs)
    if chain and len(chain) >= max_hops:
        _refuse("loop_max_hops", flow, event_type, refusals)
        return False
    if chain.count(flow.ident) >= loop_limit:
        _refuse("loop_limit", flow, event_type, refusals)
        return False
    if event_type == MESSAGE_EVENT:
        sender = str((subject_refs or {}).get("sender_uid") or "")
        if messages_per_day >= 0 and messages_today(db, sender, clock.now()) >= messages_per_day:
            _refuse("quota_messages_per_day", flow, event_type, refusals)
            return False
    if flow.owner:
        if event_type != SCHEDULE_EVENT and not reaches(flow.owner, subject_refs):
            REFUSED["not_subject"] += 1          # the ordinary case for every other person's event
            return False
        if flow.steps and flow.steps[0] == "post_event_card":
            return True                       # a card, not a run: the sink caps its own cards
        if is_light(flow):
            if light_runs_per_day >= 0 and light_runs_today(db, flow.owner, clock.now()) >= light_runs_per_day:
                _refuse("quota_light_runs_per_day", flow, event_type, refusals)
                return False
            return True
        if runs_per_day >= 0 and runs_today(db, flow.owner, clock.now(),
                                            scheduled=event_type == SCHEDULE_EVENT) >= runs_per_day:
            _refuse("quota_runs_per_day", flow, event_type, refusals)
            return False
    return True


def insert(db: DB, clock: Clock, flow: Flow, *, key: str, event_type: str,
           subject_refs: dict) -> list:
    now = clock.now()
    if is_light(flow) and not subject_refs.get(LIGHT):
        subject_refs = {**subject_refs, LIGHT: True}
    return db.execute(
        """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                 flow, flow_version, step, status, attempt, next_run_at,
                                 created_at, updated_at)
           VALUES (:rid, :sid, :et, :refs, :flow, :ver, :step, 'admitted', 0, :now, :now, :now)
           ON CONFLICT (source_event_id) DO NOTHING
           RETURNING reaction_id""",
        {"rid": uuid.uuid4().hex, "sid": key, "et": event_type, "refs": dumps(subject_refs),
         "flow": flow.ident, "ver": flow.version, "step": flow.steps[0], "now": now})


def admit(db: DB, registry: Registry, clock: Clock, *, source_event_id: str,
          event_type: str, subject_refs: dict, loop_limit: int = LOOP_LIMIT,
          max_hops: int = MAX_HOPS, runs_per_day: int = RUNS_PER_DAY,
          messages_per_day: int = MESSAGES_PER_DAY, refusals: "list | None" = None,
          light_runs_per_day: int = LIGHT_RUNS_PER_DAY,
          keyed_per_user: "int | None" = None, sealed: "dict | None" = None) -> int:
    """Returns how many reactions were newly created (0 = duplicate, no matching flow, or every
    matching flow refused — see the module docstring). A flow whose trigger has a FILTER starts only
    for events that pass it; a RELATIVE trigger starts nothing here — it arms, moves or cancels its
    keyed schedule (`flows.keyed`), which the worker fires at its time.

    `sealed` is a VERIFIED event.v2 envelope (`fact_envelope.verify`'s answer). Only through it do
    `_audience`/`_owner`/`_visibility`/`_lifecycle` reach the refs; the same keys in
    `subject_refs` are dropped first, whoever sent them (D8)."""
    from . import keyed, triggers
    subject_refs = strip_sealed(subject_refs)
    if sealed:
        subject_refs = {**subject_refs, **sealed_refs(sealed)}
    created = 0
    for flow in registry.match(event_type):
        spec = triggers.spec_of(flow)
        if spec.get("kind") == "relative":
            _keyed(db, clock, flow, spec, event_type, subject_refs, loop_limit=loop_limit,
                   max_hops=max_hops, per_user=keyed.PER_USER if keyed_per_user is None
                   else keyed_per_user)
            continue
        if spec.get("filter") and not triggers.passes(spec["filter"], event_type, subject_refs):
            REFUSED["filtered"] += 1
            continue
        if not admissible(db, flow, clock, event_type=event_type, subject_refs=subject_refs,
                          loop_limit=loop_limit, max_hops=max_hops, runs_per_day=runs_per_day,
                          messages_per_day=messages_per_day, refusals=refusals,
                          light_runs_per_day=light_runs_per_day):
            continue
        # one reaction per (fact, flow): the flow's IDENTITY joins the dedup key so a second flow
        # on the same event admits independently while a redelivery of the same fact stays a no-op.
        # The instance's flows keep their bare name here, so every key written before scope
        # existed still deduplicates against its redelivery.
        key = f"{source_event_id}::{flow.ident}"
        rows = insert(db, clock, flow, key=key, event_type=event_type, subject_refs=subject_refs)
        if rows:
            created += len(rows)
            continue
        _merge_into_admitted(db, clock, key=key, source_event_id=source_event_id,
                              incoming_refs=subject_refs)
    return created


def _keyed(db: DB, clock: Clock, flow: Flow, spec: dict, event_type: str, refs: dict, *,
           loop_limit: int, max_hops: int, per_user: int) -> str:
    """A relative trigger's event: arm, move or cancel the pending fire for its key."""
    from . import keyed, triggers
    if flow.owner and not reaches(flow.owner, refs):
        REFUSED["not_subject"] += 1
        return "not_subject"
    chain = origin_chain(refs)
    if (chain and len(chain) >= max_hops) or chain.count(flow.ident) >= loop_limit:
        _refuse("loop_limit", flow, event_type)
        return "loop"
    key = triggers.key_of(spec.get("key") or "", event_type, refs)
    if not key:
        REFUSED["keyed_no_key"] += 1
        return "no_key"
    at = triggers.fire_at(spec.get("at") or "", event_type, refs)
    gone = event_type.endswith(".cancelled") or str(refs.get("status") or "") == "cancelled"
    passes = not spec.get("filter") or triggers.passes(spec["filter"], event_type, refs)
    if gone or not passes or at is None:
        keyed.cancel(db, clock, flow, key=key)
        return "cancelled"
    return keyed.upsert(db, clock, flow, key=key, fire_at=at, event_type=event_type,
                        payload={k: v for k, v in refs.items() if k != ORIGIN}, per_user=per_user)


def _merge_into_admitted(db: DB, clock: Clock, *, key: str, source_event_id: str,
                          incoming_refs: dict) -> None:
    """A SECOND submission for a fact already admitted to this flow — the shape #1502 introduced:
    meeting-api now publishes `meeting.started`/`meeting.completed` with the SAME
    `source_event_id` scheme `invite_intake`'s own `emit_started`/`emit_completed` steps use
    (`live-<id>` / `done-<id>`), so the two producers race for one reaction. Whichever loses the
    race used to be discarded outright — `ON CONFLICT DO NOTHING` and nothing else — so when
    meeting-api's bare `{uid, meeting_id, platform, native_meeting_id, completion_reason}` won,
    `post_meeting`'s `process_meeting` lost the invite's `participants`/`participant_names` and
    its room-read degraded to empty instead of the invite's attendee order.

    The fix: MERGE the loser's `refs` into the winner's `subject_refs` — new keys are added,
    an existing key is kept UNLESS its current value is empty and the incoming one is not (an
    empty value never overwrites a non-empty one, in EITHER direction — the merge is order-
    independent by construction). The reaction's `step`/`status`/`flow_version` are untouched;
    this only ever widens what the flow already knows. The merge itself is recorded in the
    reaction's `scratch` under `ref_merges` so it is visible on the row, not just inferred from
    the fact that `subject_refs` grew.

    Read-then-write, not a single atomic statement: `subject_refs`/`scratch` are opaque JSON
    text columns (no `jsonb` on the sqlite test double), so the merge happens in Python. A
    concurrent second race on the SAME key is no worse than admission's own pre-fix behavior —
    both are last-write-wins on this path, and the ON CONFLICT insert above is still the only
    thing that is atomic. Scoped to the one dedup-collision bug this session was asked to fix,
    not a general concurrency hardening pass over admission."""
    rows = db.execute("SELECT reaction_id, subject_refs, scratch FROM reaction "
                       "WHERE source_event_id = :sid", {"sid": key})
    if not rows:
        return  # the winner is gone (e.g. reconciler cleanup) — nothing to merge into
    reaction_id, existing_refs_json, scratch_json = rows[0]
    merged, added_keys = _merge_refs(loads(existing_refs_json), incoming_refs)
    if not added_keys:
        return  # nothing this admission knows that the existing reaction didn't already — no-op
    scratch = loads(scratch_json) if scratch_json else {}
    merges = list(scratch.get("ref_merges") or [])
    merges.append({"from_source_event_id": source_event_id, "added_keys": sorted(added_keys),
                   "at": clock.now()})
    scratch["ref_merges"] = merges
    db.execute(
        """UPDATE reaction SET subject_refs = :refs, scratch = :scratch, updated_at = :now
           WHERE reaction_id = :rid""",
        {"refs": dumps(merged), "scratch": dumps(scratch), "now": clock.now(), "rid": reaction_id})


def _merge_refs(existing: dict, incoming: dict) -> tuple[dict, list[str]]:
    """New keys from `incoming` are added. A key already in `existing` is kept as-is UNLESS its
    value is empty (falsy) and the `incoming` value for that key is not — an empty value never
    overwrites a non-empty one, whichever side arrived first. The subject tickets (`_tickets`) are
    the exception: both deliveries' tickets are kept (union). Returns (merged, keys_that_changed)
    so the caller can skip a no-op write and record exactly what a merge added."""
    merged = dict(existing)
    changed: list[str] = []
    for k, v in incoming.items():
        if k == tickets.REFS_KEY:
            # A SECOND DELIVERY'S TICKETS ARE ADDED, never dropped: meeting-api's `meeting.completed`
            # and invite_join's own emit name the same fact, and each producer signs its own.
            both = tickets.union(tickets.of(merged), tickets.of(incoming))
            if both != tickets.of(merged):
                merged[k] = both
                changed.append(k)
            continue
        if k not in merged or (not merged[k] and v):
            if merged.get(k) != v:
                merged[k] = v
                changed.append(k)
    return merged, changed
