"""Every per-account bot limit, in one model (founder, 4 Nov 2026: «all the user limits in a holistic
way»; every bot limit lives in Meetings, Flows keeps only the clock).

One account has these limits, each with one key and one default:

| key | default | what it bounds | enforced |
|---|---|---|---|
| ``max_concurrent_bots`` | none | ad-hoc bots at once, queued ones counted (``users.max_concurrent_bots``) | meeting-api ``POST /bots`` (429) |
| ``max_scheduled_bots`` | 5 | booked bots overlapping one slot | at booking, scheduled-bots train (not here) |
| ``bot_start_rate`` | burst 10, 0.5/min | ad-hoc bot starts (``POST /bots``) | gateway, in memory per replica; service callers never pass it |
| ``seat_cooldown_s`` | 60 | a user-stopped ad-hoc bot keeps its seat this long | meeting-api ``POST /bots`` cap check; booked bots exempt |
| ``queue_wait_s`` | 1800 | how long a queued bot waits for a seat once late | meeting-api capacity queue |
| ``booking_rate`` | burst 200, 10/h | planned rows an account creates | the planned-row creation path, scheduled-bots train (not here) |

Resolution, most specific first: the account's override (``users.data["bot_limits"]``), then its
plan's defaults (one ``platform_settings`` row, ``bot_limits``: ``{"plans": {<plan>: {...}},
"default": {...}}``), then the defaults above. ``max_concurrent_bots`` is the exception: the
account's own column is the value, as it always was. A value that is not a valid limit is ignored
(the next level applies), so a typo never lifts a limit.

What crosses to meeting-api today is the gateway's ``X-User-Limits`` header, a decimal integer
(gateway-identity.v1 ``claims.limits``: the account's ``max_concurrent_bots``). ``from_header`` also
reads the JSON object this module resolves to (``AccountLimits.to_json``), so the rest of the set can
ride the same header once the identity claim carries it.

Self-contained on purpose (stdlib only, no meeting-api import): the scheduled-bots train ports this
file and its tests unchanged.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping, Optional

#: The ``platform_settings`` row holding per-plan defaults, and the ``users.data`` key of an override.
PLATFORM_SETTINGS_KEY = "bot_limits"
USER_DATA_KEY = "bot_limits"


@dataclass(frozen=True)
class TokenRate:
    """A token bucket: ``burst`` tokens, refilled at ``per_minute``. One token per event."""

    burst: int
    per_minute: float

    @property
    def per_second(self) -> float:
        return self.per_minute / 60.0

    def retry_after_s(self) -> int:
        """Seconds until one token is back in an empty bucket (the 429's ``Retry-After``)."""
        return max(1, math.ceil(60.0 / self.per_minute)) if self.per_minute > 0 else 3600


DEFAULT_SCHEDULED_BOTS = 5
DEFAULT_BOT_START_RATE = TokenRate(burst=10, per_minute=0.5)
DEFAULT_SEAT_COOLDOWN_S = 60
DEFAULT_QUEUE_WAIT_S = 1800
DEFAULT_BOOKING_RATE = TokenRate(burst=200, per_minute=10 / 60)


@dataclass(frozen=True)
class AccountLimits:
    max_concurrent_bots: Optional[int] = None
    max_scheduled_bots: int = DEFAULT_SCHEDULED_BOTS
    bot_start_rate: TokenRate = field(default_factory=lambda: DEFAULT_BOT_START_RATE)
    seat_cooldown_s: int = DEFAULT_SEAT_COOLDOWN_S
    queue_wait_s: int = DEFAULT_QUEUE_WAIT_S
    booking_rate: TokenRate = field(default_factory=lambda: DEFAULT_BOOKING_RATE)

    def to_json(self) -> str:
        """The JSON object ``from_header`` reads back (keys as in the table above)."""
        body = asdict(self)
        if body["max_concurrent_bots"] is None:
            body.pop("max_concurrent_bots")
        return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _count(value: Any) -> Optional[int]:
    """A non-negative integer limit, or None (bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _seconds(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _rate(value: Any) -> Optional[TokenRate]:
    if not isinstance(value, Mapping):
        return None
    burst, per_minute = _count(value.get("burst")), value.get("per_minute")
    if burst is None or burst < 1 or isinstance(per_minute, bool) \
            or not isinstance(per_minute, (int, float)) or per_minute < 0:
        return None
    return TokenRate(burst=burst, per_minute=float(per_minute))


_FIELDS = {
    "max_scheduled_bots": _count,
    "bot_start_rate": _rate,
    "seat_cooldown_s": _seconds,
    "queue_wait_s": _seconds,
    "booking_rate": _rate,
}


def _apply(limits: AccountLimits, layer: Any) -> AccountLimits:
    if not isinstance(layer, Mapping):
        return limits
    changes = {}
    for key, parse in _FIELDS.items():
        if key in layer:
            value = parse(layer[key])
            if value is not None:
                changes[key] = value
    return replace(limits, **changes) if changes else limits


def resolve(
    *,
    max_concurrent_bots: Optional[int],
    plan: Optional[str] = None,
    plan_settings: Any = None,
    override: Any = None,
) -> AccountLimits:
    """An account's limits: defaults, then its plan's (``plan_settings`` is the ``bot_limits``
    platform_settings value), then its own override (``users.data["bot_limits"]``)."""
    limits = AccountLimits(max_concurrent_bots=_count(max_concurrent_bots))
    if isinstance(plan_settings, Mapping):
        limits = _apply(limits, plan_settings.get("default"))
        plans = plan_settings.get("plans")
        if plan and isinstance(plans, Mapping):
            limits = _apply(limits, plans.get(plan))
    return _apply(limits, override)


def from_header(x_user_limits: Optional[str]) -> AccountLimits:
    """The limits carried by ``X-User-Limits``: a decimal ``max_concurrent_bots`` (the header as the
    gateway sends it today), or the JSON object of ``AccountLimits.to_json`` (whose legacy
    ``max_concurrent`` spelling is read too). Absent or unreadable → no concurrent cap, defaults for
    the rest; ``0`` is a real cap (no bot may start)."""
    if not x_user_limits or not x_user_limits.strip():
        return AccountLimits()
    raw = x_user_limits.strip()
    try:
        return AccountLimits(max_concurrent_bots=_count(int(raw)))
    except ValueError:
        pass
    try:
        obj = json.loads(raw)
    except ValueError:
        return AccountLimits()
    if not isinstance(obj, Mapping):
        return AccountLimits()
    cap = obj.get("max_concurrent_bots", obj.get("max_concurrent"))
    return _apply(AccountLimits(max_concurrent_bots=_count(cap)), obj)
