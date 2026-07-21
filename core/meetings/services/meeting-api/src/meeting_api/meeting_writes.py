"""Shared meeting-write barrier identity and durable capture-state predicate."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime


MEETING_WRITE_LOCK_NAMESPACE = 23115


def capture_is_withdrawn(data: object) -> bool:
    """True only for an explicitly withdrawn ZAKI capture; ordinary Vexa meetings stay writable."""
    capture = data.get("zaki_capture") if isinstance(data, Mapping) else None
    return isinstance(capture, Mapping) and capture.get("state") == "withdrawn"


def transcript_writes_refused(data: object) -> bool:
    """Should the transcript write barrier refuse this meeting?

    STOPPING a capture is not a privacy event: the stop tombstones the AUTHORITY
    (state=withdrawn — no future capture may ride the old grant; that check stays
    on ``capture_is_withdrawn``/``capture_authority_is_stale``) but the segments
    ALREADY captured under valid consent must still flush to the archive. Before
    this split, every ordinarily-stopped meeting hit the privacy barrier at the
    delayed db-writer flush and its ENTIRE buffered transcript was purged —
    "the bot was there, I spoke, nothing surfaced".

    Only a tombstone carrying privacy intent refuses (and purges). Fail closed:
    an unknown or missing reason on a withdrawn capture refuses — exactly the
    pre-split behavior — so only the explicit ``capture_stopped`` reason opts out.
    """
    capture = data.get("zaki_capture") if isinstance(data, Mapping) else None
    if not (isinstance(capture, Mapping) and capture.get("state") == "withdrawn"):
        return False
    return capture.get("withdrawal_reason") != "capture_stopped"


def capture_authority_is_stale(incoming: object, prior: object) -> bool:
    """Reject capture authority that does not post-date a durable scope withdrawal.

    A malformed withdrawal tombstone fails closed: once the durable state says ``withdrawn``, only
    parseable evidence of a strictly newer authorization may reopen the same capture scope.
    """
    if not capture_is_withdrawn(prior):
        return False
    incoming_capture = incoming.get("zaki_capture") if isinstance(incoming, Mapping) else None
    prior_capture = prior.get("zaki_capture") if isinstance(prior, Mapping) else None
    if not isinstance(incoming_capture, Mapping) or not isinstance(prior_capture, Mapping):
        return True
    try:
        authorized_at = datetime.fromisoformat(incoming_capture["authorized_at"])
        withdrawn_at = datetime.fromisoformat(prior_capture["withdrawn_at"])
    except (KeyError, TypeError, ValueError):
        return True
    if (
        authorized_at.tzinfo is None
        or authorized_at.utcoffset() is None
        or withdrawn_at.tzinfo is None
        or withdrawn_at.utcoffset() is None
    ):
        return True
    return authorized_at <= withdrawn_at
