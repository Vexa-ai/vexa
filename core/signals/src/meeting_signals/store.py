"""signals' own redis keys — it is the ONLY writer of every one of them (P23).

| key | what |
|---|---|
| ``sig:{kind}:{id}`` (stream) | the annotations, one ``payload`` JSON per scored line (``sig:meeting:{row}``) |
| ``sig:state:{kind}:{id}`` | the listener's State (buffer, running score, position, caps' counters, pending wake) |
| ``sig:seen:{kind}:{id}`` (set) | segment ids already accounted for — a line is scored once |
| ``sig:lease:{kind}:{id}`` | which instance holds the source (``SET NX PX``, renewed) |
| ``sig:live`` (zset) + ``sig:live:from``, ``sig:live:meta`` (hashes) | sources with recent activity, since when, and what discovery learned (a meeting's owner; the first one named is kept) |

FENCING: every state and annotation write is one MULTI under ``WATCH`` of the lease, and only when
the lease still names this instance — an instance that lost its lease (paused, partitioned) can
write nothing, so a taken-over source is never scored twice.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .core import State

LEASE_MS = 30000
STATE_TTL_S = 2 * 86400
ANNOTATIONS_MAX = 5000
LIVE = "sig:live"
LIVE_FROM = "sig:live:from"
LIVE_META = "sig:live:meta"


class LeaseLost(RuntimeError):
    pass


def _k(kind: str, sid: str) -> str:
    return f"{kind}:{sid}"


class Store:
    def __init__(self, redis, *, now=time.time):
        self.r, self.now = redis, now

    @staticmethod
    def annotations_key(kind: str, sid: str) -> str:
        return f"sig:{kind}:{sid}"

    # ── activity registry ───────────────────────────────────────────────────────────────
    async def touch(self, kind: str, sid: str, at_ms: int, meta: Optional[dict] = None) -> None:
        member = _k(kind, sid)
        await self.r.zadd(LIVE, {member: int(at_ms)}, gt=True)
        await self.r.hsetnx(LIVE_FROM, member, int(at_ms))
        if meta:
            await self.r.hsetnx(LIVE_META, member, json.dumps(meta, separators=(",", ":")))

    async def live(self, follow_s: float) -> list:
        """``[(kind, id, from_ms, meta)]`` active within ``follow_s``; older entries are dropped."""
        cutoff = int((self.now() - follow_s) * 1000)
        stale = await self.r.zrangebyscore(LIVE, "-inf", f"({cutoff}")
        if stale:
            await self.r.zrem(LIVE, *stale)
            await self.r.hdel(LIVE_FROM, *stale)
            await self.r.hdel(LIVE_META, *stale)
        out = []
        for member in await self.r.zrangebyscore(LIVE, cutoff, "+inf"):
            kind, _, sid = str(member).partition(":")
            frm = await self.r.hget(LIVE_FROM, member)
            try:
                meta = json.loads(await self.r.hget(LIVE_META, member) or "{}")
            except ValueError:
                meta = {}
            out.append((kind, sid, int(frm or 0), meta if isinstance(meta, dict) else {}))
        return out

    async def last_activity_ms(self, kind: str, sid: str) -> int:
        v = await self.r.zscore(LIVE, _k(kind, sid))
        return int(v or 0)

    async def forget(self, kind: str, sid: str) -> None:
        await self.r.zrem(LIVE, _k(kind, sid))
        await self.r.hdel(LIVE_FROM, _k(kind, sid))
        await self.r.hdel(LIVE_META, _k(kind, sid))

    # ── lease ───────────────────────────────────────────────────────────────────────────
    def lease_key(self, kind: str, sid: str) -> str:
        return f"sig:lease:{kind}:{sid}"

    async def acquire(self, kind: str, sid: str, me: str) -> bool:
        return bool(await self.r.set(self.lease_key(kind, sid), me, nx=True, px=LEASE_MS))

    async def _if_mine(self, kind: str, sid: str, me: str, act) -> bool:
        key = self.lease_key(kind, sid)
        async with self.r.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                if await pipe.get(key) != me:
                    return False
                pipe.multi()
                act(pipe, key)
                await pipe.execute()
                return True
            except Exception as exc:  # noqa: BLE001 — WatchError: the lease moved under us
                if type(exc).__name__ == "WatchError":
                    return False
                raise

    async def renew(self, kind: str, sid: str, me: str) -> bool:
        return await self._if_mine(kind, sid, me, lambda p, k: p.pexpire(k, LEASE_MS))

    async def release(self, kind: str, sid: str, me: str) -> bool:
        return await self._if_mine(kind, sid, me, lambda p, k: p.delete(k))

    # ── state ───────────────────────────────────────────────────────────────────────────
    async def load(self, kind: str, sid: str) -> State:
        raw = await self.r.get(f"sig:state:{kind}:{sid}")
        try:
            return State.from_dict(json.loads(raw)) if raw else State()
        except ValueError:
            return State()

    async def unseen(self, kind: str, sid: str, segment_ids: list) -> set:
        key = f"sig:seen:{kind}:{sid}"
        out = set()
        for s in segment_ids:
            if not await self.r.sismember(key, s):
                out.add(s)
        return out

    async def commit(self, kind: str, sid: str, me: str, state: State, annotations: list, seen: list) -> None:
        """Annotations + state + seen ids, atomically, only while the lease is ours."""
        def act(pipe, _lease):
            stream = self.annotations_key(kind, sid)
            for a in annotations:
                pipe.xadd(stream, {"payload": json.dumps(a, separators=(",", ":"))},
                          maxlen=ANNOTATIONS_MAX, approximate=True)
            pipe.set(f"sig:state:{kind}:{sid}", json.dumps(state.to_dict()), ex=STATE_TTL_S)
            if seen:
                pipe.sadd(f"sig:seen:{kind}:{sid}", *seen)
                pipe.expire(f"sig:seen:{kind}:{sid}", STATE_TTL_S)
            pipe.pexpire(_lease, LEASE_MS)
        if not await self._if_mine(kind, sid, me, act):
            raise LeaseLost(f"{kind}:{sid}")
