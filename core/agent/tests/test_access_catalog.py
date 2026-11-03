"""Access-gate deny vectors for model routes on warm units (Vexa-ai/vexa#1783).

Pure unit tests, no network. A worker's model route is fixed when it is spawned for one person, so a
meeting's warm unit belongs to the person it was dispatched for: a second person's turn on the same
meeting session addresses a unit of its own and never touches the first person's worker.
"""
from __future__ import annotations

from shared import units


def _meeting(subject: str, session_uid: str = "abc") -> dict:
    return units.make_dispatch(
        subject=subject, trigger="transcription", start=units.entrypoint(inline="brief"),
        context={"kind": "meeting", "meeting": {"meeting_id": session_uid, "session_uid": session_uid}},
    )


def test_two_people_on_one_meeting_session_get_two_units():
    assert units.dispatch_id(_meeting("126")) != units.dispatch_id(_meeting("127"))


def test_one_person_keeps_one_warm_unit_per_meeting_session():
    assert units.dispatch_id(_meeting("126")) == units.dispatch_id(_meeting("126"))
    assert units.dispatch_id(_meeting("126", "abc")) != units.dispatch_id(_meeting("126", "xyz"))


def test_the_meeting_unit_is_still_a_meeting_worker():
    """The runtime groups workers by the `agent-meet-` head; the subject rides after the session."""
    uid = units.dispatch_id(_meeting("126"))
    assert uid.startswith("agent-meet-abc")
    assert "126" in uid
