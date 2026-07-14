"""#519 · C1/A1 — the webhook ``event_id`` is the STABLE identity of a logical event (the receiver's
idempotency key), derived deterministically from what makes the event unique — NOT a fresh ``uuid4``
per emission. That per-emission nonce was the #330 4×-billing class: one logical ``meeting.completed``
emitted more than once looked like N distinct events to a deduping receiver.
"""
from __future__ import annotations

import hashlib

from meeting_api.lifecycle import LifecycleSink, MeetingStore, TransitionSource
from meeting_api.lifecycle.webhook import (
    build_status_change_envelope,
    build_typed_envelope,
    derive_event_id,
)


def _apply(goldens, *cases):
    """A fresh sink advanced through `cases` in order; returns the last StatusChange."""
    sink = LifecycleSink(store=MeetingStore())
    ch = None
    for case in cases:
        ch = sink.apply_change(goldens[case], transition_source=TransitionSource.BOT_CALLBACK)
    return ch


def test_same_logical_event_yields_identical_event_id(goldens):
    """The SAME logical event built twice (e.g. an initial POST and a retry-queue redelivery) presents
    an IDENTICAL event_id, so a deduping receiver processes it once. This is the whole fix."""
    e1 = build_status_change_envelope(_apply(goldens, "joining"))
    e2 = build_status_change_envelope(_apply(goldens, "joining"))
    assert e1["event_id"] == e2["event_id"]
    assert e1["event_id"].startswith("evt_") and len(e1["event_id"]) == 4 + 32  # evt_ + 32 hex


def test_event_id_matches_derivation_and_is_deterministic():
    """derive_event_id is a stable sha256 over (connection_id, event_type, new_status)."""
    a = derive_event_id("conn-1", "meeting.status_change", "active")
    assert a == derive_event_id("conn-1", "meeting.status_change", "active")  # deterministic
    assert a == "evt_" + hashlib.sha256(b"conn-1|meeting.status_change|active").hexdigest()[:32]
    # each component participates in the identity
    assert derive_event_id("conn-2", "meeting.status_change", "active") != a
    assert derive_event_id("conn-1", "meeting.started", "active") != a
    assert derive_event_id("conn-1", "meeting.status_change", "completed") != a


def test_distinct_event_types_of_one_advance_get_distinct_ids(goldens):
    """One advance to `active` emits BOTH meeting.status_change AND meeting.started — two DISTINCT
    logical events → distinct event_ids (event_type is part of the identity)."""
    ch = _apply(goldens, "joining", "active")
    sc = build_status_change_envelope(ch)
    typed = build_typed_envelope(ch)
    assert typed is not None and typed["event_type"] == "meeting.started"
    assert sc["event_id"] != typed["event_id"]


def test_production_builders_never_mint_a_random_id(goldens):
    """Negative control for a revert to uuid4: the same advance built N times is a SINGLE id. With the
    old `evt_{uuid4().hex}` default this set would have N distinct ids → red."""
    ids = {build_typed_envelope(_apply(goldens, "joining", "active"))["event_id"] for _ in range(4)}
    assert len(ids) == 1
