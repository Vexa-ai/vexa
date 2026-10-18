"""Flows own time (ADR-0046 decision 3): a schedule flow's cron, fired by the worker.

A schedule flow is a `flow_version` row with `cron` set. The worker asks `fire_due` every
reconcile pass; it turns the flow's most recent slot into an ordinary reaction keyed
`schedule:<scope>/<name>:<slot>`, so a slot fires AT MOST ONCE by the reaction table's own unique
constraint — two workers, a restart mid-pass, a pass that runs twice in one minute: one reaction.

THE CATCH-UP RULE, stated once. Only the MOST RECENT slot at or before now is ever a candidate.
It fires if it is no older than `catchup_s` and not earlier than the version's creation. Every
slot between the last one that fired and it is skipped — never replayed — and counted in
`SKIPPED`, with a log line. So a flows outage delays a schedule by at most the outage and costs at
most one burst of one run per schedule, whatever the length of the outage.

Cron is five fields, UTC: minute hour day-of-month month day-of-week. Each field takes `*`, a
number, `a-b`, `a,b,c` and a `/n` step on any of them; day-of-week takes 0-7 (0 and 7 are Sunday).
When both day fields are restricted a day matches either, as in every cron since Vixie's."""
from __future__ import annotations

import logging
import time as _time
from collections import Counter
from dataclasses import dataclass

from .admission import RUNS_PER_DAY, admissible, insert
from .clock import Clock
from .db import DB, dumps
from .registry import SCHEDULE_EVENT, Flow, Registry

_log = logging.getLogger(__name__)

CATCHUP_S = 3600.0
#: Slots passed over by the catch-up rule, per flow identity — counts only.
SKIPPED: Counter = Counter()
#: The newest slot already counted per identity, so a slot left unfired is counted once, not on
#: every pass.
_COUNTED: dict[str, tuple] = {}

_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


class CronError(ValueError):
    """A cron this engine cannot read. Raised at submission, never mid-fire."""


@dataclass(frozen=True)
class Cron:
    minutes: frozenset
    hours: frozenset
    days: frozenset
    months: frozenset
    weekdays: frozenset          # 0 = Sunday … 6 = Saturday
    dom_any: bool
    dow_any: bool

    def day_matches(self, tm) -> bool:
        if tm.tm_mon not in self.months:
            return False
        wd = (tm.tm_wday + 1) % 7            # gmtime: Monday = 0 → cron: Sunday = 0
        dom, dow = tm.tm_mday in self.days, wd in self.weekdays
        if self.dom_any or self.dow_any:
            return dom and dow
        return dom or dow

    def matches(self, t: float) -> bool:
        tm = _time.gmtime(t)
        return tm.tm_min in self.minutes and tm.tm_hour in self.hours and self.day_matches(tm)


