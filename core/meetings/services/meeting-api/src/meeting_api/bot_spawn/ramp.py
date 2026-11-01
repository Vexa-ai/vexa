"""The per-account bot ramp (#1962): how many spawns an account may start per window, beside the
concurrency ceiling.

Three dials: burst **B**, share **S**, window **W** (deployment defaults 5, 0.5, 60 s; env
``BOT_RAMP_BURST`` / ``BOT_RAMP_SHARE`` / ``BOT_RAMP_WINDOW_S``; per account ``users.data.bot_ramp``
via admin-api's bot-context).

  * **running** — the account's active bots, the same count the ceiling uses;
  * **started** — spawns admitted in the trailing W seconds (``meetings.data.spawn_admitted_at``
    newer than ``now - W``), whatever became of them since;
  * **base** — running bots admitted before the window (stamp at or before ``now - W``, or none);
  * **allowance** = ``max(B, floor(S * base))``.

The ramp applies only when it is on (``B > 0``), a ceiling is known and the ceiling is above B.
Then a spawn is admitted only if ``running < ceiling`` and ``started < allowance``. ``allowance``
reads ``base``, not ``running``, so bots admitted inside a window never raise that window's own
allowance: from zero the default curve admits 5, 5, 5, 7, 11, 16, 24, 36, 54, 81, 122 per minute.

Everything here is pure: the repo adapters (SQL and in-memory) gather the rows under the per-user
advisory lock and call these functions, so the two cannot drift.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

#: The ``meetings.data`` key the three admitting paths stamp. Internal: dropped from every response
#: projection (``collector.projection.OPT_IN_KEYS``) and every webhook (``_INTERNAL_DATA_KEYS``).
#: Not ``bot_admitted_at``, which is public and means the bot was let into the meeting.
STAMP_KEY = "spawn_admitted_at"

DEFAULT_BURST = 5
DEFAULT_SHARE = 0.5
DEFAULT_WINDOW_S = 60
WINDOW_MIN_S = 10
WINDOW_MAX_S = 3600

#: The ceiling refusal's ``Retry-After``: a polling hint, since a slot frees only when a bot ends.
CEILING_RETRY_AFTER_S = 60

#: The most entries ``project_curve`` returns.
CURVE_MAX_ENTRIES = 60

_ENV = {"burst": "BOT_RAMP_BURST", "share": "BOT_RAMP_SHARE", "window_s": "BOT_RAMP_WINDOW_S"}


@dataclass(frozen=True)
class RampDials:
    burst: int
    share: float
    window_s: int
    #: ``"deployment"`` or ``"account"`` (an account override set at least one dial).
    source: str = "deployment"


@dataclass(frozen=True)
class RampState:
    running: int
    base: int
    started: int
    #: ``None`` when the ramp does not apply (off, no ceiling, or ceiling <= burst).
    allowance: Optional[int]
    #: ``None``, ``"ceiling"`` or ``"ramp"``.
    blocked_by: Optional[str]
    #: Seconds until a start is admissible; ``0`` when one is now, ``None`` when the ceiling blocks.
    retry_after: Optional[int]


class RampConfigError(ValueError):
    """A ``BOT_RAMP_*`` value out of range — the boot refuses and names the variable."""


def _parse_burst(raw: Any) -> int:
    value = int(str(raw).strip())
    if value < 0:
        raise ValueError("must be an integer >= 0 (0 turns the ramp off)")
    return value


def _parse_share(raw: Any) -> float:
    value = float(str(raw).strip())
    if not (0.0 <= value <= 1.0) or math.isnan(value):
        raise ValueError("must be a number from 0 to 1")
    return value


def _parse_window(raw: Any) -> int:
    value = int(str(raw).strip())
    if not (WINDOW_MIN_S <= value <= WINDOW_MAX_S):
        raise ValueError(f"must be an integer from {WINDOW_MIN_S} to {WINDOW_MAX_S}")
    return value


def deployment_dials(env: Optional[Mapping[str, str]] = None) -> RampDials:
    """The deployment's dials from ``BOT_RAMP_*``. An empty or unset value is the default; a value
    out of range raises ``RampConfigError`` naming the variable (the boot hook refuses on it)."""
    env = os.environ if env is None else env
    parsers = {"burst": _parse_burst, "share": _parse_share, "window_s": _parse_window}
    defaults = {"burst": DEFAULT_BURST, "share": DEFAULT_SHARE, "window_s": DEFAULT_WINDOW_S}
    values: dict[str, Any] = {}
    for dial, var in _ENV.items():
        raw = env.get(var)
        if raw is None or not str(raw).strip():
            values[dial] = defaults[dial]
            continue
        try:
            values[dial] = parsers[dial](raw)
        except ValueError as e:
            reason = str(e) if "must be" in str(e) else "is not a number"
            raise RampConfigError(f"{var}={raw!r} {reason}") from None
    return RampDials(**values)


def resolve_ramp(override: Any, deployment: Optional[RampDials]) -> Optional[RampDials]:
    """The dials one account runs under, or ``None`` when the ramp is off.

    ``deployment`` with ``burst == 0`` (``BOT_RAMP_BURST=0``) turns the ramp off for every account.
    ``override`` is the account's ``bot_ramp`` (``{"burst", "share", "window_s"}``, any subset) from
    bot-context; a missing or unreadable key falls back to the deployment value, so a lookup that
    failed (``{}``) is simply the deployment's dials."""
    if deployment is None or deployment.burst <= 0:
        return None
    if not isinstance(override, Mapping):
        return deployment
    parsers = {"burst": _parse_burst, "share": _parse_share, "window_s": _parse_window}
    changes: dict[str, Any] = {}
    for dial, parse in parsers.items():
        raw = override.get(dial)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = parse(raw)
        except (TypeError, ValueError):
            continue
        if dial == "burst" and value < 1:
            continue  # an account cannot turn the ramp off; admin-api refuses it too
        changes[dial] = value
    if not changes:
        return deployment
    return replace(deployment, source="account", **changes)


