"""The booking bounds on a planned row (ADR-0052 §8) — one module, read by both repos' one creation path.

Every booked row — ``POST /meetings``, calendar sync, and (from s2d) an invite — is created by the
repo's ``create_planned_meeting``. In the same transaction, under the per-user lock, it:

* refuses a ``scheduled_at`` that is not ISO-8601 (before this, any string was stored and the bot
  simply never came);
* counts the account's FUTURE ``scheduled`` rows with ``auto_join`` on against
  ``BOOKED_MAX_PER_ACCOUNT`` (500, no horizon; founder, 1 Nov 2026). Over it, the API is refused
  (``429`` with the cap), and calendar sync still mirrors the event but creates the row with no bot
  (``auto_join`` off) and ``auto_join_error`` saying why, so the owner sees it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

DEFAULT_BOOKED_MAX_PER_ACCOUNT = 500
CAP_REACHED = "booking cap reached"


def booked_max_per_account() -> int:
    """``BOOKED_MAX_PER_ACCOUNT``, default 500; an unparseable or negative value is the default."""
    raw = os.getenv("BOOKED_MAX_PER_ACCOUNT")
    if raw is None or not raw.strip():
        return DEFAULT_BOOKED_MAX_PER_ACCOUNT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BOOKED_MAX_PER_ACCOUNT
    return value if value >= 0 else DEFAULT_BOOKED_MAX_PER_ACCOUNT


def parse_scheduled_at(value: Any) -> Optional[datetime]:
    """An ISO-8601 time (``Z`` or an offset; a naive time is read as UTC), or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def future_bookings(datas: Iterable[Any], now: Optional[datetime] = None) -> int:
    """How many of these ``scheduled`` rows' ``data`` are a future booking with a bot."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for data in datas:
        if not isinstance(data, dict) or data.get("auto_join") is False:
            continue
        at = parse_scheduled_at(data.get("scheduled_at"))
        if at is not None and at > now:
            n += 1
    return n


def cap_message(cap: int) -> str:
    return (f"{CAP_REACHED}: this account already has {cap} future meetings booked with a bot "
            f"(BOOKED_MAX_PER_ACCOUNT)")
