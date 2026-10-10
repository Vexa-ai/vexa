"""delegation_refresh.py — a live unit's delegation token is replaced before it expires.

A worker's delegation token lives for the chat warm window plus one turn
(``Settings.delegation_ttl_sec``) and is revoked when its unit ends (``delegation_revocation``). A warm
unit can outlive one token: a conversation that keeps going, a background job. Without a refresh it
would lose its vexa MCP tools at the token's ``exp`` and keep running without them.

WHAT HAPPENS, every reaper sweep, for each unit the runtime still runs:

1. Once two thirds of the current token's life has passed (``REFRESH_AT``), agent-api mints a new
   token for the same person, regime, ceiling and target, with a new ``jti`` and a full lifetime.
   It re-mints from ITS OWN record of the current token (``delegation_revocation.CURRENT_PREFIX``,
   a key no worker can reach), verified with its own key, never from anything a worker can write.
2. The new token is recorded against the unit (so it is revoked when the unit ends) and published
   to ``shared.units.delegation_key(unit)``, which the worker reads before each turn
   (``worker.engine.DelegationRefresh``): the next turn's harness attaches with it.
3. The old token is superseded: the sweep revokes it ``REFRESH_OVERLAP_SEC`` later, so a turn that
   was already running with it can finish.

A unit the runtime no longer runs is never offered a refresh (``delegation_revocation.sweep``), and a
refreshed token is recorded as past the spawn grace, so it is revoked on the first sweep after its
unit ends.

:func:`publish` is also how a dispatch hands over its first token (``Dispatcher._record_delegation``).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from control_plane import delegation_revocation as dr
from shared import delegation
from shared.units import delegation_key

logger = logging.getLogger("agent_api.delegation_refresh")

#: The share of a token's life after which it is replaced.
REFRESH_AT = 2 / 3
#: How long a replaced token stays good, for a turn that started with it. Never longer than the
#: token's own remaining life.
REFRESH_OVERLAP_SEC = 300


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def publish(client, *, unit_id: str, token: str, exp: int, now: Optional[float] = None) -> None:
    """Make ``token`` the unit's current token: agent-api's record and the worker's copy, both
    expiring with it."""
    t = time.time() if now is None else now
    ttl = max(1, int(exp - t))
    client.set(dr.CURRENT_PREFIX + unit_id, token, ex=ttl)
    client.set(delegation_key(unit_id), token, ex=ttl)


def due(claims: dict, now: float) -> bool:
    """Has two thirds of this token's life passed?"""
    iat, exp = int(claims.get("iat") or 0), int(claims.get("exp") or 0)
    return exp > iat and now >= iat + (exp - iat) * REFRESH_AT


class Refresher:
    """``refresher(client, unit_id, now)`` for ``delegation_revocation.sweep``: replace the unit's
    token when it is due. ``ttl_sec`` is read on every refresh, so a changed setting applies to the
    next token."""

    def __init__(self, secret: str, ttl_sec: Callable[[], int], *,
                 overlap_sec: float = REFRESH_OVERLAP_SEC) -> None:
        if not secret:
            raise ValueError("a refresh needs the delegation key")
        self._secret = secret
        self._ttl = ttl_sec
        self._overlap = overlap_sec

    def __call__(self, client, unit_id: str, now: float) -> Optional[str]:
        """The new token's ``jti`` when one was minted, else None (nothing recorded, or not due)."""
        current = client.get(dr.CURRENT_PREFIX + unit_id)
        if not current:
            return None
        try:
            claims = delegation.verify_delegation(self._secret, _text(current), now=int(now))
        except delegation.DelegationError:
            return None  # expired or unreadable: the unit's next dispatch mints afresh
        if not due(claims, now):
            return None
        scope = claims.get("scope") or {}
        token = delegation.mint_delegation(
            self._secret, subject=str(claims["sub"]), regime=str(scope.get("regime")),
            workspaces=scope.get("workspaces"), target=str(claims.get("target") or ""),
            ttl_sec=int(self._ttl()), now=int(now))
        fresh = delegation.verify_delegation(self._secret, token, now=int(now))
        dr.record(client, unit_id=unit_id, jti=str(fresh["jti"]), exp=int(fresh["exp"]), now=now,
                  awaiting_spawn=False)
        publish(client, unit_id=unit_id, token=token, exp=int(fresh["exp"]), now=now)
        old_exp = int(claims["exp"])
        dr.supersede(client, unit_id, str(claims.get("jti") or ""),
                     revoke_at=min(now + self._overlap, old_exp))
        logger.info("delegation token of unit=%s refreshed (expires in %ds)", unit_id,
                    int(fresh["exp"] - now))
        return str(fresh["jti"])
