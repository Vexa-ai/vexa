"""workload_redis.py — the Redis user an agent worker connects as.

A worker talks to Redis for exactly four keys of its own unit: it reads ``unit:<id>:in``, appends to
``unit:<id>:out``, records how far it has read in ``unit:<id>:cursor``, and reads its current
delegation token at ``unit:<id>:delegation`` (agent-api replaces it before it expires,
``delegation_refresh``). It is given a Redis user that can do that and nothing else — no other key,
no pub/sub channel, no admin command, and only read access to its own input stream (agent-api is that
stream's one writer) and to its delegation key (agent-api publishes it) — instead of the service
connection agent-api itself uses. Every other unit's streams, every meeting's transcript
feed and every service key stay out of its reach, whatever runs inside the worker.

* :func:`grant` — before a dispatch spawns, (re)defines the unit's user (``ACL SETUSER`` with
  ``reset`` first, so it is idempotent) and returns the URL the worker connects with. The password is
  derived from the internal secret and the unit id, so a respawn or a touch of a live unit leaves a
  running worker's credential valid.
* :func:`sweep` — removes the users of units no longer starting or running on the runtime.
* :func:`restore` — Redis keeps no user across a restart, so every indexed user it no longer has is
  defined again (the password is derived, so a live worker's URL is valid again unchanged).

``REDIS_WORKLOAD_ACL=shared`` hands workers the service connection instead. That is a deployment's
explicit choice for a Redis that cannot define users (some managed services); every worker can then
read and write every unit's streams, so it is safe only when every person on the instance trusts
every other.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from typing import Callable, Iterable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from shared.units import delegation_key, inbox_cursor_key, input_topic, output_topic

logger = logging.getLogger("agent_api.workload_redis")

USER_PREFIX = "vexa-unit-"
#: Hash of the users this service defined: user name → "<unit id>|<epoch seconds of the grant>".
INDEX_KEY = "vexa:acl:units"
MODE_PER_WORKLOAD = "per-workload"
MODE_SHARED = "shared"
_LABEL = b"vexa-workload-redis.v1"
#: The commands a worker sends. Connection-level ones (AUTH, and CLIENT SETINFO which redis-py
#: tolerates being refused) need no grant.
WORKER_COMMANDS = ("xadd", "xread", "xrange", "xrevrange", "set", "get", "ping")
#: A user younger than this is never swept: its dispatch may not have reached the runtime yet.
SWEEP_GRACE_SEC = 15 * 60


class WorkloadRedisError(RuntimeError):
    """The worker's Redis user could not be defined — the dispatch must not proceed."""


def user_for(unit_id: str) -> str:
    """The unit's Redis user name: a digest, because a unit id carries the chat's session string."""
    return USER_PREFIX + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:32]


