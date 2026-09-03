"""The timeline: scoping · merge · ordering (PRD decision 31).

Offline and stdlib-pure like the rest of the suite — a real sqlite schema, real rows written the
way the engine writes them, and no gateway. The meetings half is injected.
"""
from __future__ import annotations

import json

import pytest

from flows import SqliteDB
from flows_timeline import (build_timeline, concerns, event_from_meeting, events_from_reaction,
                            iso, merge, read_flows, resolve_identity, split_around, to_epoch)
from flows_timeline.model import Event, event_from_receipt

T0 = 1_788_000_000.0          # a fixed "now"; every offset below is relative to it
HOUR = 3600.0

REFS_INVITE = {"ics_uid": "platform-sync@zoom.us", "organizer": "admin@vexa.ai", "title": "Platform Sync",
               "participants": ["sam.richards@example.test", "tommy.snyder@example.test"],
               "start": T0 + 3 * HOUR}
REFS_DONE = {**REFS_INVITE, "uid": "126", "meeting_id": 104}


def _reaction(db, rid, event_type, refs, *, flow="invite_intake", step="await_start",
              status="done", created=T0, updated=None, reason=None):
    db.execute("""INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                        flow, flow_version, step, status, attempt, next_run_at,
                                        reason, created_at, updated_at)
                  VALUES (:rid,:sid,:et,:refs,:flow,1,:step,:st,0,0,:why,:c,:u)""",
               {"rid": rid, "sid": f"{rid}::{flow}", "et": event_type,
                "refs": json.dumps(refs), "flow": flow, "step": step, "st": status,
                "why": reason, "c": created, "u": updated if updated is not None else created})


def _receipt(db, rid, step, *, state="confirmed", result=None, at=T0, confirmed=None,
             provider_ref=None):
    db.execute("""INSERT INTO effect_receipt (effect_key, reaction_id, step, state, provider_ref,
                                              result, attempted_at, confirmed_at)
                  VALUES (:k,:r,:s,:st,:p,:res,:a,:c)""",
               {"k": f"{rid}:{step}", "r": rid, "s": step, "st": state, "p": provider_ref,
                "res": json.dumps(result) if result is not None else None,
                "a": at, "c": confirmed if confirmed is not None else (at if state == "confirmed" else None)})


def _lane():
    """The recorded day, exactly as the founder lane wrote it: invite → prepare mail → completed →
    minutes mail. Two reactions, four receipts that matter and three that are machinery."""
    db = SqliteDB()
    _reaction(db, "r-invite", "invite.received", REFS_INVITE, flow="invite_intake",
              created=T0, updated=T0 + 60)
    _receipt(db, "r-invite", "ensure_user", result={"uid": "126"}, at=T0 + 1)
    _receipt(db, "r-invite", "rsvp_accept", result={"message_id": "<rsvp@vexa.ai>"}, at=T0 + 2)
    _reaction(db, "r-prep", "meeting.upcoming", REFS_INVITE, flow="meeting_prep",
              step="prepare_meeting", created=T0 + 3, updated=T0 + 4)
    _receipt(db, "r-prep", "prepare_meeting", at=T0 + 4,
             result={"message_id": "<prep@vexa.ai>", "meeting_ref": "104"},
             provider_ref="<prep@vexa.ai>")
    _reaction(db, "r-done", "meeting.completed", REFS_DONE, flow="post_meeting",
              step="email_attendees", created=T0 + 2 * HOUR, updated=T0 + 2 * HOUR + 200)
    _receipt(db, "r-done", "require_workspace", result={"ready": True}, at=T0 + 2 * HOUR + 1)
    _receipt(db, "r-done", "email_minutes", at=T0 + 2 * HOUR + 160,
             result={"message_id": "<min@vexa.ai>", "link": "https://app.dev.vexa.ai/?s=tok"})
    return db


def _ident(subject):
    return ("126", "admin@vexa.ai")


# ── scoping ──────────────────────────────────────────────────────────────────────────────────────

def test_scoping_matches_organizer_attendee_and_uid():
    assert concerns({"organizer": "Admin@Vexa.ai"}, email="admin@vexa.ai")
    assert concerns({"participants": ["a@x.io", "sam.richards@example.test"]},
                    email="Sam.Richards@Example.test")
    assert concerns({"uid": "126"}, uid="126")
    assert concerns({"user_id": 126}, uid="126")   # an int column compares as its string
    assert not concerns({"organizer": "someone@else.io"}, uid="126", email="admin@vexa.ai")
    assert not concerns({"uid": "126"}, uid="", email="")   # no identity ⇒ nothing is yours


