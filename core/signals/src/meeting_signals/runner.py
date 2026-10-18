"""The runner: one asyncio process, one task per live source.

* **discover** — every source adapter names the sources with fresh activity; they go into the shared
  ``sig:live`` registry (so any instance can pick up a source another one dropped).
* **supervise** — every two seconds: each live source no task here holds is offered to this instance;
  the lease decides who takes it. The task reads the source's brief and the company policy (agent-api
  answers both); policy off, it lets go at once and the source is not offered again for 30 s.
* **listen** (``SourceTask``) — read the source from the saved position, score each batch, write the
  annotations and the state under the lease, wake when due, and acknowledge. At the source's end the
  remainder is handed over as an ``end`` wake and the task ends; after ``IDLE_S`` with no entries and
  an empty buffer it lets go.

Multi-instance: several instances share the consumer groups. A source is listened to by the one that
holds its lease; if that instance dies its lease lapses (30 s), another takes the source, claims the
dead reader's pending entries, skips everything at or before the saved position (and every segment
id already scored), and resends any wake that was saved but not confirmed — with the same event id.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Optional

from . import keys as keys_mod
from . import model as model_mod
from . import scoring
from .core import Caps, Listener, State, after, entry_key
from .model import ModelRefused, ModelUnavailable
from .store import LeaseLost, Store
from .wake import DELIVERED, FlowsEmitter

log = logging.getLogger("signals.runner")

FOLLOW_S = 600.0
IDLE_S = 600.0
RENEW_S = 10.0
SUPERVISE_S = 2.0
BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)
IDLE_TICK_S = 0.05
OFF_RECHECK_S = 30.0
HEARD_MAX = 50
HEARD_SLACK_S = 2.0


def _overlaps(line, heard: list) -> bool:
    """Is ``line`` the same speech as an interim that already woke the agent (same speaker,
    overlapping in time)? Its final, or a later draft under another segment id, wakes nothing."""
    for speaker, start, end in heard:
        if line.speaker == speaker and line.start <= end + HEARD_SLACK_S and line.end >= start - HEARD_SLACK_S:
            return True
    return False


class SourceTask:
    def __init__(self, runner: "Runner", source, sid: str, from_ms: int, meta: Optional[dict] = None):
        self.run_, self.src, self.sid, self.from_ms = runner, source, sid, from_ms
        self.meta = dict(meta or {})
        self.kind = source.kind
        self.listener: Optional[Listener] = None
        self.state = State()
        self.failures = 0

    async def _scorer(self, policy, tier: str = "cheapest"):
        """The model port for the policy as it is now, at the brief's tier (only a model the policy
        allows); None (lines buffered unscored) without a key."""
        m = policy.model_for(tier)
        try:
            key = await self.run_.keys.resolve(m.get("secret_ref", "none"))
        except keys_mod.KeyUnavailable as exc:
            self.run_.say(f"signal model has no key: {exc}")
            return None
        ref = m.get("secret_ref", "none")
        adapter = model_mod.build(m, key, transport=self.run_.model_transport)

        async def score(asks, line, ctx):
            # A KEY THE MODEL REFUSES (401/403) is dropped and read again ONCE: a key stored in the
            # broker after a dead one was cached (or after the dev fallback answered) takes effect
            # without a restart.
            nonlocal adapter
            try:
                return await adapter.score(asks, line, ctx)
            except ModelRefused as exc:
                if ref == "none" or str(exc) not in ("HTTP 401", "HTTP 403"):
                    raise
                self.run_.keys.forget(ref)
                try:
                    fresh = await self.run_.keys.resolve(ref)
                except keys_mod.KeyUnavailable:
                    raise exc from None
                adapter = model_mod.build(m, fresh, transport=self.run_.model_transport)
                return await adapter.score(asks, line, ctx)
        return score

    async def _turn_running(self, brief) -> bool:
        """Is a SIGNAL turn of the chat this source wakes still running? (positive evidence: the
        chat's head lease is held by a `-signal` unit). New lines then wait in the one pending
        buffer instead of each spending the cap and queueing another turn."""
        chat = self.src.wake_chat(self.sid, brief) if hasattr(self.src, "wake_chat") else ""
        if not chat:
            return False
        try:
            holder = await self.run_.redis.get(f"session:{chat}:lease")
        except Exception:  # noqa: BLE001 — unknown is not busy
            return False
        return bool(holder) and str(holder).endswith("-signal")

    async def _deliver_pending(self) -> bool:
        p = self.state.pending
        if not p:
            return True
        outcome = await self.run_.emitter.emit(self.src.event_type, p.get("subject", ""), p["data"])
        if outcome != DELIVERED:
            return False
        self.state.pending = None
        await self.run_.store.commit(self.kind, self.sid, self.run_.me, self.state, [], [])
        self.run_.wakes_sent += 1
        return True

    async def run(self) -> None:
        r, store, me = self.run_, self.run_.store, self.run_.me
        self.state = await store.load(self.kind, self.sid)
        renew_at = time.monotonic() + RENEW_S
        last_entry = time.monotonic()
        opened = False
        seeded = False
        try:
            while True:
                await asyncio.sleep(0)                              # never starve the other sources
                if time.monotonic() >= renew_at:
                    if not await store.renew(self.kind, self.sid, me):
                        log.info("%s:%s — lease lost, letting go", self.kind, self.sid)
                        return
                    renew_at = time.monotonic() + RENEW_S
                listening = await self.src.listening(self.sid, self.meta)
                policy, brief = listening.policy, listening.brief
                if not await self._deliver_pending():
                    await asyncio.sleep(BACKOFF_S[min(self.failures, len(BACKOFF_S) - 1)])
                    self.failures += 1
                    continue
                if not policy.active:
                    r.off_until[f"{self.kind}:{self.sid}"] = time.monotonic() + OFF_RECHECK_S
                    return
                if not opened:
                    opened = await self.src.open(self.sid, self.from_ms)
                    if not opened:
                        if time.monotonic() - last_entry > IDLE_S:
                            return
                        await asyncio.sleep(1.0)
                        continue
                caps = Caps(policy.wakes_cap, policy.cost_cap_usd, r.usd_per_mtok)
                if self.listener is None:
                    self.listener = Listener({"kind": self.kind, "id": self.sid}, scorer=None, caps=caps, now=r.clock)
                self.listener.caps = caps
                items = await self.src.read(self.sid, me, self.state.scored_upto)
                fresh = [i for i in items if after(i.entry_id, self.state.scored_upto)]
                if self.state.heard:
                    fresh = [dataclasses.replace(i, mode="context", interim=False)
                             if i.kind == "line" and _overlaps(i.line, self.state.heard) else i for i in fresh]
                if items:
                    last_entry = time.monotonic()
                lines, retracted, ended = [], set(), False
                candidates = [i.line.segment_id for i in fresh if i.kind == "line"]
                unseen = await store.unseen(self.kind, self.sid, candidates) if candidates else set()
                for it in fresh:
                    if it.kind == "line" and it.line.segment_id in unseen:
                        unseen.discard(it.line.segment_id)          # a duplicate inside one batch too
                        lines.append(it.line if it.mode == "score" else (it.line, it.mode))
                        if it.interim:
                            self.state.heard = (self.state.heard + [[it.line.speaker, it.line.start,
                                                                     it.line.end]])[-HEARD_MAX:]
                    elif it.kind == "retract":
                        retracted.update(it.ids)
                    elif it.kind == "end":
                        ended = True
                if retracted:
                    self.state.buffer = [b for b in self.state.buffer if b["segment_id"] not in retracted]
                if lines and not seeded:
                    first = min((i.entry_id for i in fresh if i.kind == "line"), key=entry_key)
                    self.listener.seed_context(await self.src.context(self.sid, first, 24))
                    seeded = True
                annotations, batch = [], []
                if lines:
                    self.listener.scorer = await self._scorer(policy, brief.model_tier)
                    try:
                        annotations, batch = await self.listener.take(self.state, lines, brief)
                    except ModelUnavailable as exc:
                        delay = BACKOFF_S[min(self.failures, len(BACKOFF_S) - 1)]
                        self.failures += 1
                        log.warning("%s:%s — signal model unavailable (%s); retrying in %ss",
                                    self.kind, self.sid, exc, delay)
                        await asyncio.sleep(delay)
                        continue                                    # nothing committed, nothing acked
                    self.failures = 0
                if fresh:
                    self.state.scored_upto = max((i.entry_id for i in fresh), key=entry_key)
                reason = "end" if ended else self.listener.due(self.state, brief, batch)
                if reason and reason != "end" and self.state.buffer and await self._turn_running(brief):
                    reason = None                                   # coalesce behind the running turn
                elif reason in ("threshold", "max_wait") and scoring.addressed(b.get("scores") for b in self.state.buffer):
                    reason = "addressed"                            # a coalesced buffer holds an address
                if reason and (self.state.buffer or reason == "end"):
                    if self.state.buffer:
                        data = self.listener.wake_data(self.state, brief, reason, self.src.wake_target(self.sid))
                        self.state.pending = {"id": f"signal:{self.kind}:{self.sid}:{self.state.seq + 1}",
                                              "subject": listening.subject, "data": data}
                        self.listener.woke(self.state)
                        log.info("%s:%s — wake %d (%s, %d lines)", self.kind, self.sid, self.state.seq,
                                 reason, len(data["buffer"]))
                if items or retracted or self.state.pending:
                    await store.commit(self.kind, self.sid, me, self.state, annotations,
                                       [(l[0] if isinstance(l, tuple) else l).segment_id for l in lines])
                    r.lines_scored += len(annotations)
                    await self.src.ack(self.sid, [i.entry_id for i in items])
                    await self._deliver_pending()
                else:
                    await asyncio.sleep(IDLE_TICK_S)                # the read already waited; yield anyway
                if ended:
                    if self.state.pending is None:
                        await store.forget(self.kind, self.sid)
                        return
                    continue                                        # keep trying the end wake
                if not items and not self.state.buffer and time.monotonic() - last_entry > IDLE_S:
                    return
        except LeaseLost:
            log.info("%s:%s — lease moved to another instance", self.kind, self.sid)
        finally:
            await store.release(self.kind, self.sid, me)


class Runner:
    def __init__(self, redis, sources: list, keys, emitter: FlowsEmitter, *, me: str,
                 usd_per_mtok: float, model_transport=None, clock=time.time):
        self.redis, self.sources = redis, {s.kind: s for s in sources}
        self.store = Store(redis, now=clock)
        self.keys, self.emitter = keys, emitter
        self.off_until: dict = {}
        self.me, self.usd_per_mtok, self.model_transport, self.clock = me, usd_per_mtok, model_transport, clock
        self.tasks: dict = {}
        self.lines_scored = 0
        self.wakes_sent = 0
        self._said: set = set()

    def say(self, line: str) -> None:
        if line not in self._said:
            self._said.add(line)
            log.warning("%s", line)

    async def discover_once(self) -> None:
        for src in self.sources.values():
            for act in await src.discover():
                await self.store.touch(src.kind, act.id, act.at_ms, act.meta)

    async def supervise_once(self) -> None:
        for key, t in list(self.tasks.items()):
            if t.done():
                self.tasks.pop(key)
                if not t.cancelled() and t.exception():
                    log.error("source %s stopped: %r", key, t.exception())
        now = time.monotonic()
        for kind, sid, from_ms, meta in await self.store.live(FOLLOW_S):
            key = f"{kind}:{sid}"
            src = self.sources.get(kind)
            if src is None or key in self.tasks or self.off_until.get(key, 0) > now:
                continue
            if await self.store.acquire(kind, sid, self.me):
                self.tasks[key] = asyncio.create_task(SourceTask(self, src, sid, from_ms, meta).run(), name=key)

    async def _loop(self, fn, every: float) -> None:
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a loop of the runner must never die on one bad turn
                log.exception("%s failed; continuing", fn.__name__)
                await asyncio.sleep(1.0)
            if every:
                await asyncio.sleep(every)

    async def run(self) -> None:
        await asyncio.gather(self._loop(self.discover_once, 0), self._loop(self.supervise_once, SUPERVISE_S))

    def health(self) -> dict:
        return {"status": "ok", "instance": self.me, "sources": sorted(self.tasks),
                "lines_scored": self.lines_scored, "wakes_sent": self.wakes_sent}
