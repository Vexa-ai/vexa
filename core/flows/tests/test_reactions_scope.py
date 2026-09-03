"""GATE 4c — `GET /reactions?subject=`: the operator projection, scoped to one person
(R-D07 · R-D12), on the service side of the same two rows.

Offline like the rest of the suite: a real sqlite schema and rows written the way the engine writes
them. What is under test is the SCOPING, which is the part that can be wrong; the route above it is
a thin forward.
"""
from __future__ import annotations

import json

from sqlite_double import SqliteDB
from flows_timeline import (REACTION_FOUND, REACTION_MISSING, REACTION_NOT_YOURS,
                            list_reactions, reaction_concerns)

T0 = 1_788_000_000.0

MINE_BY_UID = {"uid": "126", "meeting_id": 104, "title": "ours"}
MINE_BY_EMAIL = {"organizer": "dima@vexa.ai", "ics_uid": "a@b", "title": "ours, from the invite"}
THEIRS = {"uid": "999", "organizer": "someone@else.test", "meeting_id": 7,
          "title": "another tenant's meeting"}


def _reaction(db, rid, refs, *, status="failed", reason=None, created=T0):
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        reason, created_at, updated_at)
                  VALUES (:rid,:sid,'meeting.completed',:refs,'post_meeting',1,'email_minutes',
                          :st,0,0,:why,:c,:c)""",
               {"rid": rid, "sid": f"{rid}::post_meeting", "refs": json.dumps(refs),
                "st": status, "why": reason, "c": created})


def _db():
    db = SqliteDB()
    _reaction(db, "r-mine-uid", MINE_BY_UID)
    _reaction(db, "r-mine-email", MINE_BY_EMAIL)
    _reaction(db, "r-theirs", THEIRS, reason="smtp 535 on their mailbox")
    return db


def _identity(_subject):
    return ("126", "dima@vexa.ai")


def test_a_subject_sees_only_their_own_reactions():
    """The defect, exactly: an unscoped read returned all three rows, and the control MCP reported
    the third — another tenant's flow, step and failure reason — as this person's queue."""
    everything = list_reactions(_db())
    assert {r["reaction_id"] for r in everything} == {"r-mine-uid", "r-mine-email", "r-theirs"}

    mine = list_reactions(_db(), subject="126", identity=_identity)
    assert {r["reaction_id"] for r in mine} == {"r-mine-uid", "r-mine-email"}
    assert all("smtp" not in str(r["reason"] or "") for r in mine)


def test_scoping_uses_both_identifiers():
    """Half a scope is the failure mode `model.concerns` exists to prevent: the invite lineage
    carries an organizer address and no uid, the completed lineage a uid and no address."""
    uid_only = list_reactions(_db(), subject="126", identity=lambda s: ("126", ""))
    email_only = list_reactions(_db(), subject="x", identity=lambda s: ("", "dima@vexa.ai"))

    assert {r["reaction_id"] for r in uid_only} == {"r-mine-uid"}
    assert {r["reaction_id"] for r in email_only} == {"r-mine-email"}


def test_status_still_filters_under_a_subject():
    _db_ = _db()
    _reaction(_db_, "r-mine-done", MINE_BY_UID, status="done")
    got = list_reactions(_db_, subject="126", status="done", identity=_identity)
    assert {r["reaction_id"] for r in got} == {"r-mine-done"}


def test_an_unresolvable_subject_is_none_not_everything():
    """FAIL CLOSED. A subject nobody answers to must not fall back to the unscoped read — that is
    how a scoping bug turns into the leak it was added to close."""
    assert list_reactions(_db(), subject="nobody@nowhere.test",
                          identity=lambda s: ("", "")) is None


# ── the signal verbs' ownership check ────────────────────────────────────────────────────────────
#
# The read half above stops a stranger's reaction id being HANDED OUT. This half stops one being
# ACTED ON, which is the destructive one: `cancel` on somebody else's scheduled join destroys work
# they are waiting for.

def test_a_reaction_you_own_is_yours_to_steer():
    """The ordinary path, and the reason this is ownership rather than operator authority:
    `bot_schedule` mints the reaction, and the same person has to be able to cancel it."""
    assert reaction_concerns(_db(), "r-mine-uid", subject="126",
                             identity=_identity) == REACTION_FOUND
    assert reaction_concerns(_db(), "r-mine-email", subject="126",
                             identity=_identity) == REACTION_FOUND


def test_a_colleagues_reaction_is_refused():
    assert reaction_concerns(_db(), "r-theirs", subject="126",
                             identity=_identity) == REACTION_NOT_YOURS


def test_a_reaction_that_does_not_exist_says_so():
    """Three outcomes, not two. "not yours" for an id nobody has sends the caller off to
    re-derive it; "no such reaction" tells them to ask for a real one."""
    assert reaction_concerns(_db(), "r-nope", subject="126",
                             identity=_identity) == REACTION_MISSING


def test_an_unresolvable_subject_owns_nothing():
    """FAIL CLOSED, the same direction as the read half."""
    assert reaction_concerns(_db(), "r-mine-uid", subject="ghost@nowhere.test",
                             identity=lambda s: ("", "")) == REACTION_NOT_YOURS


def test_ownership_is_a_direct_lookup_not_a_windowed_scan():
    """An OLD reaction — outside any projection window or `LIMIT 100` — is still yours.

    Reusing `list_reactions` for this check would have been the shorter edit and would have
    started refusing exactly the reactions a person is most likely to be cancelling: the ones that
    have been sitting scheduled for a while."""
    db = _db()
    for i in range(150):
        _reaction(db, f"r-filler-{i}", THEIRS, created=T0 + 1 + i)
    _reaction(db, "r-old-mine", MINE_BY_UID, created=T0 - 90 * 86400)

    assert reaction_concerns(db, "r-old-mine", subject="126", identity=_identity) == REACTION_FOUND
    listed = list_reactions(db, subject="126", identity=_identity)
    assert "r-old-mine" in {r["reaction_id"] for r in listed}, \
        "the projection can lose it and the ownership check still must not"