def test_scoping_needs_both_identifiers():
    """The invite lineage carries no uid and the completed lineage carries no address of ours —
    scoping on either alone drops half the day. This is the regression that rule exists for."""
    db = _lane()
    by_email = read_flows(db, email="admin@vexa.ai", since=T0 - HOUR, until=T0 + 4 * HOUR)
    by_uid = read_flows(db, uid="126", since=T0 - HOUR, until=T0 + 4 * HOUR)
    both = read_flows(db, uid="126", email="admin@vexa.ai", since=T0 - HOUR, until=T0 + 4 * HOUR)
    assert {e.kind for e in by_uid} == {"meeting.held", "report.delivered"}
    assert "invite.received" in {e.kind for e in by_email}
    assert len(both) > len(by_uid)


def test_a_stranger_sees_nothing():
    db = _lane()
    assert read_flows(db, uid="999", email="nobody@else.io",
                      since=T0 - HOUR, until=T0 + 4 * HOUR) == []


# ── mapping ──────────────────────────────────────────────────────────────────────────────────────

def test_machinery_steps_produce_no_events():
    """`ensure_user` and `require_workspace` happened; a person does not care. A timeline that
    lists them is a log."""
    db = _lane()
    kinds = [e.kind for e in read_flows(db, uid="126", email="admin@vexa.ai",
                                        since=T0 - HOUR, until=T0 + 4 * HOUR)]
    assert "ensure_user" not in kinds and "require_workspace" not in kinds


def test_a_failed_receipt_is_an_event_whatever_its_step():
    refs = {"uid": "126", "title": "Platform Sync"}
    ev = event_from_receipt({"step": "ensure_user", "state": "failed", "attempted_at": T0,
                             "result": json.dumps({})}, refs)
    assert ev is not None and ev.kind == "reaction.failed" and ev.status == "failed"


def test_a_failed_reaction_keeps_the_fact_that_arrived():
    evs = events_from_reaction({"event_type": "invite.received", "subject_refs": json.dumps(REFS_INVITE),
                                "flow": "invite_intake", "status": "failed", "reason": "smtp said no",
                                "created_at": T0, "updated_at": T0 + 900})
    assert [e.kind for e in evs] == ["invite.received", "reaction.failed"]
    assert [e.at for e in evs] == [T0, T0 + 900]
    assert evs[1].detail == "smtp said no"


def test_a_skipped_send_says_skipped_not_done():
    ev = event_from_receipt({"step": "email_attendees", "state": "confirmed", "attempted_at": T0,
                             "result": json.dumps({"sent": 0, "skipped": "no inside-domain attendee"})},
                            REFS_DONE)
    assert ev.status == "skipped" and ev.detail == "no inside-domain attendee"


def test_produced_carries_the_link_the_note_and_the_message():
    ev = event_from_receipt({"step": "drop_to_attendees", "state": "confirmed", "attempted_at": T0,
                             "provider_ref": "<m@vexa.ai>",
                             "result": json.dumps({"entity": "kg/entities/meeting/platform-sync.md",
                                                   "link": "https://app/x", "dropped": 2})},
                            REFS_DONE)
    assert ev.produced == {"link": "https://app/x", "note_path": "kg/entities/meeting/platform-sync.md",
                           "message_id": "<m@vexa.ai>"}


def test_a_meeting_row_is_scheduled_until_it_is_terminal():
    upcoming = event_from_meeting({"id": 200, "status": "scheduled", "data": {"title": "Standup",
                                   "scheduled_at": "2026-09-03T09:00:00Z"}})
    held = event_from_meeting({"id": 104, "status": "completed", "data": {"title": "Platform Sync"},
                               "start_time": "2026-09-02T14:23:00", "end_time": "2026-09-02T15:36:10"})
    assert (upcoming.kind, upcoming.title) == ("meeting.scheduled", "Standup")
    assert held.kind == "meeting.held" and held.at == to_epoch("2026-09-02T15:36:10Z")


def test_a_naive_timestamp_is_read_as_utc():
    """meeting-api stores naive UTC. Reading it as local would move a meeting by hours on any host
    whose clock is not the deployment's, and nothing would say so."""
    assert to_epoch("2026-09-02T14:23:00") == to_epoch("2026-09-02T14:23:00Z")


# ── merge + ordering ─────────────────────────────────────────────────────────────────────────────

def test_ordering_is_ascending_and_the_limit_keeps_the_most_recent():
    evs = [Event(at=T0 + i, kind="mail.sent", title=f"m{i}", status="done") for i in range(10)]
    got = merge(evs, limit=3)
    assert [e.title for e in got] == ["m7", "m8", "m9"]