def _field(text: str, lo: int, hi: int, name: str) -> frozenset:
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"{name}: empty list item")
        rng, _, step_s = part.partition("/")
        step = 1
        if step_s:
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"{name}: bad step {step_s!r}")
            step = int(step_s)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a_s, _, b_s = rng.partition("-")
            if not (a_s.isdigit() and b_s.isdigit()):
                raise CronError(f"{name}: bad range {rng!r}")
            a, b = int(a_s), int(b_s)
        elif rng.isdigit():
            a = b = int(rng)
            if step_s:
                b = hi
        else:
            raise CronError(f"{name}: {rng!r} is not a number, a range or *")
        if a < lo or b > hi or a > b:
            raise CronError(f"{name}: {part!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse(expr: str) -> Cron:
    fields = str(expr or "").split()
    if len(fields) != 5:
        raise CronError("a cron is five fields — minute hour day-of-month month day-of-week, UTC")
    names = ("minute", "hour", "day-of-month", "month", "day-of-week")
    vals = [_field(f, lo, hi, n) for f, (lo, hi), n in zip(fields, _BOUNDS, names)]
    weekdays = frozenset(d % 7 for d in vals[4])
    return Cron(vals[0], vals[1], vals[2], vals[3], weekdays,
                dom_any=fields[2] == "*", dow_any=fields[4] == "*")


#: How far back or ahead the slot search walks, in minutes — a little over a year, so a cron
#: that names a single day of the year still resolves, and one that can never match (31 February)
#: answers None rather than spinning.
_HORIZON_MIN = 60 * 24 * 367


def _walk(cron: Cron, t: int, direction: int) -> float | None:
    """Minute by minute, but a whole hour at a time where the hour or the day cannot match."""
    limit = t + direction * _HORIZON_MIN * 60
    while (t >= limit) if direction < 0 else (t <= limit):
        tm = _time.gmtime(t)
        if tm.tm_hour not in cron.hours or not cron.day_matches(tm):
            hour_start = t - t % 3600
            t = hour_start - 60 if direction < 0 else hour_start + 3600
            continue
        if tm.tm_min in cron.minutes:
            return float(t)
        t += 60 * direction
    return None


def previous_slot(cron: Cron, now: float) -> float | None:
    """The latest minute at or before `now` the cron fires on, or None."""
    return _walk(cron, int(now) - int(now) % 60, -1)


def next_slot(cron: Cron, after: float) -> float | None:
    """The first minute strictly after `after` the cron fires on, or None."""
    return _walk(cron, int(after) - int(after) % 60 + 60, 1)


#: Where `min_interval` samples from by default: a Monday, 2027-01-04 00:00 UTC — any fixed instant
#: works, and a fixed one makes the answer the same on every call.
_SAMPLE_FROM = 1799020800.0


def min_interval(expr: str, *, start: float | None = None, samples: int = 64) -> float:
    """The shortest gap between two consecutive fires over the next `samples` fires — what the
    `quota_min_interval` refusal compares against. `inf` for a cron that fires at most once."""
    cron = parse(expr)
    t = start if start is not None else _SAMPLE_FROM
    gaps = []
    prev = next_slot(cron, t - 60)
    for _ in range(samples):
        if prev is None:
            break
        nxt = next_slot(cron, prev)
        if nxt is None:
            break
        gaps.append(nxt - prev)
        prev = nxt
    return min(gaps) if gaps else float("inf")


def slot_key(flow: Flow, slot: float) -> str:
    return f"schedule:{flow.scope}/{flow.name}:{int(slot)}"


def _last_fired(db: DB, flow: Flow) -> float | None:
    rows = db.execute("SELECT subject_refs FROM reaction WHERE flow = :f AND event_type = :et "
                      "ORDER BY created_at DESC LIMIT 1", {"f": flow.ident, "et": SCHEDULE_EVENT})
    if not rows:
        return None
    import json
    try:
        return float(json.loads(rows[0][0]).get("slot"))
    except (TypeError, ValueError, AttributeError):
        return None


REFUSED_EVENT = "schedule.refused"


def refusal_text(reason: str, *, runs_per_day: int, next_at: "float | None") -> str:
    """One sentence a person reads in the routine's chat and on the bell."""
    import time as _t
    when = (" The next run can start after " + _t.strftime("%H:%M UTC", _t.gmtime(next_at)) + "."
            if next_at else "")
    if reason == "quota_runs_per_day":
        return (f"Not run: your routines reached the daily limit of {runs_per_day} runs "
                f"(counted over the last 24 hours).{when}")
    return f"Not run: refused ({reason}).{when}"


def record_refusal(db: DB, clock: Clock, flow: Flow, reason: str, refs: dict, *,
                   runs_per_day: int) -> bool:
    """A schedule fire refused for a person's flow is RECORDED, once per flow, reason and UTC day —
    never once per slot — as a finished reaction the person can see (`/queue/waiting`, `GET /flows`)
    and the worker announces in the routine's chat. True when this call recorded it."""
    import time as _t
    from .admission import next_run_possible
    now = clock.now()
    next_at = (next_run_possible(db, flow.owner, now, runs_per_day=runs_per_day)
               if reason == "quota_runs_per_day" else None)
    day = _t.strftime("%Y-%m-%d", _t.gmtime(now))
    key = f"refused:{flow.ident}:{reason}:{day}"
    text = refusal_text(reason, runs_per_day=runs_per_day, next_at=next_at)
    rows = db.execute(
        """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                 flow, flow_version, step, status, attempt, next_run_at, reason,
                                 created_at, updated_at)
           VALUES (:rid, :sid, :et, :refs, :flow, :ver, :step, 'failed', 0, :now, :why, :now, :now)
           ON CONFLICT (source_event_id) DO NOTHING
           RETURNING reaction_id""",
        {"rid": __import__("uuid").uuid4().hex, "sid": key, "et": REFUSED_EVENT,
         "refs": dumps({**refs, "refused": reason, "next_run_at": next_at, "text": text}),
         "flow": flow.ident, "ver": flow.version, "step": flow.steps[0],
         "why": f"{reason}: {text}", "now": now})
    return bool(rows)


def fire_due(db: DB, registry: Registry, clock: Clock, *, catchup_s: float = CATCHUP_S,
             **limits) -> int:
    """Fire every schedule flow whose most recent slot has not fired. Returns reactions created."""
    now = clock.now()
    created = 0
    for flow in registry.schedules():
        try:
            cron = parse(flow.cron or "")
        except CronError:
            continue                          # refused at submission; a hand-written row stays inert
        slot = previous_slot(cron, now)
        once = is_one_shot(flow)
        # A ONE-SHOT made inside its own minute ("at 12:13", saved at 12:13:20) still fires: its slot
        # is the minute it names, which began before the version existed.
        earliest = flow.created_at - 60 if once else flow.created_at
        if slot is None or slot < earliest:
            continue
        last = _last_fired(db, flow)
        if last is not None and last >= slot:
            if once:
                retire_spent(db, flow)
            continue
        if now - slot > catchup_s:
            _count_skipped(flow, cron, last, slot, include_slot=True)
            continue
        _count_skipped(flow, cron, last, slot, include_slot=False)
        refs = {"slot": slot, "scope": flow.scope, "flow": flow.name}
        if flow.owner:
            refs["uid"] = flow.owner
        refused: list = []
        if not admissible(db, flow, clock, event_type=SCHEDULE_EVENT, subject_refs=refs,
                          refusals=refused, **limits):
            if flow.owner and refused:
                record_refusal(db, clock, flow, refused[0], refs,
                               runs_per_day=int(limits.get("runs_per_day", RUNS_PER_DAY)))
            continue
        key = slot_key(flow, slot)
        rows = insert(db, clock, flow, key=f"{key}::{flow.ident}", event_type=SCHEDULE_EVENT,
                      subject_refs=refs)
        created += len(rows or [])
        if once:
            retire_spent(db, flow)
    return created


def is_one_shot(flow: Flow) -> bool:
    """A schedule made from a time, not a cron (`trigger: {kind: at}`): it fires once and retires."""
    try:
        return bool(flow.param("once", False))
    except Exception:  # noqa: BLE001 — a param that cannot be read is not a one-shot
        return False


def retire_spent(db: DB, flow: Flow) -> None:
    """A one-shot that has fired stops being chosen (flows phase 0). Its run, already admitted on
    this version, finishes on it — a retired version keeps its definition for the runs it has."""
    db.execute("UPDATE flow_version SET status = 'retired' WHERE scope = :sc AND name = :n "
               "AND version = :v AND status = 'active'",
               {"sc": flow.scope, "n": flow.name, "v": flow.version})
    _log.info("one-shot %s@%d fired and retired", flow.ident, flow.version)


def _count_skipped(flow: Flow, cron: Cron, last: float | None, slot: float, *,
                   include_slot: bool) -> None:
    """Count the slots the catch-up rule passed over between the last fire and `slot`."""
    mark = (last, slot, include_slot)
    if _COUNTED.get(flow.ident) == mark:
        return
    _COUNTED[flow.ident] = mark
    start = max(last if last is not None else flow.created_at, flow.created_at)
    if not start:
        return
    n, t = 0, next_slot(cron, start if last is not None else start - 60)
    while t is not None and t < slot and n < 100_000:
        n += 1
        t = next_slot(cron, t)
    if include_slot:
        n += 1
    if n:
        SKIPPED[flow.ident] += n
        _log.warning("schedule %s@%d: %d slot(s) skipped by the catch-up rule (%d so far)",
                     flow.ident, flow.version, n, SKIPPED[flow.ident])
