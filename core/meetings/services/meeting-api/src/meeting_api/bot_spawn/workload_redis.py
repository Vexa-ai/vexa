"""workload_redis.py — the Redis user a meeting bot connects as.

A bot talks to Redis for three things: it appends its transcript to ``transcription_segments``,
publishes live updates on ``tc:meeting:<id>:mutable`` and listens for commands on
``bot_commands:meeting:<id>``. Each spawn defines a Redis user for exactly that — its own meeting's
two channels, the one stream, append only — and the invocation carries that user's URL instead of the
service connection meeting-api uses. No other key, channel or command is in a bot's reach; the user is
removed when the session reaches a terminal state, and any left behind is removed once it is older
than the MeetingToken it was minted beside. Redis keeps no user across a restart, so :meth:`restore`
defines again every user still in the index; the password is derived from the MeetingToken key and
the session, so a live bot's credential is valid again without telling it anything.

``REDIS_WORKLOAD_ACL=shared`` hands bots the service connection instead — a deployment's explicit
choice for a Redis that cannot define users, safe only where every person on the instance trusts
every other.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Callable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

USER_PREFIX = "vexa-bot-"
#: Hash of the users this service defined: user name → "<connection id>|<meeting id>|<epoch seconds>".
INDEX_KEY = "vexa:acl:bots"
_LABEL = b"vexa-bot-redis.v1"
MODE_PER_WORKLOAD = "per-workload"
MODE_SHARED = "shared"
TRANSCRIPT_STREAM = "transcription_segments"
#: The commands a bot sends. Connection-level ones (AUTH, and CLIENT SETINFO which the client
#: tolerates being refused) need no grant.
BOT_COMMANDS = ("xadd", "publish", "subscribe", "unsubscribe", "ping", "quit")


class BotRedisError(RuntimeError):
    """The bot's Redis user could not be defined — the spawn must not proceed."""


def user_for(connection_id: str) -> str:
    return USER_PREFIX + "".join(c for c in connection_id if c.isalnum() or c == "-")


def password_for(secret: str, connection_id: str) -> str:
    if not secret:
        raise BotRedisError("no MeetingToken key configured: a bot's Redis password cannot be derived")
    key = hmac.new(secret.encode("utf-8"), _LABEL, hashlib.sha256).digest()
    return hmac.new(key, connection_id.encode("utf-8"), hashlib.sha256).hexdigest()


def acl_rules(meeting_id: int, password: str) -> list[str]:
    """The ``ACL SETUSER`` rules for one bot session, from a clean slate."""
    mid = int(meeting_id)
    return ["reset", "on", f">{password}", f"~{TRANSCRIPT_STREAM}", "resetchannels",
            f"&tc:meeting:{mid}:mutable", f"&bot_commands:meeting:{mid}", "-@all",
            *[f"+{c}" for c in BOT_COMMANDS]]


def bot_url(service_url: str, user: str, password: str) -> str:
    """``service_url`` with its credentials replaced by the bot's."""
    parts = urlsplit(service_url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class BotRedisUsers:
    """Defines and removes bot users over meeting-api's own (async) Redis connection."""

    def __init__(self, client, service_url: str, *, secret: str, max_age_sec: float,
                 now: Optional[Callable[[], float]] = None) -> None:
        self._r = client
        self._url = service_url
        self._secret = secret
        self._run_id: Optional[str] = None
        self._max_age = max_age_sec
        self._now = now or time.time

    async def grant(self, connection_id: str, meeting_id: int) -> str:
        """Define the session's bot user and return the URL its bot connects with. Users older than
        ``max_age_sec`` are removed on the way. Raises :class:`BotRedisError`."""
        user = user_for(connection_id)
        try:
            password = password_for(self._secret, connection_id)
            await self._expire_stale()
            await self._r.execute_command("ACL", "SETUSER", user, *acl_rules(meeting_id, password))
            await self._r.hset(INDEX_KEY, user, f"{connection_id}|{int(meeting_id)}|{int(self._now())}")
        except Exception as e:  # noqa: BLE001 — any failure leaves the bot without a credential
            raise BotRedisError(f"the bot's Redis user could not be defined: {type(e).__name__}") from e
        return bot_url(self._url, user, password)

    async def revoke(self, connection_id: str) -> None:
        """Remove the session's bot user. Best-effort: the age sweep removes what this misses."""
        user = user_for(connection_id)
        await self._r.execute_command("ACL", "DELUSER", user)
        await self._r.hdel(INDEX_KEY, user)

    async def restore(self) -> int:
        """Define again every indexed user Redis no longer has (it keeps none across a restart).
        Returns how many were restored."""
        restored = 0
        for user, (connection_id, meeting_id, _granted) in (await self._index()).items():
            if await self._r.execute_command("ACL", "GETUSER", user):
                continue
            await self._r.execute_command(
                "ACL", "SETUSER", user, *acl_rules(meeting_id, password_for(self._secret, connection_id)))
            restored += 1
        return restored

    async def restore_if_restarted(self) -> int:
        """:meth:`restore` the moment Redis is a new process (its ``run_id`` changed) — a live bot's
        subscription is down until its user exists again, so this runs on a short tick and costs one
        ``INFO`` when nothing happened."""
        info = await self._r.info("server")
        run_id = (info or {}).get("run_id")
        if run_id == self._run_id:
            return 0
        restored = await self.restore()
        self._run_id = run_id
        return restored

    async def _index(self) -> dict[str, tuple[str, int, float]]:
        out: dict[str, tuple[str, int, float]] = {}
        for user, value in (await self._r.hgetall(INDEX_KEY) or {}).items():
            user = user.decode() if isinstance(user, bytes) else user
            value = value.decode() if isinstance(value, bytes) else str(value)
            parts = value.split("|")
            try:
                out[user] = (parts[0], int(parts[1]), float(parts[2]))
            except (IndexError, ValueError):
                out[user] = (parts[0], 0, 0.0)
        return out

    async def _expire_stale(self) -> None:
        current = self._now()
        for user, (_conn, _mid, granted) in (await self._index()).items():
            if current - granted > self._max_age:
                await self._r.execute_command("ACL", "DELUSER", user)
                await self._r.hdel(INDEX_KEY, user)