def test_the_window_excludes_what_is_outside_it():
    evs = [Event(at=T0 - HOUR, kind="mail.sent", title="old", status="done"),
           Event(at=T0 + HOUR, kind="mail.sent", title="in", status="done"),
           Event(at=T0 + 10 * HOUR, kind="mail.sent", title="far", status="done")]
    assert [e.title for e in merge(evs, since=T0, until=T0 + 2 * HOUR)] == ["in"]


def test_one_meeting_seen_twice_is_one_row_and_the_earlier_sighting_wins():
    """The fact said the meeting finished at 13:52; the meetings table says its row ended at 15:36.
    Both are true and the person had one meeting — keep the moment the system LEARNED it."""
    evs = [Event(at=T0 + 100, kind="meeting.held", title="Platform Sync", status="done", meeting_id="104",
                 source="reaction"),
           Event(at=T0 + 6000, kind="meeting.held", title="Platform Sync", status="completed",
                 meeting_id="104", source="meeting")]
    got = merge(evs)
    assert len(got) == 1 and got[0].source == "reaction"


def test_scheduled_and_held_for_one_meeting_both_survive():
    evs = [Event(at=T0, kind="meeting.scheduled", title="Platform Sync", status="scheduled", meeting_id="104"),
           Event(at=T0 + 100, kind="meeting.held", title="Platform Sync", status="done", meeting_id="104")]
    assert [e.kind for e in merge(evs)] == ["meeting.scheduled", "meeting.held"]


def test_split_around_gives_the_last_few_and_the_next_few():
    evs = [Event(at=T0 - i * HOUR, kind="mail.sent", title=f"p{i}", status="done") for i in range(8, 0, -1)]
    evs += [Event(at=T0 + i * HOUR, kind="meeting.scheduled", title=f"n{i}", status="scheduled")
            for i in range(1, 8)]
    past, future = split_around(merge(evs, limit=100), T0, back=5, ahead=5)
    assert [e.title for e in past] == ["p5", "p4", "p3", "p2", "p1"]
    assert [e.title for e in future] == ["n1", "n2", "n3", "n4", "n5"]


# ── the whole route answer ───────────────────────────────────────────────────────────────────────

def test_the_recorded_day_comes_back_in_order():
    db = _lane()
    out = build_timeline(db, "126", since=T0 - HOUR, until=T0 + 4 * HOUR, limit=50,
                         now=T0 + 3 * HOUR, meetings=None, identity=_ident)
    kinds = [e["kind"] for e in out["events"]]
    assert kinds[:2] == ["invite.received", "invite.accepted"]
    assert kinds.index("meeting.scheduled") < kinds.index("meeting.held")
    assert kinds.index("meeting.held") < kinds.index("report.delivered")
    ats = [e["at_epoch"] for e in out["events"]]
    assert ats == sorted(ats)
    assert out["now"] == iso(T0 + 3 * HOUR)


def test_the_meetings_half_merges_in():
    db = _lane()
    rows = [{"id": 300, "status": "scheduled", "data": {"title": "Next standup",
                                                        "scheduled_at": T0 + 3 * HOUR}}]
    out = build_timeline(db, "126", since=T0 - HOUR, until=T0 + 4 * HOUR, limit=50,
                         now=T0 + 2.5 * HOUR, meetings=lambda uid: rows, identity=_ident)
    scheduled = [e for e in out["events"] if e["kind"] == "meeting.scheduled"]
    assert "Next standup" in [e["title"] for e in scheduled]
    assert out["events"][-1]["title"] == "Next standup"      # the future sorts last


def test_an_unresolvable_subject_is_said_so_not_answered_with_everything():
    db = _lane()
    out = build_timeline(db, "", meetings=None, identity=lambda s: ("", ""))
    assert out["unresolved"] is True and out["events"] == []


def test_identity_resolution_asks_admin_api_for_the_half_it_lacks():
    seen = []

    def lookup(path):
        seen.append(path)
        return {"email": "admin@vexa.ai"} if path.endswith("/126") else {"id": 126}

    assert resolve_identity("126", lookup) == ("126", "admin@vexa.ai")
    assert resolve_identity("Admin@Vexa.ai", lookup) == ("126", "admin@vexa.ai")
    assert seen == ["/admin/users/126", "/admin/users/email/admin@vexa.ai"]


def test_identity_failure_degrades_to_the_half_we_were_given():
    def lookup(path):
        raise RuntimeError("identity is down")

    assert resolve_identity("126", lookup) == ("126", "")


@pytest.mark.parametrize("subject", ["126", "admin@vexa.ai"])
def test_either_identifier_reaches_the_same_day(subject):
    db = _lane()
    out = build_timeline(db, subject, since=T0 - HOUR, until=T0 + 4 * HOUR, limit=50,
                         now=T0 + 3 * HOUR, meetings=None, identity=_ident)
    assert [e["kind"] for e in out["events"]].count("invite.received") == 1
