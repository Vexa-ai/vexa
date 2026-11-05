"""Bot limits, the arithmetic: no I/O, no clock, no meeting content.

An account has two limits, one per kind of bot: ``scheduled`` (a booked meeting) and ``adhoc``. Each
comes from the account's own peak concurrent bots over the last 14 days, per hour of the week, plus
50% rounded up. An account with no history gets 2 and 2. A cap raised above the plan default is a
floor: it was granted on purpose, and history never takes it away.

``limit`` is the peak across the whole week: what decides ``would``. The per-hour figures only answer
``within_prediction``: is this bot inside what the account normally does at this hour of the week?
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional

KINDS = ("scheduled", "adhoc")
DEFAULT_LIMIT = 2  # a new account, and the least any kind or hour ever predicts
PLAN_DEFAULT_CAP = 5  # users.max_concurrent_bots above this was raised on purpose


@dataclass(frozen=True)
class Interval:
    """One bot's time in a meeting. Naive UTC, like the meetings table's time columns."""

    start: datetime
    end: datetime
    booked: bool


@dataclass(frozen=True)
class KindLimits:
    limit: int = DEFAULT_LIMIT
    hours: Optional[Mapping[int, int]] = None  # hour of the week (0 = Monday 00:00 UTC) -> limit; missing = default

    def at(self, hour: int) -> int:
        return (self.hours or {}).get(hour, DEFAULT_LIMIT)


@dataclass(frozen=True)
class Prediction:
    scheduled: KindLimits = KindLimits()
    adhoc: KindLimits = KindLimits()

    def of(self, kind: str) -> KindLimits:
        return self.scheduled if kind == "scheduled" else self.adhoc

    def to_json(self) -> dict:
        return {
            kind: {"limit": self.of(kind).limit, "hours": {str(h): v for h, v in (self.of(kind).hours or {}).items()}}
            for kind in KINDS
        }

    @classmethod
    def from_json(cls, body: Mapping) -> "Prediction":
        def _kind(part: Mapping) -> KindLimits:
            return KindLimits(limit=int(part["limit"]), hours={int(h): int(v) for h, v in part["hours"].items()})

        return cls(scheduled=_kind(body["scheduled"]), adhoc=_kind(body["adhoc"]))


@dataclass(frozen=True)
class Decision:
    limit: int
    would: str  # allow | queue_over_limit
    within_prediction: bool


def hour_of_week(at: datetime) -> int:
    return at.weekday() * 24 + at.hour


def with_headroom(peak: int) -> int:
    """Peak + 50%, rounded up, never under the new-account limit."""
    return max(DEFAULT_LIMIT, (peak * 3 + 1) // 2)


def raised_floor(cap: Optional[int]) -> int:
    """The account's cap as a floor, only when it was raised above the plan default."""
    return cap if cap is not None and cap > PLAN_DEFAULT_CAP else 0


def peak_by_hour(intervals: Iterable[Interval]) -> dict[int, int]:
    """Hour of the week -> the most bots running at one instant in that hour, on any week.

    An interval ending exactly when another starts is not an overlap. A zero-length interval still
    counts as a bot that ran."""
    events: list[tuple[datetime, int]] = []
    for item in intervals:
        events.append((item.start, 1))
        events.append((max(item.end, item.start + timedelta(seconds=1)), -1))
    events.sort()  # at a tie -1 sorts first: back-to-back bots don't overlap

    peaks: dict[int, int] = {}
    running = 0
    previous: Optional[datetime] = None
    for at, delta in events:
        if running > 0 and previous is not None and at > previous:
            cursor = previous.replace(minute=0, second=0, microsecond=0)
            while cursor < at:
                hour = hour_of_week(cursor)
                peaks[hour] = max(peaks.get(hour, 0), running)
                cursor += timedelta(hours=1)
        running += delta
        previous = at
    return peaks


def _kind_limits(intervals: Iterable[Interval], floor: int) -> KindLimits:
    peaks = peak_by_hour(intervals)
    overall = max(peaks.values(), default=0)
    return KindLimits(
        limit=max(with_headroom(overall), floor),
        hours={hour: max(with_headroom(peak), floor) for hour, peak in sorted(peaks.items())},
    )


def predict(intervals: Iterable[Interval], *, cap: Optional[int] = None) -> Prediction:
    """One account's two limits from its own history."""
    items = list(intervals)
    floor = raised_floor(cap)
    return Prediction(
        scheduled=_kind_limits((i for i in items if i.booked), floor),
        adhoc=_kind_limits((i for i in items if not i.booked), floor),
    )


def decide(prediction: Prediction, *, kind: str, active: int, cap: Optional[int], at: datetime) -> Decision:
    """What the limit would say about one more bot, with ``active`` of this kind already running.

    The cap is applied here as well as in ``predict``: the daily job reads meetings only, and the
    cap belongs to identity, which hands it to every spawn."""
    limits = prediction.of(kind)
    floor = raised_floor(cap)
    limit = max(limits.limit, floor)
    return Decision(
        limit=limit,
        would="allow" if active < limit else "queue_over_limit",
        within_prediction=active + 1 <= max(limits.at(hour_of_week(at)), floor),
    )
