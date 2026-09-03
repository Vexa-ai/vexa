"""A fact becomes exactly one reaction PER MATCHING FLOW — idempotently, by constraint."""
from __future__ import annotations

import uuid

from .clock import Clock
from .db import DB, dumps, loads
from .registry import Registry


def admit(db: DB, registry: Registry, clock: Clock, *, source_event_id: str,
          event_type: str, subject_refs: dict) -> int:
    """Returns how many reactions were newly created (0 = duplicate or no matching flow)."""
    created = 0
    for flow in registry.match(event_type):
        # one reaction per (fact, flow): the flow name joins the dedup key so a second flow on the
        # same event admits independently while a redelivery of the same fact stays a no-op.
        key = f"{source_event_id}::{flow.name}"
        now = clock.now()
        rows = db.execute(
            """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                     flow, flow_version, step, status, attempt, next_run_at,
                                     created_at, updated_at)
               VALUES (:rid, :sid, :et, :refs, :flow, :ver, :step, 'admitted', 0, :now, :now, :now)
               ON CONFLICT (source_event_id) DO NOTHING
               RETURNING reaction_id""",
            {"rid": uuid.uuid4().hex, "sid": key, "et": event_type, "refs": dumps(subject_refs),
             "flow": flow.name, "ver": flow.version, "step": flow.steps[0], "now": now})
        if rows:
            created += len(rows)
            continue
        _merge_into_admitted(db, clock, key=key, source_event_id=source_event_id,
                              incoming_refs=subject_refs)
    return created


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
    overwrites a non-empty one, whichever side arrived first. Returns (merged, keys_that_changed)
    so the caller can skip a no-op write and record exactly what a merge added."""
    merged = dict(existing)
    changed: list[str] = []
    for k, v in incoming.items():
        if k not in merged or (not merged[k] and v):
            if merged.get(k) != v:
                merged[k] = v
                changed.append(k)
    return merged, changed
