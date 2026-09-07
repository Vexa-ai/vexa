"""#1502 dedup collision: meeting-api now publishes `meeting.started`/`meeting.completed` into
flows with the SAME `source_event_id` scheme `invite_intake`'s own `emit_started`/`emit_completed`
steps use (`live-<id>` / `done-<id>`) — see `flows_defs/production.py`'s `emit_completed`
(`f"done-{d['meeting_id']}"`). The two producers therefore dedup to ONE reaction per `post_meeting`
admission, and for a calendar-invited meeting meeting-api's bare
`{uid, meeting_id, platform, native_meeting_id, completion_reason}` usually wins the race — the
loser (invite_intake's own `emit_completed`, carrying `participants`/`participant_names`) was
simply discarded by `ON CONFLICT (source_event_id) DO NOTHING`, so `process_meeting`'s room-read
(`flows_defs/production.py`'s `process_meeting`, reading `ctx.refs.get("participants")` /
`ctx.refs.get("participant_names")` via `mt.room_order`) degraded to empty instead of the invite's
attendee order.

RED before the fix in `flows/admission.py`: `admit()` only ever inserted-or-discarded on conflict,
so whichever admission lost the race left NOTHING of its refs on the reaction. GREEN after: a
second admission for an already-admitted `(source_event_id, flow)` key MERGES its `subject_refs`
into the existing reaction's — new keys added, an existing key kept unless it is empty and the
incoming one is not (never let an empty value overwrite a non-empty one, in either direction) —
and records the merge under `scratch.ref_merges` so it is visible on the row without diffing two
admissions by hand.

Uses `fixtures.rig()` directly (not the full drain loop): admission is a pure DB-writing function,
independent of which steps the flow runs, so these tests never execute `post_meeting`'s steps at
all — they only exercise `admit()` against the `reaction` table `rig()` already wires up with a
`post_meeting` flow reacting to `MEETING_COMPLETED` (`flows_steps.fakes`), the same flow name and
event type production uses for this exact collision."""
from __future__ import annotations

import json

from fixtures import rig
from flows import admit
from flows_steps.fakes import MEETING_COMPLETED

MEETING_API_REFS = {"uid": "u-1", "meeting_id": "m-42", "platform": "google_meet",
                     "native_meeting_id": "n-42", "completion_reason": "normal"}
INVITE_REFS = {"uid": "u-1", "meeting_id": "m-42", "invite": "ics-1",
               "attendees": ["anna@bank.com", "ben@bank.com"]}


def _reaction_row(db):
    """The one `post_meeting` reaction admitted for `done-42` — asserts there is exactly one,
    which is the dedup invariant this whole bug is about."""
    rows = db.execute(
        "SELECT reaction_id, subject_refs, scratch FROM reaction WHERE flow = 'post_meeting'")
    assert len(rows) == 1, f"expected exactly one post_meeting reaction, got {len(rows)}"
    reaction_id, refs_json, scratch_json = rows[0]
    return reaction_id, json.loads(refs_json), json.loads(scratch_json) if scratch_json else {}


def test_meeting_api_wins_the_race_then_invite_intake_merges_in_attendees():
    """meeting-api's bare completion admits first (wins the race, as it usually does per the
    defect); invite_intake's own `emit_completed` — carrying the invite's attendees — admits
    second under the SAME `done-42` id. The reaction must end up knowing everything either
    admission knew: a union, not whichever one happened to get there first."""
    db, reg, clock, _world = rig()

    created_first = admit(db, reg, clock, source_event_id="done-42",
                           event_type=MEETING_COMPLETED.name, subject_refs=MEETING_API_REFS)
    created_second = admit(db, reg, clock, source_event_id="done-42",
                            event_type=MEETING_COMPLETED.name, subject_refs=INVITE_REFS)

    assert created_first == 1     # the reaction is admitted on the winner
    assert created_second == 0    # the loser is a dedup, not a second reaction

    _reaction_id, refs, scratch = _reaction_row(db)
    assert refs == {**MEETING_API_REFS, **INVITE_REFS}   # disjoint keys except uid/meeting_id (equal)
    assert scratch.get("ref_merges"), "the merge must be visible on the reaction's scratch"
    assert scratch["ref_merges"][0]["from_source_event_id"] == "done-42"
    assert set(scratch["ref_merges"][0]["added_keys"]) == {"invite", "attendees"}


def test_invite_intake_wins_the_race_then_meeting_api_merge_is_a_no_op_gain():
    """Same collision, reverse order: invite_intake's `emit_completed` (with attendees) wins the
    race, meeting-api's bare completion arrives second. The union must be identical regardless of
    which producer happened to admit first — the fix must not be order-dependent."""
    db, reg, clock, _world = rig()

    admit(db, reg, clock, source_event_id="done-42",
          event_type=MEETING_COMPLETED.name, subject_refs=INVITE_REFS)
    admit(db, reg, clock, source_event_id="done-42",
          event_type=MEETING_COMPLETED.name, subject_refs=MEETING_API_REFS)

    _reaction_id, refs, _scratch = _reaction_row(db)
    assert refs == {**MEETING_API_REFS, **INVITE_REFS}


def test_duplicate_delivery_with_identical_refs_is_a_true_no_op():
    """Transport-level redelivery of the SAME producer's fact (identical refs, same source_event_id)
    must stay a no-op the way it always has — no second reaction, no refs change, and no merge
    noise in scratch (nothing was actually added)."""
    db, reg, clock, _world = rig()

    admit(db, reg, clock, source_event_id="done-42",
          event_type=MEETING_COMPLETED.name, subject_refs=MEETING_API_REFS)
    created = admit(db, reg, clock, source_event_id="done-42",
                     event_type=MEETING_COMPLETED.name, subject_refs=dict(MEETING_API_REFS))

    assert created == 0
    _reaction_id, refs, scratch = _reaction_row(db)
    assert refs == MEETING_API_REFS
    assert not scratch.get("ref_merges"), "an identical redelivery must not write a merge record"


def test_an_empty_value_never_overwrites_a_non_empty_one_either_direction():
    """A key present on both sides where one side's value is empty must never clobber the other
    side's real value — regardless of which admission carries the empty one, and regardless of
    admission order. This is the safety rail on the merge itself, independent of the specific
    meeting-api/invite_intake shapes above."""
    db, reg, clock, _world = rig()

    empty_first = {"uid": "u-1", "meeting_id": "m-9", "attendees": []}
    real_second = {"uid": "u-1", "meeting_id": "m-9", "attendees": ["anna@bank.com"]}
    admit(db, reg, clock, source_event_id="done-9",
          event_type=MEETING_COMPLETED.name, subject_refs=empty_first)
    admit(db, reg, clock, source_event_id="done-9",
          event_type=MEETING_COMPLETED.name, subject_refs=real_second)
    _reaction_id, refs, _scratch = _reaction_row(db)
    assert refs["attendees"] == ["anna@bank.com"]     # the real value filled the empty slot

    db2, reg2, clock2, _world2 = rig()
    real_first = {"uid": "u-1", "meeting_id": "m-9", "attendees": ["ben@bank.com"]}
    empty_second = {"uid": "u-1", "meeting_id": "m-9", "attendees": []}
    admit(db2, reg2, clock2, source_event_id="done-9",
          event_type=MEETING_COMPLETED.name, subject_refs=real_first)
    admit(db2, reg2, clock2, source_event_id="done-9",
          event_type=MEETING_COMPLETED.name, subject_refs=empty_second)
    _reaction_id2, refs2, _scratch2 = _reaction_row(db2)
    assert refs2["attendees"] == ["ben@bank.com"]      # the later empty value never wins
