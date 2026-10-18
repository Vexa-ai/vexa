"""delegation_refresh.py — a live unit's delegation token is replaced before it expires.

A worker's delegation token lives for the chat warm window plus one turn
(``Settings.delegation_ttl_sec``) and is revoked when its unit ends (``delegation_revocation``). A warm
unit can outlive one token: a conversation that keeps going, a background job. Without a refresh it
would lose its vexa MCP tools at the token's ``exp``.

WHAT HAPPENS, every reaper sweep, for each unit the runtime still runs:

1. Once half of the current token's life has passed (``REFRESH_AT``), agent-api mints a new token
   for the same person, regime, ceiling and target, with a new ``jti`` and a full lifetime. It
   re-mints from ITS OWN record of the current token (``delegation_revocation.CURRENT_PREFIX``, a key
   no worker can reach), verified with its own key, never from anything a worker can write.
2. The new token is recorded against the unit (so it is revoked when the unit ends) and published
   to ``shared.units.delegation_key(unit)``, which the worker reads before each turn
   (``worker.engine.DelegationRefresh``): the next turn's harness attaches with it.
3. The replaced token is left alone. A turn already running holds it in its harness, and it keeps it
   until its own ``exp`` — half a lifetime after the refresh, 900 s at the 1800 s default — or until
   the unit ends, when it is revoked with the rest. A turn that outlives even that fails loud: its
   refused tool call ends the turn with a typed fault (``worker.tool_access``).

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

#: The share of a token's life after which it is replaced: the other half is the headroom a turn
#: that started with it has before its own ``exp``.
REFRESH_AT = 1 / 2


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def publish(client, *, unit_id: str, token: str, exp: int, now: Optional[float] = None) -> None:
    """Make ``token`` the unit's current token: agent-api's record and the worker's copy, both
    expiring with it."""
    t = time.time() if now is None else now
    ttl = max(1, int(exp - t))
    client.set(dr.CURRENT_PREFIX + unit_id, token, ex=ttl)
    client.set(delegation_key(unit_id), token, ex=ttl)


def withdraw(client, *, unit_id: str) -> None:
    """Remove the unit's current token: agent-api's record and the worker's copy. The worker keeps
    the token it holds; nothing is refreshed until a token is published again."""
    client.delete(dr.CURRENT_PREFIX + unit_id, delegation_key(unit_id))


def same_authority(client, secret: str, *, unit_id: str, claims: dict,
                   now: Optional[float] = None) -> bool:
    """May a token with ``claims`` become ``unit_id``'s current token? Yes when the unit holds no live
    current token, or holds one for the same person. The same person's newer token replaces it —
    a narrowed ceiling reaches the running worker at its next turn."""
    current = client.get(dr.CURRENT_PREFIX + unit_id)
    if not current:
        return True
    t = time.time() if now is None else now
    try:
        held = delegation.verify_delegation(secret, _text(current), now=int(t))
    except delegation.DelegationError:
        return True  # expired or unreadable: nothing live to protect
    return str(held.get("sub")) == str(claims.get("sub"))


def due(claims: dict, now: float) -> bool:
    """Has half of this token's life passed?"""
    iat, exp = int(claims.get("iat") or 0), int(claims.get("exp") or 0)
    return exp > iat and now >= iat + (exp - iat) * REFRESH_AT


class Refresher:
    """``refresher(client, unit_id, now)`` for ``delegation_revocation.sweep``: replace the unit's
    token when it is due. ``ttl_sec`` is read on every refresh, so a changed setting applies to the
    next token."""

    def __init__(self, secret: str, ttl_sec: Callable[[], int]) -> None:
        if not secret:
            raise ValueError("a refresh needs the delegation key")
        self._secret = secret
        self._ttl = ttl_sec

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
        logger.info("delegation token of unit=%s refreshed (expires in %ds)", unit_id,
                    int(fresh["exp"] - now))
        return str(fresh["jti"])
