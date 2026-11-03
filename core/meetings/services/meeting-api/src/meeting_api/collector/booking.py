"""The booking bounds on a planned row (ADR-0052 §8) — one module, read by both repos' writers.

Every booked row — ``POST /meetings``, calendar sync, and (from s2d) an invite — is created by the
repo's ``create_planned_meeting``; a row can also BECOME a booking later — ``PATCH /meetings``
giving an idle plan a time or turning ``auto_join`` back on, ``PUT …/intent``, calendar sync
re-deriving ``auto_join`` or moving a time (``update_planned_meeting`` / ``set_intent``). Every one
of those writers, in the same transaction, under the per-user lock:

* refuses a ``scheduled_at`` that is not ISO-8601 (before this, any string was stored and the bot
  simply never came);
* counts the account's FUTURE ``scheduled`` rows with ``auto_join`` on against
  ``BOOKED_MAX_PER_ACCOUNT`` (500, no horizon; founder, 1 Nov 2026). Over it, the API is refused
  (``429`` with the cap), and calendar sync still mirrors the event but creates the row with no bot
  (``auto_join`` off) and ``auto_join_error`` saying why, so the owner sees it.

A write that leaves the row's own count where it was (moving an already-armed row, editing its
title) is never refused, so an account over the cap from before it existed keeps what it has.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

BOOKED_MAX_ENV = "BOOKED_MAX_PER_ACCOUNT"
DEFAULT_BOOKED_MAX_PER_ACCOUNT = 500
CAP_REACHED = "booking cap reached"


def booked_max_per_account(env: Optional[Mapping[str, str]] = None) -> int:
    """``BOOKED_MAX_PER_ACCOUNT``, 500 when unset or empty. Anything but a whole number of 0 or more
    raises ``ValueError`` naming it, and the boot refuses it (``_booking_cap_at_boot``): a typo used
    to become 500 with no word, a cap nobody chose."""
    raw = (os.environ if env is None else env).get(BOOKED_MAX_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_BOOKED_MAX_PER_ACCOUNT
    if not re.fullmatch(r"[0-9]+", raw.strip()):
        raise ValueError(f"{BOOKED_MAX_ENV} must be a whole number of 0 or more (got {raw!r})")
    return int(raw.strip())


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


def is_future_booking(status: Any, data: Any, now: Optional[datetime] = None) -> bool:
    """Does this one row count against the cap: ``scheduled``, ``auto_join`` not off, a future time."""
    return status == "scheduled" and future_bookings([data], now) == 1


def cap_reached(held_by_others: int) -> Optional[int]:
    """The cap, when the account's OTHER future bookings leave no room for one more; else None."""
    cap = booked_max_per_account()
    return cap if held_by_others >= cap else None


def stamp_over_cap(data: dict, cap: int) -> None:
    """Mirror the row without a bot: ``auto_join`` off and the reason, for the owner to see."""
    data["auto_join"] = False
    data["auto_join_error"] = cap_message(cap)


def clear_cap_stamp(data: dict) -> None:
    """A row armed again under the cap no longer carries the cap's refusal."""
    err = data.get("auto_join_error")
    if isinstance(err, str) and err.startswith(CAP_REACHED):
        data.pop("auto_join_error", None)
