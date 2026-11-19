"""The listener: buffer, scoring, caps and the wake decision — the same for every source.

Nothing here knows what a meeting is. A source hands it confirmed ``Line``s and a ``Brief``; it hands
back the annotations to write and, when it is time, the reason to wake. Its whole memory is one
``State`` per source, which the runner persists after every batch (so another instance can resume it):

* ``scored_upto`` — the last source entry whose lines are accounted for (nothing at or before it is
  scored again);
* ``buffer`` / ``score`` / ``since`` — every line since the last wake, the running score
  (``scoring.py`` has the formula) and when the buffer's first line arrived;
* ``wakes`` / ``cost_usd`` / ``seq`` — the caps' counters and the wake sequence number that keys
  each wake's idempotent event id;
* ``pending`` — a wake already taken out of the buffer and saved, not yet confirmed by flows. It is
  saved BEFORE it is sent, so a crash between the two re-sends the same event id (one fact at the
  intake) instead of losing the wake or scoring its lines again.

A batch is a list of lines, each optionally tagged with how the source wants it handled:

* ``score`` (the default) — the brief's own rule triggers are checked first (``rules.py``); a match
  is treated as ``addressed``; otherwise the line goes to the model;
* ``addressed`` — the source's fixed rule tier fired (a chat: ``@vexa``, a reply to the agent, …):
  no model call, the line is recorded as ``addressed_to_vexa: 1.0`` (annotation model ``rule``), and
  the agent wakes at once;
* ``context`` — the agent's own words: never buffered, never scored, only context for what follows.

CAPS ARE CHECKED BEFORE EVERY MODEL CALL: when the source has used its wakes, or the call's estimated
cost would take it past the cost cap, the line is not sent to the model. It still rides in the buffer,
unscored, so the recap at the end has it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional

from . import rules as rules_mod
from . import scoring
from .brief import ADDRESSED, Brief, instructions
from .model import Line, ModelRefused, Scored, estimate_tokens

log = logging.getLogger("signals.core")

CONTEXT_LINES = 24
BUFFER_MAX = 500
#: Past the hour's wake cap, being addressed still wakes the agent — this many caps' worth more, then
#: nothing until the next hour: every wake is an agent turn, and a name said again is not free.
ADDRESSED_HEADROOM = 1
CONCURRENCY = 4
DEFAULT_USD_PER_MTOK = 2.0

Scorer = Callable[[dict, Line, list], Awaitable[Scored]]


@dataclass
class State:
    scored_upto: str = ""
    buffer: list = field(default_factory=list)
    score: float = 0.0
    since: Optional[float] = None
    wakes: int = 0
    cost_usd: float = 0.0
    seq: int = 0
    dropped: int = 0
    refusals: dict = field(default_factory=dict)
    pending: Optional[dict] = None            # a wake recorded but not yet confirmed delivered
    heard: list = field(default_factory=list)  # [speaker, start, end] of interims that woke the agent
    opened: Optional[float] = None            # when listening began: the wake cap is per HOUR from here

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "State":
        if not isinstance(d, dict):
            return cls()
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass(frozen=True)
class Caps:
    wakes: int
    cost_usd: float
    usd_per_mtok: float = DEFAULT_USD_PER_MTOK


def entry_key(entry_id: str) -> tuple:
    """A stream id (``<ms>-<seq>``) as a sortable pair; anything else sorts first."""
    ms, _, seq = str(entry_id or "").partition("-")
    try:
        return int(ms), int(seq or 0)
    except ValueError:
        return (0, 0)


def after(entry_id: str, upto: str) -> bool:
    return entry_key(entry_id) > entry_key(upto) if upto else True


async def _none():
    return None, None


class Listener:
    """One source's listening: score new lines, decide the wake. ``scorer`` is the model port."""

    def __init__(self, source: dict, *, scorer: Optional[Scorer], caps: Caps,
                 now: Callable[[], float] = time.time):
        self.source = dict(source)                       # {"kind": …, "id": …}
        self.scorer, self.caps, self.now = scorer, caps, now
        self.context: deque = deque(maxlen=CONTEXT_LINES)

    def seed_context(self, lines: list) -> None:
        for line in lines[-CONTEXT_LINES:]:
            self.context.append(line)

    def allowed_wakes(self, state: State) -> int:
        """The wake cap is a cost guard per HOUR of meeting (app.dev 2026-10-16: a 12-wake lifetime
        budget went in four minutes and the sensor was silent for the rest of the call)."""
        if state.opened is None:
            return self.caps.wakes
        return self.caps.wakes * (1 + int(max(0.0, self.now() - state.opened) // 3600))

    def _refuse(self, state: State, why: str) -> None:
        if why not in state.refusals:
            log.warning("source %s:%s — model call refused: %s (the line is buffered, unscored)",
                        self.source["kind"], self.source["id"], why)
        state.refusals[why] = state.refusals.get(why, 0) + 1

    async def take(self, state: State, lines: list, brief: Brief) -> tuple:
        """Score ``lines`` (one batch; each a ``Line`` or ``(Line, mode)``) into ``state``. Returns
        ``(annotations, batch_scores)``. Raises ``ModelUnavailable`` untouched: the caller retries the
        batch, and ``state`` is unchanged (scores are applied only after every call answered)."""
        asks = instructions(brief)
        plans, reserved = [], 0.0
        if state.opened is None:
            state.opened = self.now()
        context = list(self.context)
        for entry in lines:
            line, mode = entry if isinstance(entry, tuple) else (entry, "score")
            if mode == "context":
                plans.append((line, None, "context"))
                context.append(line)
                continue
            ctx = context[-CONTEXT_LINES:]
            context.append(line)
            if mode == "score" and brief.rules and await rules_mod.matches(
                    brief.rules, text=line.text, speaker=line.speaker):
                mode = "addressed"
            if mode == "addressed":
                plans.append((line, ctx, "rule"))
                continue
            why = None
            if self.scorer is None:
                why = "no_model"
            elif state.wakes >= self.allowed_wakes(state):
                why = "wakes_cap"
            else:
                est = estimate_tokens(asks, line, ctx) * self.caps.usd_per_mtok / 1e6
                if state.cost_usd + reserved + est > self.caps.cost_usd:
                    why = "cost_cap"
                else:
                    reserved += est
            plans.append((line, ctx, why))

        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(line, ctx, why):
            if why == "rule":
                return Scored({ADDRESSED: 1.0}, 0, "rule"), None
            if why:
                return None, why
            async with gate:
                try:
                    return await self.scorer(asks, line, ctx), None
                except ModelRefused as exc:
                    return None, f"model_refused: {exc}"

        results = await asyncio.gather(*(one(*p) if p[2] != "context" else _none() for p in plans))
        annotations, batch = [], []
        now = self.now()
        for (line, _ctx, plan), (scored, why) in zip(plans, results):
            if plan == "context":
                self.context.append(line)
                continue
            scores = {}
            if scored is not None:
                scores = dict(scored.scores)
                state.cost_usd += scored.tokens * self.caps.usd_per_mtok / 1e6
                annotations.append({"source": dict(self.source), "segment_id": line.segment_id,
                                    "scores": scores, "words": [], "model": scored.model,
                                    "brief_version": brief.version, "at": round(now, 3)})
                batch.append(scores)
            elif why:
                self._refuse(state, why.split(":")[0])
            state.buffer.append({"segment_id": line.segment_id, "speaker": line.speaker, "text": line.text,
                                 "start": float(line.start), "scores": scores})
            if state.since is None:
                state.since = now
            self.context.append(line)
        if len(state.buffer) > BUFFER_MAX:
            state.dropped += len(state.buffer) - BUFFER_MAX
            del state.buffer[: len(state.buffer) - BUFFER_MAX]
        state.score += scoring.batch_score(batch, brief.wake_labels)
        return annotations, batch

    def due(self, state: State, brief: Brief, batch: list) -> Optional[str]:
        """The wake reason now, or None. A source that has used its wakes is woken only when it is
        addressed (no model is involved: someone asked the agent) and at its end — and being
        addressed is capped too: past the cap it wakes at most the cap's number again
        (``ADDRESSED_HEADROOM`` × the cap), so a line that names Vexa, said over and over, is not an
        unbounded run of agent turns. Every wake counts (``woke``)."""
        allowed = self.allowed_wakes(state)
        if state.wakes >= allowed:
            if (state.buffer and scoring.addressed(batch)
                    and state.wakes < allowed * (1 + ADDRESSED_HEADROOM)):
                return "addressed"
            # ONE QUIET WAKE PAST THE CAP (by max_wait): agent-api refuses it and tells the room that
            # Vexa paused listening, instead of silence
            if (state.wakes == allowed and state.buffer and state.since is not None
                    and self.now() - state.since >= brief.max_wait_s):
                return "max_wait"
            return None
        return scoring.wake_reason(scores=batch, running=state.score, brief=brief,
                                   buffered=len(state.buffer), since=state.since, now=self.now())

    def wake_data(self, state: State, brief: Brief, reason: str, target: dict) -> dict:
        """signals.v1 `WakeData` for the buffer as it is."""
        return {"source": dict(self.source), **target, "buffer": list(state.buffer),
                "score": round(state.score, 4), "reason": reason, "brief_version": brief.version}

    @staticmethod
    def woke(state: State) -> None:
        """The wake was handed over: reset the buffer and count it."""
        state.buffer, state.score, state.since = [], 0.0, None
        state.wakes += 1
        state.seq += 1
