"""Browser sessions and verify checks — short-lived records in meeting-api's Redis.

A **browser session** is a ``meeting-bot`` workload in sign-in mode a person signs in through, live
in their page; a **check** is the same workload in verify mode, proving a saved bot account is
still signed in. Both are records, never rows:

    botsession:<id>   {session_id, user_id, owner, platform, display_name, account_id, workload_id,
                       host, status, detail, vnc_password, token_hash, expires_at}
    botcheck:<id>     {check_id, user_id, account_id, platform, workload_id, status, detail,
                       token_hash, expires_at}
    botsession:user:<user_id>   the one open session a person may hold (SET NX)
    botsessions:open  sorted set of ``session:<id>`` / ``check:<id>`` by expiry, the reaper's index
                      and the deployment-wide count of browser pods (``admit``: at most N at once)
    botsession:opens:<user_id>  sorted set of this person's opens by time (``rate_hit``: N per window)

Each record lives ``timeout + RECORD_MARGIN_S`` so the reaper still finds an expired one and stops its
workload. The workload's bearer is random and stored only as its SHA-256; it is compared with
``hmac.compare_digest``.

Status: ``starting`` → ``ready`` (the workload is running and its 6080 answers) → ``saved`` /
``failed`` / ``closed``. A check is ``checking`` → ``done`` / ``failed`` / ``closed``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import string
import time
from typing import Any, Optional, Protocol

SESSION_PREFIX = "botsession:"
CHECK_PREFIX = "botcheck:"
USER_PREFIX = "botsession:user:"
OPEN_INDEX = "botsessions:open"
RATE_PREFIX = "botsession:opens:"
#: How long a record outlives its own expiry, so the reaper can still stop its workload.
RECORD_MARGIN_S = 300
OPEN_STATUSES = ("starting", "ready")
SESSION_TERMINAL = ("saved", "failed", "closed")
CHECK_OPEN = ("checking",)

_VNC_ALPHABET = string.ascii_letters + string.digits


def new_id() -> str:
    return secrets.token_hex(16)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_vnc_password() -> str:
    """Eight characters: the RFB password length x11vnc honours (longer is silently truncated)."""
    return "".join(secrets.choice(_VNC_ALPHABET) for _ in range(8))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, stored_hash: Optional[str]) -> bool:
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(token_hash(token).encode(), str(stored_hash).encode())


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


class SessionStore(Protocol):
    async def save(self, kind: str, record_id: str, record: dict, *, ttl_s: int) -> None: ...
    async def load(self, kind: str, record_id: str) -> Optional[dict]: ...
    async def claim_user(self, user_id: int, session_id: str, *, ttl_s: int) -> Optional[str]: ...
    async def release_user(self, user_id: int, session_id: str) -> None: ...
    async def holder(self, user_id: int) -> Optional[str]: ...
    async def index(self, member: str, expires_at: float) -> None: ...
    async def expired(self, now: float) -> list[str]: ...
    async def unindex(self, member: str) -> None: ...
    async def admit(self, member: str, expires_at: float, *, cap: int) -> bool: ...
    async def rate_hit(self, user_id: int, now: float, *, window_s: int, limit: int) -> bool: ...


def _key(kind: str, record_id: str) -> str:
    return (SESSION_PREFIX if kind == "session" else CHECK_PREFIX) + record_id


class RedisSessionStore:
    """``SessionStore`` over a ``redis.asyncio`` client (``decode_responses`` either way)."""

    def __init__(self, redis: Any):
        self._r = redis

    async def save(self, kind, record_id, record, *, ttl_s):
        await self._r.set(_key(kind, record_id), json.dumps(record), ex=max(1, int(ttl_s)))

    async def load(self, kind, record_id):
        raw = _text(await self._r.get(_key(kind, record_id)))
        if not raw:
            return None
        try:
            out = json.loads(raw)
        except ValueError:
            return None
        return out if isinstance(out, dict) else None

    async def claim_user(self, user_id, session_id, *, ttl_s):
        key = f"{USER_PREFIX}{int(user_id)}"
        if await self._r.set(key, session_id, nx=True, ex=max(1, int(ttl_s))):
            return None
        return _text(await self._r.get(key))

    async def release_user(self, user_id, session_id):
        key = f"{USER_PREFIX}{int(user_id)}"
        if _text(await self._r.get(key)) == session_id:
            await self._r.delete(key)

    async def holder(self, user_id):
        return _text(await self._r.get(f"{USER_PREFIX}{int(user_id)}"))

    async def index(self, member, expires_at):
        await self._r.zadd(OPEN_INDEX, {member: float(expires_at)})

    async def expired(self, now):
        return [_text(m) for m in await self._r.zrangebyscore(OPEN_INDEX, "-inf", float(now))]

    async def unindex(self, member):
        await self._r.zrem(OPEN_INDEX, member)

    async def admit(self, member, expires_at, *, cap):
        """Index ``member`` only while the deployment holds fewer than ``cap`` open sessions and checks.
        Add first, then count: two replicas racing for the last place may both be refused, never both
        admitted, so the cap holds across every meeting-api replica sharing this Redis."""
        await self._r.zadd(OPEN_INDEX, {member: float(expires_at)})
        if int(await self._r.zcard(OPEN_INDEX)) > int(cap):
            await self._r.zrem(OPEN_INDEX, member)
            return False
        return True

    async def rate_hit(self, user_id, now, *, window_s, limit):
        """Count one open for ``user_id`` unless it already made ``limit`` in the last ``window_s``."""
        key = f"{RATE_PREFIX}{int(user_id)}"
        await self._r.zremrangebyscore(key, "-inf", float(now) - window_s)
        member = f"{float(now):.6f}:{secrets.token_hex(4)}"
        await self._r.zadd(key, {member: float(now)})
        await self._r.expire(key, max(1, int(window_s)))
        if int(await self._r.zcard(key)) > int(limit):
            await self._r.zrem(key, member)
            return False
        return True


class InMemorySessionStore:
    """``SessionStore`` in a dict — the app factory's default and a test double. TTLs are kept and
    honoured on read against ``clock``."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._kv: dict[str, tuple[float, str]] = {}
        self._index: dict[str, float] = {}
        self._opens: dict[int, list[float]] = {}

    def _get(self, key):
        hit = self._kv.get(key)
        if hit is None or hit[0] <= self._clock():
            self._kv.pop(key, None)
            return None
        return hit[1]

    async def save(self, kind, record_id, record, *, ttl_s):
        self._kv[_key(kind, record_id)] = (self._clock() + ttl_s, json.dumps(record))

    async def load(self, kind, record_id):
        raw = self._get(_key(kind, record_id))
        return json.loads(raw) if raw else None

    async def claim_user(self, user_id, session_id, *, ttl_s):
        key = f"{USER_PREFIX}{int(user_id)}"
        cur = self._get(key)
        if cur is not None:
            return cur
        self._kv[key] = (self._clock() + ttl_s, session_id)
        return None

    async def release_user(self, user_id, session_id):
        key = f"{USER_PREFIX}{int(user_id)}"
        if self._get(key) == session_id:
            self._kv.pop(key, None)

    async def holder(self, user_id):
        return self._get(f"{USER_PREFIX}{int(user_id)}")

    async def index(self, member, expires_at):
        self._index[member] = float(expires_at)

    async def expired(self, now):
        return [m for m, at in self._index.items() if at <= now]

    async def unindex(self, member):
        self._index.pop(member, None)

    async def admit(self, member, expires_at, *, cap):
        if member not in self._index and len(self._index) >= int(cap):
            return False
        self._index[member] = float(expires_at)
        return True

    async def rate_hit(self, user_id, now, *, window_s, limit):
        recent = [t for t in self._opens.get(int(user_id), []) if t > float(now) - window_s]
        if len(recent) >= int(limit):
            self._opens[int(user_id)] = recent
            return False
        self._opens[int(user_id)] = recent + [float(now)]
        return True
