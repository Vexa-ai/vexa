"""delegation_revocation.py — a worker's delegation token ends with the unit that holds it.

agent-api mints each dispatch a delegation token (``shared.delegation``) that the worker presents to
the MCP edge. Identity verifies it statelessly — signature, audience, expiry — so on its own a token
stays good until its ``exp``, whatever became of the worker it was handed to. This module is the
other half: the tokens a unit was handed are REVOKED once the unit is no longer running, in a store
identity reads for every ``vxd_`` bearer it answers.

THE STORE IS REDIS, one key per revoked token, living exactly as long as the token would have:

    vexa:delegation:revoked:<jti>   "1"  EX = the token's remaining lifetime

admin-api (``admin_api/app/delegation_revocation.py``) answers ``EXISTS`` on that key before it
answers for the token, and refuses the token when the store cannot be read. The key outlives an
admin-api restart, and the store never holds more than the tokens still alive. ``REVOKED_PREFIX`` is
held equal on both sides by gate:fact-parity (fact ``delegation-revocation-key``).

WHAT AGENT-API KEEPS so that it knows which tokens a unit holds:

    vexa:delegation:units                 the unit ids with a token recorded
    vexa:delegation:unit:<unit id>        hash  jti -> "<exp>|<minted at>", expiring with its last token

* :func:`record` — at dispatch, before the spawn: the token's ``jti`` against its unit. A token that
  cannot be recorded could never be revoked, so the dispatcher withholds it (``Dispatcher``).
* :func:`sweep` — THE UNIT-END HOOK. The recorded units are read FIRST, then the runtime's live set;
  every recorded unit the runtime no longer reports starting or running has ended — its turn
  completed, it idled out, it was stopped, it crashed, its start failed: the runtime does not say
  which, and every one of them ends the token — and its tokens are revoked.
* :func:`revoke_unit` — revoke what one unit holds. Also called at a new dispatch when the runtime
  reports the unit's previous incarnation ended, because a unit id is REUSED (a chat thread keeps
  its id across warm windows): once the next incarnation is running, the sweep sees the id as live
  and would leave the old incarnation's token alone until the id ended again.
* :func:`start_reaper` — the sweep, in a daemon thread. For a unit still live it also runs the
  refresher it is given (``delegation_refresh``), so a warm unit's token is replaced before it
  expires; a unit the runtime no longer runs is never refreshed. A token a refresh replaced is NOT
  revoked while its unit runs — a turn that started with it keeps it until its own ``exp`` — and is
  revoked with the rest of the unit's tokens when the unit ends.

A token minted less than ``GRACE_SEC`` ago is never revoked: its spawn may not have reached the
runtime yet, so "not live" says nothing about it. A sweep that cannot read the runtime's workloads
revokes nothing — a runtime blip must not cut a live worker's toolbelt.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable, Optional

from shared.units import delegation_key

logger = logging.getLogger("agent_api.delegation_revocation")

#: The key identity checks: ``REVOKED_PREFIX + jti`` (gate:fact-parity, ``delegation-revocation-key``).
REVOKED_PREFIX = "vexa:delegation:revoked:"
UNITS_KEY = "vexa:delegation:units"
UNIT_PREFIX = "vexa:delegation:unit:"
#: The token a live unit currently holds, as agent-api minted it — agent-api's own record, the one a
#: refresh re-mints from. The worker reads its copy at ``shared.units.delegation_key`` instead, a
#: key its Redis user may read and not write (``workload_redis``).
CURRENT_PREFIX = "vexa:delegation:current:"
#: A token younger than this is never revoked: its dispatch may not have reached the runtime yet.
GRACE_SEC = 120
#: How often the reaper compares the recorded units with the runtime's live set.
REAP_INTERVAL_SEC = 30.0
#: The runtime.v1 states in which a unit's container is gone or going.
ENDED_STATES = frozenset({"stopping", "stopped", "destroyed"})


class RevocationError(RuntimeError):
    """The store could not be written: a token recorded nowhere can never be revoked."""


def revoked_key(jti: str) -> str:
    return REVOKED_PREFIX + jti


def unit_key(unit_id: str) -> str:
    return UNIT_PREFIX + unit_id


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _entry(value) -> "tuple[int, float]":
    """``"<exp>|<minted at>"`` → ``(exp, minted_at)``; an unreadable entry reads as expired."""
    exp, _, minted = _text(value).partition("|")
    try:
        return int(exp), float(minted)
    except ValueError:
        return 0, 0.0


def record(client, *, unit_id: str, jti: str, exp: int, now: Optional[float] = None,
           awaiting_spawn: bool = True) -> None:
    """Remember that ``unit_id`` holds the token ``jti`` (expiring at ``exp``).
    Raises :class:`RevocationError` when the store cannot be written.

    ``awaiting_spawn=False`` is a token minted for a unit already running (a refresh): no spawn is
    on its way, so it is recorded as past the grace and is revoked as soon as the unit is gone."""
    t = time.time() if now is None else now
    if not jti:
        raise RevocationError("a delegation token without a jti cannot be revoked")
    minted = t if awaiting_spawn else t - GRACE_SEC
    try:
        client.hset(unit_key(unit_id), jti, f"{int(exp)}|{minted}")
        # The record outlives every token in it and no longer: it is only ever extended, so a token
        # minted under a shorter lifetime never cuts an earlier one's record short.
        remaining = max(1, int(exp - t))
        current = client.ttl(unit_key(unit_id))
        if current is None or current < remaining:
            client.expire(unit_key(unit_id), remaining)
        client.sadd(UNITS_KEY, unit_id)
    except Exception as e:  # noqa: BLE001 — any failure leaves the token unrevocable
        raise RevocationError(f"the delegation token could not be recorded: {type(e).__name__}") from e


def revoke(client, *, jti: str, exp: int, now: Optional[float] = None) -> bool:
    """Revoke one token for the rest of its life. False when it has already expired."""
    t = time.time() if now is None else now
    remaining = int(exp - t)
    if remaining <= 0:
        return False
    client.set(revoked_key(jti), "1", ex=remaining)
    return True


def revoke_unit(client, unit_id: str, *, now: Optional[float] = None,
                grace_sec: float = GRACE_SEC) -> int:
    """Revoke every token ``unit_id`` was handed at least ``grace_sec`` ago; forget expired ones.
    Returns how many were revoked. The revocation is written BEFORE the record is dropped, so a
    failure part-way leaves the record for the next sweep."""
    t = time.time() if now is None else now
    key = unit_key(unit_id)
    revoked = 0
    for jti, value in (client.hgetall(key) or {}).items():
        jti = _text(jti)
        exp, minted = _entry(value)
        if exp > t and t - minted < grace_sec:
            continue  # its spawn may still be on its way to the runtime
        if revoke(client, jti=jti, exp=exp, now=t):
            revoked += 1
        client.hdel(key, jti)
    _forget_if_empty(client, unit_id)
    return revoked


def prune(client, unit_id: str, *, now: Optional[float] = None) -> None:
    """Drop a live unit's expired tokens — there is nothing left to revoke on them."""
    t = time.time() if now is None else now
    key = unit_key(unit_id)
    for jti, value in (client.hgetall(key) or {}).items():
        if _entry(value)[0] <= t:
            client.hdel(key, _text(jti))
    _forget_if_empty(client, unit_id)


