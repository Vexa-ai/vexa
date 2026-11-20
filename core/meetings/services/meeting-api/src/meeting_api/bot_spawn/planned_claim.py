"""Pure helpers for claiming a calendar-planned meeting row for a bot run."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def claimed_plan_data(
    planned: dict,
    spawn_data: dict,
    *,
    claimed_at: str,
    preserve_schedule_until: str,
) -> dict:
    """Merge spawn data over a plan without consuming an unservable future occurrence.

    A manual spawn may find a calendar row for the same recurring meeting link whose occurrence is
    still far in the future. Claiming that row while retaining its future ``scheduled_at`` makes the
    completed manual run look like it served the future occurrence, so calendar sync suppresses the
    real auto-join later. Preserve the plan's schedule only while the bot's admission-wait budget can
    reach it; otherwise attribute this claimed run to the claim time.
    """
    result = {**planned, **spawn_data}
    scheduled_at = _parse_utc(planned.get("scheduled_at"))
    preserve_until = _parse_utc(preserve_schedule_until)
    if scheduled_at is not None and preserve_until is not None and scheduled_at > preserve_until:
        result["scheduled_at"] = claimed_at
    return result
