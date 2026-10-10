"""delegation_revocation.py — has agent-api revoked this worker's delegation token?

A delegation token (``vxd_``) verifies on its own — signature, audience, expiry — and would stay good
until its ``exp``. agent-api revokes it earlier, when the unit it was minted for ends, by writing one
key per token to the service Redis with the token's remaining lifetime as its expiry
(``core/agent/control_plane/delegation_revocation.py``):

    vexa:delegation:revoked:<jti>   "1"

``/internal/validate`` asks :func:`is_revoked` for a token whose signature has already verified, and
refuses it when the key exists. ``REVOKED_PREFIX`` is held equal on both sides by gate:fact-parity
(fact ``delegation-revocation-key``).

FAILS CLOSED. A store that is unset, unreachable or erroring raises :class:`Unavailable`, which the
caller turns into a refusal of the DELEGATION TOKEN — never into "not revoked", which would let every
token a dead unit held back in. An API key never reaches this module: a Redis outage costs a worker its
toolbelt and costs a person nothing.

The client is one connection pool per process, made on first use from ``REDIS_URL``.
"""
from __future__ import annotations

import os
from typing import Any, Optional

#: The key agent-api writes for a revoked token: ``REVOKED_PREFIX + jti``.
REVOKED_PREFIX = "vexa:delegation:revoked:"
#: Where the store is when ``REDIS_URL`` is unset (config.v1 `REDIS_URL`).
DEFAULT_REDIS_URL = "redis://redis:6379/0"
#: A Redis that does not answer within this many seconds is an unavailable store.
TIMEOUT_SEC = 2.0

_client: Optional[Any] = None


class Unavailable(RuntimeError):
    """The revocation store could not be read; a delegation token must be refused."""


def _redis():
    global _client
    if _client is None:
        import redis.asyncio as aioredis

        _client = aioredis.from_url(os.environ.get("REDIS_URL") or DEFAULT_REDIS_URL,
                                    socket_connect_timeout=TIMEOUT_SEC, socket_timeout=TIMEOUT_SEC)
    return _client


async def is_revoked(jti: str) -> bool:
    """True when agent-api revoked the token ``jti``. Raises :class:`Unavailable` when that cannot
    be known."""
    try:
        return bool(await _redis().exists(REVOKED_PREFIX + jti))
    except Exception as e:  # noqa: BLE001 — every failure means "cannot know", which refuses
        raise Unavailable(type(e).__name__) from e