def _forget_if_empty(client, unit_id: str) -> None:
    """A unit that holds no token any more is forgotten, and so is the token published for it
    (``delegation_refresh.publish``): nothing it names is still good."""
    if not client.hlen(unit_key(unit_id)):
        client.srem(UNITS_KEY, unit_id)
        client.delete(CURRENT_PREFIX + unit_id, delegation_key(unit_id))


def has_revocable(client, unit_id: str, *, now: Optional[float] = None,
                  grace_sec: float = GRACE_SEC) -> bool:
    """Does ``unit_id`` hold a live token older than the grace? (What a dispatch asks before it
    spends a runtime call on whether the unit's previous incarnation ended.)"""
    t = time.time() if now is None else now
    for value in (client.hgetall(unit_key(unit_id)) or {}).values():
        exp, minted = _entry(value)
        if exp > t and t - minted >= grace_sec:
            return True
    return False


#: ``refresher(client, unit_id, now)`` — replaces a live unit's token when it is due
#: (``delegation_refresh.Refresher``). Called only for units the runtime still runs.
Refresher = Callable[[object, str, float], object]


def sweep(client, live_units: Callable[[], Iterable[str]], *, now: Optional[float] = None,
          grace_sec: float = GRACE_SEC, refresher: Optional[Refresher] = None) -> int:
    """Revoke the tokens of every recorded unit the runtime no longer runs. Returns how many. A live
    unit is offered to ``refresher`` instead; a unit the runtime does not run never is.

    The recorded units are read BEFORE the live set, so a unit recorded after this sweep began is
    not in the snapshot; one recorded just before it is protected by the grace."""
    units = [_text(u) for u in (client.smembers(UNITS_KEY) or set())]
    if not units:
        return 0
    live = set(live_units())
    t = time.time() if now is None else now
    revoked = 0
    for unit in units:
        if unit in live:
            prune(client, unit, now=t)
            if refresher is not None:
                try:
                    refresher(client, unit, t)
                except Exception:  # noqa: BLE001 — one unit's refresh never stops the sweep
                    logger.warning("delegation refresh failed for unit=%s; retried next sweep", unit,
                                   exc_info=True)
        else:
            revoked += revoke_unit(client, unit, now=t, grace_sec=grace_sec)
    return revoked


def start_reaper(*, client_factory: Callable[[], object], live_units: Callable[[], Iterable[str]],
                 interval_sec: float = REAP_INTERVAL_SEC,
                 refresher: Optional[Refresher] = None) -> Optional[threading.Event]:
    """Run :func:`sweep` every ``interval_sec`` in a daemon thread. Returns the stop event."""
    if interval_sec <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_sec):
            try:
                revoked = sweep(client_factory(), live_units, refresher=refresher)
                if revoked:
                    logger.info("delegation reaper revoked %d token(s) of ended units", revoked)
            except Exception:  # noqa: BLE001 — the next tick tries again; nothing was dropped
                logger.warning("delegation reaper sweep failed; the records are kept for the next one",
                               exc_info=True)

    threading.Thread(target=loop, name="delegation-reaper", daemon=True).start()
    return stop
