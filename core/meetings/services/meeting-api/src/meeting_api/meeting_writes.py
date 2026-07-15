"""Shared meeting-write barrier identity and durable capture-state predicate."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime


MEETING_WRITE_LOCK_NAMESPACE = 23115


def capture_is_withdrawn(data: object) -> bool:
    """True only for an explicitly withdrawn ZAKI capture; ordinary Vexa meetings stay writable."""
    capture = data.get("zaki_capture") if isinstance(data, Mapping) else None
    return isinstance(capture, Mapping) and capture.get("state") == "withdrawn"


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