def ramp_applies(dials: Optional[RampDials], ceiling: Optional[int]) -> bool:
    return dials is not None and ceiling is not None and ceiling > dials.burst


def allowance_for(dials: RampDials, base: int) -> int:
    return max(dials.burst, math.floor(dials.share * base))


def format_stamp(at: datetime) -> str:
    """ISO-8601 UTC with milliseconds and ``Z`` (fixed width, so stamps also sort as text)."""
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    at = at.astimezone(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def parse_stamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def window_counts(
    rows: Iterable[tuple[bool, Any]], *, now: datetime, window_s: int
) -> tuple[int, list[datetime]]:
    """``(base, window_stamps)`` from ``(is_active, stamp)`` rows of one account.

    ``window_stamps`` (oldest first) are every row stamped inside ``(now - W, now]``, active or not
    (a failed or stopped spawn still counts while it is in the window); ``base`` counts the ACTIVE
    rows stamped at or before ``now - W``, or never stamped (admitted before the stamp existed)."""
    cutoff = now - timedelta(seconds=window_s)
    base = 0
    stamps: list[datetime] = []
    for active, raw in rows:
        at = parse_stamp(raw)
        if at is not None and at > cutoff:
            stamps.append(at)
        elif active:
            base += 1
    stamps.sort()
    return base, stamps


def retry_after(
    dials: RampDials, allowance: int, window_stamps: Sequence[datetime], now: datetime
) -> int:
    """Seconds until the ramp admits one more start: the ``k``-th oldest in-window stamp leaves the
    window, ``k = started - allowance + 1``. ``ceil(stamp_k + W - now)``, clamped to 1..W."""
    started = len(window_stamps)
    k = started - allowance + 1
    if k < 1:
        return 0
    stamp_k = sorted(window_stamps)[k - 1]
    seconds = math.ceil((stamp_k + timedelta(seconds=dials.window_s) - now).total_seconds())
    return max(1, min(dials.window_s, seconds))


def ramp_state(
    dials: Optional[RampDials],
    ceiling: Optional[int],
    *,
    running: int,
    base: int,
    window_stamps: Sequence[datetime],
    now: datetime,
) -> RampState:
    """Where an account stands: the same arithmetic the spawn check uses. The ceiling is checked
    first, so a request past both dials is told about the ceiling."""
    started = len(window_stamps)
    if ceiling is not None and running >= ceiling:
        allowance = allowance_for(dials, base) if ramp_applies(dials, ceiling) else None
        return RampState(running, base, started, allowance, "ceiling", None)
    if not ramp_applies(dials, ceiling):
        return RampState(running, base, started, None, None, 0)
    allowance = allowance_for(dials, base)
    if started < allowance:
        return RampState(running, base, started, allowance, None, 0)
    return RampState(running, base, started, allowance, "ramp",
                     retry_after(dials, allowance, window_stamps, now))


def enforce(
    dials: Optional[RampDials],
    ceiling: Optional[int],
    *,
    user_id: int,
    running: int,
    rows: Iterable[tuple[bool, Any]],
    now: datetime,
) -> None:
    """The spawn check, run by both repo adapters under the per-user lock right after the ceiling:
    raise ``RampExceeded`` when the ramp applies and the account has started its allowance.
    ``rows`` are the account's ``(is_active, stamp)`` pairs (``browser_session`` excluded)."""
    if not ramp_applies(dials, ceiling):
        return
    base, stamps = window_counts(rows, now=now, window_s=dials.window_s)
    state = ramp_state(dials, ceiling, running=running, base=base, window_stamps=stamps, now=now)
    if state.blocked_by == "ramp":
        from .ports import RampExceeded

        raise RampExceeded(user_id, started=state.started, window_s=dials.window_s,
                           allowance=state.allowance, running=running,
                           retry_after=state.retry_after)


def project_curve(
    dials: Optional[RampDials],
    ceiling: Optional[int],
    *,
    running: int,
    base: int,
    window_stamps: Sequence[datetime],
    now: datetime,
    max_entries: int = CURVE_MAX_ENTRIES,
) -> list[dict]:
    """The projected schedule up to the ceiling, if every admissible start is taken the moment it is
    admissible and every bot keeps running: ``[{"after_seconds", "total"}]``, ``total`` counting
    running bots. ``[]`` when the ramp does not apply or the ceiling is reached."""
    if not ramp_applies(dials, ceiling) or running >= ceiling:
        return []
    window = timedelta(seconds=dials.window_s)
    # Admission times that matter from here on: the base bots (older than any window) and every
    # start still inside the window.
    old = now - window - timedelta(days=1)
    admitted = [old] * base + sorted(window_stamps)
    total = running
    t = now
    curve: list[dict] = []
    while total < ceiling and len(curve) < max_entries:
        cutoff = t - window
        started = sum(1 for a in admitted if a > cutoff)
        base_t = sum(1 for a in admitted if a <= cutoff)
        can = min(allowance_for(dials, base_t) - started, ceiling - total)
        if can > 0:
            admitted += [t] * can
            total += can
            curve.append({"after_seconds": math.ceil((t - now).total_seconds()), "total": total})
            continue
        upcoming = [a + window for a in admitted if a > cutoff]
        if not upcoming:
            break
        t = min(upcoming)
    return curve
