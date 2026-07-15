"""Shared meeting-write barrier identity and durable capture-state predicate."""
from __future__ import annotations


MEETING_WRITE_LOCK_NAMESPACE = 23115


def capture_is_withdrawn(data: object) -> bool:
    """True only for an explicitly withdrawn ZAKI capture; ordinary Vexa meetings stay writable."""
    capture = data.get("zaki_capture") if isinstance(data, dict) else None
    return isinstance(capture, dict) and capture.get("state") == "withdrawn"
