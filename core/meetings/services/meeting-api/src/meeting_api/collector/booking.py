"""The booking bounds on a planned row (ADR-0052 §3, §8) — one module, read by both repos' writers.

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

The same writers refuse a booking no scheduler would ever start (ADR-0052 §3, Q1 (a), decided
4 Nov 2026). Flows starts every booked meeting's bot, and meeting-api reaches it at
``VEXA_FLOWS_API_URL``. Where that is unset, a row that BECOMES ``scheduled`` with ``auto_join`` on is
refused by name — ``422`` ``scheduling_not_deployed`` naming the key — and calendar sync still
mirrors the event, with ``auto_join`` off and ``auto_join_error`` "scheduling not deployed". A plan
with ``auto_join`` off is never refused, and nothing else in meetings needs a scheduler.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

BOOKED_MAX_ENV = "BOOKED_MAX_PER_ACCOUNT"
DEFAULT_BOOKED_MAX_PER_ACCOUNT = 500
CAP_REACHED = "booking cap reached"
#: The scheduler's address on meeting-api: Flows, whose ``booked_bot`` starts booked bots.
SCHEDULER_ENV = "VEXA_FLOWS_API_URL"
SCHEDULING_NOT_DEPLOYED = "scheduling not deployed"
#: The refusal's ``code`` — what a program branches on.
SCHEDULING_NOT_DEPLOYED_CODE = "scheduling_not_deployed"


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


def scheduler_configured(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is a scheduler configured: ``VEXA_FLOWS_API_URL`` set to something other than blanks."""
    raw = (os.environ if env is None else env).get(SCHEDULER_ENV)
    return bool(raw and raw.strip())


def is_booking(status: Any, data: Any) -> bool:
    """A row a scheduler would start a bot for: ``scheduled``, ``auto_join`` not off, not stopped."""
    return (status == "scheduled" and isinstance(data, dict)
            and data.get("auto_join") is not False and not data.get("stop_requested"))


def unscheduled(status: Any, data: Any, *, was: bool = False) -> bool:
    """Does this write make a booking no scheduler will start: the row becomes a booking (it was
    not one before, ``was``) on a deployment with no scheduler configured."""
    return is_booking(status, data) and not was and not scheduler_configured()


def scheduling_refusal() -> dict:
    """The ``422`` body: ``detail`` (a sentence, like every 422 here), ``code`` and the ``key`` to set."""
    return {"detail": (f"{SCHEDULING_NOT_DEPLOYED}: this deployment runs no scheduler, so a meeting "
                       f"booked with auto_join would never get its bot. Set {SCHEDULER_ENV} on "
                       f"meeting-api to the Flows API, or book with \"auto_join\": false"),
            "code": SCHEDULING_NOT_DEPLOYED_CODE, "key": SCHEDULER_ENV}


def stamp_unscheduled(data: dict) -> None:
    """Mirror the row without a bot: ``auto_join`` off and the reason, for the owner to see."""
    data["auto_join"] = False
    data["auto_join_error"] = SCHEDULING_NOT_DEPLOYED


def clear_unscheduled_stamp(data: dict) -> None:
    """A row armed again once a scheduler is configured no longer carries that refusal."""
    if data.get("auto_join_error") == SCHEDULING_NOT_DEPLOYED:
        data.pop("auto_join_error", None)


def clear_cap_stamp(data: dict) -> None:
    """A row armed again under the cap no longer carries the cap's refusal."""
    err = data.get("auto_join_error")
    if isinstance(err, str) and err.startswith(CAP_REACHED):
        data.pop("auto_join_error", None)
