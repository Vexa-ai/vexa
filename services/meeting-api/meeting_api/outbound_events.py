"""Internal outbound-event ledger stored in meeting.data.

The ledger is the no-migration outbox used by v0.10.6.1 for internal
post-meeting hooks. It records one event per meeting + event type +
destination and lets callers distinguish:

* delivered: HTTP delivery completed;
* queued: delivery failed but Redis retry accepted the event;
* pending: event was claimed before HTTP delivery and needs sweep recovery;
* failed: neither direct delivery nor durable queue currently owns it.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .models import Meeting
from .schemas import MeetingStatus

OUTBOUND_EVENTS_KEY = "outbound_events"
DEFAULT_PENDING_MAX_AGE_SECONDS = 300


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def destination_hash(destination: str) -> str:
    return hashlib.sha256(destination.encode("utf-8")).hexdigest()[:16]


def event_key(channel: str, event_type: str, meeting_id: int, destination: str) -> str:
    return f"{channel}:{event_type}:{meeting_id}:{destination_hash(destination)}"


def _copy_data(meeting: Meeting) -> dict[str, Any]:
    return dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}


def _copy_events(data: dict[str, Any]) -> dict[str, Any]:
    events = data.get(OUTBOUND_EVENTS_KEY)
    return dict(events) if isinstance(events, dict) else {}


def _flag_data_modified(meeting: Meeting) -> None:
    try:
        flag_modified(meeting, "data")
    except Exception:
        # Unit tests often use MagicMock-shaped Meeting objects. Real ORM
        # instances still need flag_modified for JSONB persistence.
        pass


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def claim_outbound_event(
    db: AsyncSession,
    *,
    meeting_id: int,
    channel: str,
    event_type: str,
    destination: str,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any], bool]:
    """Claim an outbound event under a row lock.

    Returns ``(key, event, should_deliver)``. Delivered, queued, and pending
    events are not delivered again. Failed events can be reclaimed because no
    durable owner currently has the work.
    """
    key = event_key(channel, event_type, meeting_id, destination)
    now = utc_now_iso()

    meeting = (
        await db.execute(
            select(Meeting)
            .where(Meeting.id == meeting_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if meeting is None:
        await db.rollback()
        return key, {"status": "failed", "error": "meeting not found"}, False

    data = _copy_data(meeting)
    events = _copy_events(data)
    existing = events.get(key)
    if isinstance(existing, dict) and existing.get("status") in {"delivered", "queued", "pending"}:
        await db.rollback()
        return key, dict(existing), False

    event = dict(existing) if isinstance(existing, dict) else {}
    event.update({
        "key": key,
        "channel": channel,
        "event_type": event_type,
        "destination": destination,
        "destination_hash": destination_hash(destination),
        "payload": payload,
        "status": "pending",
        "first_claimed_at": event.get("first_claimed_at") or event.get("claimed_at") or now,
        "claimed_at": now,
        "updated_at": now,
        "attempts": int(event.get("attempts") or 0),
    })
    events[key] = event
    data[OUTBOUND_EVENTS_KEY] = events
    meeting.data = data
    _flag_data_modified(meeting)
    await db.commit()
    return key, event, True


async def mark_outbound_event(
    db: AsyncSession,
    *,
    meeting_id: int,
    key: str,
    status: str,
    attempts: int | None = None,
    error: str | None = None,
    status_code: int | None = None,
) -> None:
    """Update one ledger event under row lock."""
    now = utc_now_iso()
    meeting = (
        await db.execute(
            select(Meeting)
            .where(Meeting.id == meeting_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if meeting is None:
        await db.rollback()
        return

    data = _copy_data(meeting)
    events = _copy_events(data)
    event = dict(events.get(key) or {"key": key})
    event["status"] = status
    event["updated_at"] = now
    if attempts is not None:
        event["attempts"] = attempts
    if error:
        event["error"] = error[:500]
    elif "error" in event and status in {"delivered", "queued", "pending"}:
        event.pop("error", None)
    if status_code is not None:
        event["status_code"] = status_code
    if status == "delivered":
        event["delivered_at"] = now
    elif status == "queued":
        event["queued_at"] = now
    elif status == "failed":
        event["failed_at"] = now

    events[key] = event
    data[OUTBOUND_EVENTS_KEY] = events
    meeting.data = data
    _flag_data_modified(meeting)
    await db.commit()


def is_stale_pending_event(
    event: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_PENDING_MAX_AGE_SECONDS,
) -> bool:
    if event.get("status") != "pending":
        return False
    claimed_at = _parse_iso(event.get("claimed_at") or event.get("updated_at"))
    if claimed_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now - claimed_at > timedelta(seconds=max_age_seconds)


async def find_stale_pending_events(
    db: AsyncSession,
    *,
    max_age_seconds: int = DEFAULT_PENDING_MAX_AGE_SECONDS,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Find stale pending ledger events on terminal meetings.

    This bounded scan is intentionally simple for the hotfix. It recovers the
    narrow crash window after claim commit and before HTTP/Redis ownership.
    """
    rows = (
        await db.execute(
            select(Meeting)
            .where(Meeting.status.in_([MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]))
            .order_by(Meeting.id.desc())
            .limit(limit)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    stale: list[dict[str, Any]] = []
    for meeting in rows:
        data = _copy_data(meeting)
        events = _copy_events(data)
        for key, event in events.items():
            if not isinstance(event, dict):
                continue
            if is_stale_pending_event(event, now=now, max_age_seconds=max_age_seconds):
                stale.append({
                    "meeting_id": meeting.id,
                    "key": key,
                    "event": dict(event),
                })
    return stale
