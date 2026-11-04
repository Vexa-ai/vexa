"""Every per-account bot limit, in one model (founder, 4 Nov 2026: «all the user limits in a holistic
way»; every bot limit lives in Meetings).

One account has these limits, each with one key and one built-in default:

| key | default | what it bounds | enforced |
|---|---|---|---|
| ``max_concurrent_bots`` | none | bots at once, queued ones counted (``users.max_concurrent_bots``) | ``create_meeting_guarded`` (429) |
| ``bot_start_rate`` | burst 10, 0.5/min | bot starts, every path (``POST /bots``, the Flows door, auto-join, a continued run) | ``create_meeting_guarded`` / ``reopen_meeting``, under the per-user lock (429 ``bot_start_rate``) |
| ``seat_cooldown_s`` | 60 | a user-stopped ad-hoc bot keeps its seat this long | the same cap check; booked bots exempt |
| ``queue_wait_s`` | 1800 | how long a queued bot waits for a seat once late | stamped on the run at spawn; lifecycle/queue.py and the reaper read the run's value |
| ``max_scheduled_bots`` | 5 | booked bots overlapping one slot | at booking (the scheduled-bots train; not enforced here) |
| ``booking_rate`` | burst 200, 10/h | planned rows an account creates | at booking (the scheduled-bots train; not enforced here) |

WHERE THE VALUES COME FROM. One carrier, versioned: bot-limits.v1 (core/identity/contracts/
bot-limits.v1), the ``bot_limits`` field of the identity domain's internal bot-context answer
(``GET /internal/users/{id}/bot-context``), which ``request_bot`` reads on every spawn path. admin-api
resolves it: ``max_concurrent_bots`` is the account's ``users.max_concurrent_bots`` column; every other
key is the account's override (``users.data["bot_limits"]``), else its plan's (``platform_settings``
``bot_limits.plans[<users.data.subscription_tier>]``), else the platform default
(``bot_limits.default``). meeting-api reads no ``X-User-Limits`` header, no identity table and no
identity env for limits. An answer with no ``bot_limits``, or a ``version`` this build does not know,
is ``LimitsUnavailable``: the start is refused, never run unlimited.

This module is the one parser: an absent key, or a value that is not a valid limit, gets the built-in
default (a typo never removes a limit). A new limit is one row in ``_FIELDS``; the carrier already
takes any key as a plain resolved value (bot-limits.v1 ``Limit``), so it needs no new path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

#: The bot-context key of the resolved limits (and the platform_settings / users.data key admin-api
#: resolves it from).
CONTEXT_KEY = "bot_limits"
#: The bot-limits.v1 ``version`` this build reads.
VERSION = 1


class LimitsUnavailable(Exception):
    """The account's limits could not be read (no bot-limits.v1 answer, or an unknown version):
    the start is refused (HTTP 503), never run past a limit nobody could read."""


@dataclass(frozen=True)
class TokenRate:
    """A token bucket: ``burst`` tokens, refilled at ``per_minute``. One token per event."""

    burst: int
    per_minute: float

    def retry_after_s(self) -> int:
        """Seconds until one token is back in an empty bucket (the 429's ``Retry-After``)."""
        return max(1, math.ceil(60.0 / self.per_minute)) if self.per_minute > 0 else 3600

    def horizon_s(self) -> float:
        """How far back the recorded events are replayed (``allows``): four refills of the whole
        bucket, at most a day. Past it the bucket is taken to have been full, which lets a sustained
        abuser through at most ``(1 + 1/4)`` times the rate."""
        if self.per_minute <= 0:
            return 86400.0
        return min(86400.0, 4 * 60.0 * self.burst / self.per_minute)

    def allows(self, ages_s) -> bool:
        """Whether one more event may happen now, given how many seconds ago each recorded event in
        the horizon happened. Replays the bucket from full at the horizon's edge, oldest first."""
        rate = self.per_minute / 60.0
        horizon = self.horizon_s()
        tokens, at = float(self.burst), horizon
        for age in sorted((max(0.0, a) for a in ages_s if a <= horizon), reverse=True):
            tokens = min(float(self.burst), tokens + (at - age) * rate) - 1.0
            at = age
        tokens = min(float(self.burst), tokens + at * rate)
        return tokens >= 1.0


DEFAULT_BOT_START_RATE = TokenRate(burst=10, per_minute=0.5)
DEFAULT_SEAT_COOLDOWN_S = 60
DEFAULT_QUEUE_WAIT_S = 1800
DEFAULT_SCHEDULED_BOTS = 5
DEFAULT_BOOKING_RATE = TokenRate(burst=200, per_minute=10 / 60)


@dataclass(frozen=True)
class AccountLimits:
    max_concurrent_bots: Optional[int] = None
    bot_start_rate: TokenRate = field(default_factory=lambda: DEFAULT_BOT_START_RATE)
    seat_cooldown_s: int = DEFAULT_SEAT_COOLDOWN_S
    queue_wait_s: int = DEFAULT_QUEUE_WAIT_S
    max_scheduled_bots: int = DEFAULT_SCHEDULED_BOTS
    booking_rate: TokenRate = field(default_factory=lambda: DEFAULT_BOOKING_RATE)


def _number(value: Any) -> Optional[float]:
    """A finite number, or None: bool, NaN and infinity are not limits."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _count(value: Any) -> Optional[int]:
    """A whole-number limit. A negative one is 0 (nothing allowed), never "no limit"; a fraction is
    not a count and is ignored, so the default applies."""
    n = _number(value)
    if n is None or not n.is_integer():
        return None
    return max(0, int(n))


def _seconds(value: Any) -> Optional[int]:
    """A non-negative duration, a fraction rounded UP (a limit never shortens by rounding)."""
    n = _number(value)
    if n is None or n < 0:
        return None
    return int(math.ceil(n))


def _rate(value: Any) -> Optional[TokenRate]:
    if not isinstance(value, Mapping):
        return None
    burst, per_minute = _count(value.get("burst")), _number(value.get("per_minute"))
    if burst is None or burst < 1 or per_minute is None or per_minute < 0:
        return None
    return TokenRate(burst=burst, per_minute=per_minute)


_FIELDS = {
    "bot_start_rate": _rate,
    "seat_cooldown_s": _seconds,
    "queue_wait_s": _seconds,
    "max_scheduled_bots": _count,
    "booking_rate": _rate,
}


def resolve(bot_limits: Any) -> AccountLimits:
    """An account's limits from its bot-limits.v1 object: the built-in defaults, overlaid by each
    valid value. ``LimitsUnavailable`` when it is not a version-1 object with a readable cap."""
    if not isinstance(bot_limits, Mapping) or bot_limits.get("version") != VERSION:
        raise LimitsUnavailable("no bot-limits.v1 answer")
    cap = _count(bot_limits.get("max_concurrent_bots"))
    if cap is None:
        raise LimitsUnavailable("bot-limits.v1 carries no readable max_concurrent_bots")
    changes = {}
    for key, parse in _FIELDS.items():
        if key in bot_limits:
            value = parse(bot_limits[key])
            if value is not None:
                changes[key] = value
    return replace(AccountLimits(max_concurrent_bots=cap), **changes)