def password_for(secret: str, unit_id: str) -> str:
    if not secret:
        raise WorkloadRedisError("no internal secret configured: a worker's Redis password cannot be derived")
    key = hmac.new(secret.encode("utf-8"), _LABEL, hashlib.sha256).digest()
    return hmac.new(key, unit_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _glob_literal(text: str) -> str:
    """``text`` as an ACL key pattern that matches exactly itself."""
    return "".join("\\" + c if c in "*?[]\\" else c for c in text)


def unit_keys(unit_id: str) -> tuple[str, str, str]:
    return input_topic(unit_id), output_topic(unit_id), inbox_cursor_key(unit_id)


def acl_rules(unit_id: str, password: str, *, read_only_input: bool = True) -> list[str]:
    """The ``ACL SETUSER`` rules for one unit's worker, from a clean slate. The input stream and the
    delegation key are read-only to it (``%R~``, a Redis 7 / Valkey key permission): agent-api writes
    both. ``read_only_input=False`` is the form for a server without key permissions, where the
    pattern grants read and write (the refresh re-mints from agent-api's own record, never from the
    worker's copy, so a worker writing its copy changes nothing but its own attachment)."""
    inp, out, cursor = unit_keys(unit_id)
    read_only = "%R~" if read_only_input else "~"
    return (["reset", "on", f">{password}"]
            + [read_only + _glob_literal(inp)]
            + [f"~{_glob_literal(k)}" for k in (out, cursor)]
            + [read_only + _glob_literal(delegation_key(unit_id))]
            + ["resetchannels", "-@all"]
            + [f"+{c}" for c in WORKER_COMMANDS])


def _set_user(client, user: str, unit_id: str, password: str) -> None:
    """``ACL SETUSER`` with the read-only input stream; on a server that does not know key
    permissions (Redis before 7, which names the ``%R~`` rule it refuses), the read-write form,
    said loudly — the worker's user can then also append to its own input stream."""
    try:
        client.execute_command("ACL", "SETUSER", user, *acl_rules(unit_id, password))
    except Exception as e:  # noqa: BLE001 — classified below; anything else is the caller's error
        if "%R~" not in str(e):
            raise
        logger.warning("this Redis has no read-only key permissions (Redis 7+ / Valkey): the worker "
                       "of unit %s may also write its own input stream", unit_id)
        client.execute_command("ACL", "SETUSER", user,
                               *acl_rules(unit_id, password, read_only_input=False))


def worker_url(service_url: str, user: str, password: str) -> str:
    """``service_url`` with its credentials replaced by the worker's."""
    parts = urlsplit(service_url)
    host = parts.hostname or ""
    if ":" in host:  # an IPv6 literal keeps its brackets
        host = f"[{host}]"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def grant(client, *, secret: str, unit_id: str, service_url: str,
          now: Optional[Callable[[], float]] = None) -> str:
    """Define the unit's worker user and return the URL its worker connects with.
    Raises :class:`WorkloadRedisError` when the user cannot be defined."""
    user, password = user_for(unit_id), password_for(secret, unit_id)
    try:
        _set_user(client, user, unit_id, password)
        client.hset(INDEX_KEY, user, f"{unit_id}|{int((now or time.time)())}")
    except Exception as e:  # noqa: BLE001 — any failure leaves the worker without a credential
        raise WorkloadRedisError(f"the worker's Redis user could not be defined: {type(e).__name__}") from e
    return worker_url(service_url, user, password)


def sweep(client, live_units: Iterable[str], *, now: Optional[Callable[[], float]] = None,
          grace_sec: float = SWEEP_GRACE_SEC) -> int:
    """Delete the users of units not in ``live_units`` (granted at least ``grace_sec`` ago).
    Returns how many were deleted."""
    live = set(live_units)
    current = (now or time.time)()
    removed = 0
    for user, value in (client.hgetall(INDEX_KEY) or {}).items():
        user = user.decode() if isinstance(user, bytes) else user
        value = value.decode() if isinstance(value, bytes) else value
        unit_id, _, granted = str(value).rpartition("|")
        try:
            age = current - float(granted)
        except ValueError:
            age = grace_sec
        if unit_id in live or age < grace_sec:
            continue
        client.execute_command("ACL", "DELUSER", user)
        client.hdel(INDEX_KEY, user)
        removed += 1
    return removed


def restore(client, *, secret: str) -> int:
    """Define again every indexed user Redis no longer has. Returns how many were restored."""
    restored = 0
    for user, value in (client.hgetall(INDEX_KEY) or {}).items():
        user = user.decode() if isinstance(user, bytes) else user
        value = value.decode() if isinstance(value, bytes) else str(value)
        if client.execute_command("ACL", "GETUSER", user):
            continue
        unit_id = value.rpartition("|")[0]
        _set_user(client, user, unit_id, password_for(secret, unit_id))
        restored += 1
    return restored


def start_sweeper(*, client_factory: Callable[[], object], live_units: Callable[[], Iterable[str]],
                  secret: str, restore_interval_sec: float = 1.0,
                  sweep_interval_sec: float = 600.0) -> Optional[threading.Event]:
    """In a daemon thread: every ``restore_interval_sec``, :func:`restore` when Redis is a new process
    (its ``run_id`` changed — one ``INFO`` otherwise), and :func:`sweep` every ``sweep_interval_sec``.
    A sweep that cannot read the runtime's workloads deletes nothing. Returns the stop event."""
    if restore_interval_sec <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        last_sweep = time.monotonic()
        run_id = None
        while not stop.wait(restore_interval_sec):
            try:
                client = client_factory()
                current = (client.info("server") or {}).get("run_id")
                if current != run_id:
                    restored = restore(client, secret=secret)
                    run_id = current
                    if restored:
                        logger.warning("redis lost %d worker user(s) (a restart) — defined again", restored)
            except Exception:  # noqa: BLE001
                logger.exception("workload redis restore failed")
            if time.monotonic() - last_sweep < sweep_interval_sec:
                continue
            last_sweep = time.monotonic()
            try:
                units = list(live_units())
            except Exception:  # noqa: BLE001 — without the live set nothing is safe to delete
                logger.warning("workload redis sweep skipped: the runtime's workloads could not be read")
                continue
            try:
                removed = sweep(client_factory(), units)
                if removed:
                    logger.info("workload redis sweep removed %d stale worker user(s)", removed)
            except Exception:  # noqa: BLE001
                logger.exception("workload redis sweep failed")

    threading.Thread(target=loop, name="workload-redis-sweep", daemon=True).start()
    return stop
