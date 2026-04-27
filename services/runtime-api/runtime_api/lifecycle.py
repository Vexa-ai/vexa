"""Lifecycle management — idle timeouts and callback delivery.

Idle loop: periodically checks running containers and stops those that
have exceeded their profile's idle_timeout without a /touch heartbeat.

Callback delivery: POSTs {container_id, name, profile, status, exit_code, metadata}
to the callback_url provided at container creation time.
Retries with exponential backoff (default: 1s, 5s, 30s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from runtime_api import config, state
from runtime_api.backends import Backend
from runtime_api.profiles import get_profile

logger = logging.getLogger("runtime_api.lifecycle")


async def idle_loop(redis, backend: Backend) -> None:
    """Background task: stop idle containers + sweep pending exit callbacks
    + reconcile browser-session secondary index against primary container state.

    Three responsibilities, one loop tick:
      (1) Stop containers that have been idle past their profile's timeout.
      (2) Re-try any exit callback that has not yet been acknowledged by the
          consumer. This makes exit-callback delivery durable across consumer
          outages — without it, runtime-api gives up after CALLBACK_RETRIES
          and the downstream meeting row is stuck 'active' forever.
      (3) v0.10.5 Pack K.2 — sweep browser_session:* secondary index for
          orphans whose container_name is missing from the primary
          `runtime:container:*` index. [PLATFORM] data on #273 showed 14
          stale browser_session entries vs 0 actual K8s pods — pods were
          reaped but the secondary index never reconciled, leading to the
          "Browser session not found or expired" UX symptom.

    v0.10.5 Pack K.5 — heartbeat instrumentation logs the loop's tick
    rate. Without an external observable, can't tell whether the loop is
    "running silently" vs "not running at all" (closes #273 Finding 3).
    """
    iteration = 0
    while True:
        await asyncio.sleep(config.IDLE_CHECK_INTERVAL)
        # v0.10.5 Pack K.5 — heartbeat at start of iteration (not end), so
        # a hung sweep still advances the counter on the iteration that
        # started. External observers can detect "loop not running" via a
        # stale `last_iteration_at`. Metrics infrastructure (Pack M) wires
        # this into Prometheus; until then, the structured log + the
        # in-memory counter on `lifecycle.idle_loop_last_iteration_at`
        # gives operators something to grep / log-aggregate against.
        iteration += 1
        global idle_loop_iterations, idle_loop_last_iteration_at
        idle_loop_iterations = iteration
        idle_loop_last_iteration_at = time.time()
        if iteration % 60 == 1:
            # Log heartbeat every ~60 iterations (1× / IDLE_CHECK_INTERVAL × 60)
            # so logs aren't spammed but external grep can confirm liveness.
            logger.info(
                f"idle_loop heartbeat: iteration={iteration} "
                f"last_iteration_at={idle_loop_last_iteration_at:.0f}"
            )
        try:
            containers = await state.list_containers(redis)
            now = time.time()
            for c in containers:
                if c.get("status") != "running":
                    continue
                profile_name = c.get("profile", "")
                profile_def = get_profile(profile_name)
                if not profile_def:
                    continue

                timeout = profile_def.get("idle_timeout", 300)
                if timeout == 0:
                    continue  # no idle timeout

                created = c.get("created_at", now)
                updated = c.get("updated_at", created)
                if now - updated > timeout:
                    name = c.get("name", "")
                    logger.info(f"Container {name} idle >{timeout}s, stopping")
                    try:
                        await backend.stop(name)
                        await backend.remove(name)
                        await state.set_stopped(redis, name)
                        # Fire callback
                        await _fire_exit_callback(redis, name, exit_code=0)
                    except Exception:
                        logger.warning(f"Failed to stop idle container {name}", exc_info=True)

            # Durable exit-callback sweep — re-deliver anything still pending.
            # This is the single mechanism that makes callback delivery
            # eventually-complete; _deliver_callback no longer deletes the
            # record on burst-exhaustion, so we retry here every tick.
            try:
                pending_names = await state.list_pending_callbacks(redis)
            except Exception:
                pending_names = []
                logger.debug("pending-callback scan failed", exc_info=True)
            for name in pending_names:
                try:
                    await _deliver_callback(redis, name)
                except Exception:
                    logger.debug(f"pending-callback sweep delivery failed for {name}", exc_info=True)

            # v0.10.5 Pack K.2 — reconcile browser_session:* secondary index.
            #
            # The browser_session:<meeting_id> Redis key (set by meeting-api
            # at dispatch time, TTL ~24h) is a secondary lookup from
            # meeting_id to container_name. When the primary container is
            # reaped (natural exit, K8s eviction, manual delete), the
            # primary `runtime:container:*` entry clears but this secondary
            # index doesn't — leaving stale entries that downstream services
            # serve as if live. [PLATFORM] data 2026-04-27: 14 stale entries
            # vs 0 K8s pods.
            #
            # Sweep: iterate browser_session:*, for each entry check whether
            # its container_name still exists in the primary index. If not,
            # the entry is orphan — delete it.
            try:
                orphan_count = 0
                async for key in redis.scan_iter("browser_session:*"):
                    try:
                        raw = await redis.get(key)
                        if not raw:
                            continue
                        # Entries can be either a JSON object {container_name, ...}
                        # or a bare container_name string (legacy shape).
                        try:
                            entry = json.loads(raw)
                            container_name = (
                                entry.get("container_name") if isinstance(entry, dict)
                                else None
                            )
                        except (json.JSONDecodeError, TypeError):
                            container_name = raw  # legacy bare-string shape
                        if not container_name:
                            continue
                        primary = await state.get_container(redis, container_name)
                        # Orphan if primary index doesn't have it OR primary
                        # has it as stopped/failed (pod reaped).
                        if primary is None or primary.get("status") in ("stopped", "failed"):
                            await redis.delete(key)
                            orphan_count += 1
                            logger.warning(
                                f"Pack K.2 reconcile: deleted orphan {key} -> {container_name} "
                                f"(primary={'missing' if primary is None else primary.get('status')})"
                            )
                    except Exception:
                        logger.debug(f"Pack K.2 reconcile error on {key}", exc_info=True)
                if orphan_count > 0:
                    logger.warning(
                        f"Pack K.2 reconcile: swept {orphan_count} orphan browser_session entries"
                    )
            except Exception:
                logger.debug("Pack K.2 reconcile scan failed", exc_info=True)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("Idle check error", exc_info=True)


# v0.10.5 Pack K.5 — module-level heartbeat state.
# External observable: read these from a /metrics or /health endpoint
# elsewhere in runtime-api. Set at the START of each idle_loop iteration
# (not the end) so a hung sweep handler still updates the counter.
idle_loop_iterations: int = 0
idle_loop_last_iteration_at: float = 0.0


async def handle_container_exit(redis, name: str, exit_code: int) -> None:
    """Called when a container exits (from event listener or reaper).

    Updates state and delivers the exit callback.
    """
    status = "stopped" if exit_code == 0 else "failed"
    logger.info(f"Container {name} exited with code {exit_code} -> {status}")
    await state.set_stopped(redis, name, status=status, exit_code=exit_code)
    await _fire_exit_callback(redis, name, exit_code=exit_code)


async def _fire_exit_callback(redis, name: str, exit_code: int = 0) -> None:
    """Deliver exit callback to the URL provided at creation time."""
    container_data = await state.get_container(redis, name)
    if not container_data:
        return

    callback_url = container_data.get("callback_url")
    if not callback_url:
        return

    metadata = container_data.get("metadata", {})
    if not metadata.get("connection_id"):
        logger.warning(f"No connection_id in metadata for {name} — skipping exit callback")
        return

    payload = {
        # Merge metadata first so domain-specific fields (e.g. connection_id)
        # appear as top-level keys in the callback payload.
        **metadata,
        "container_id": container_data.get("container_id", ""),
        "name": name,
        "profile": container_data.get("profile", ""),
        "status": "stopped" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "metadata": metadata,
    }

    # Store as pending for retry
    await state.store_pending_callback(redis, name, {
        "url": callback_url,
        "payload": payload,
        "attempts": 0,
    })

    await _deliver_callback(redis, name)


async def _deliver_callback(redis, name: str) -> None:
    """Attempt to deliver a callback with exponential backoff.

    One burst = CALLBACK_RETRIES attempts. If the burst exhausts without a
    2xx/3xx, the pending-callback record is LEFT in Redis so the idle_loop
    sweeper can retry on its next tick. This makes exit-callback delivery
    durable across consumer outages: the only way a pending callback stops
    retrying is by succeeding (or TTL expiry, which is the outer bound).
    """
    cb = await state.get_pending_callback(redis, name)
    if not cb:
        return

    url = cb["url"]
    payload = cb["payload"]
    backoff = config.CALLBACK_BACKOFF

    for attempt in range(config.CALLBACK_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code < 400:
                    logger.info(f"Callback delivered for {name} -> {url} (attempt {attempt + 1})")
                    await state.delete_pending_callback(redis, name)
                    return
                logger.warning(
                    f"Callback for {name} returned {resp.status_code} (attempt {attempt + 1})"
                )
        except Exception as e:
            logger.warning(f"Callback delivery failed for {name} (attempt {attempt + 1}): {e}")

        if attempt < config.CALLBACK_RETRIES - 1:
            delay = backoff[attempt] if attempt < len(backoff) else backoff[-1]
            logger.info(f"Retrying callback for {name} in {delay}s")
            await asyncio.sleep(delay)

    # Burst exhausted. Leave the pending-callback record in Redis so idle_loop
    # will re-invoke this function on its next tick; do NOT call
    # delete_pending_callback here.
    logger.warning(
        f"Callback burst exhausted for {name} after {config.CALLBACK_RETRIES} attempts; "
        f"idle_loop will retry on next tick (IDLE_CHECK_INTERVAL)"
    )


async def reconcile_state(redis, backend: Backend) -> None:
    """On startup, sync Redis state with backend reality.

    Containers that exist in the backend but not in Redis get added.
    Redis entries for containers that no longer exist get marked stopped.
    """
    try:
        backend_containers = await backend.list()
        backend_names = set()
        count = 0

        for c in backend_containers:
            backend_names.add(c.name)
            data = {
                "status": c.status,
                "profile": c.labels.get("runtime.profile", "unknown"),
                "user_id": c.labels.get("runtime.user_id", "unknown"),
                "image": c.image or "",
                "created_at": c.created_at or time.time(),
                "ports": c.ports,
                "container_id": c.id,
            }
            await state.set_container(redis, c.name, data)
            count += 1

        # Mark stale Redis entries as stopped
        redis_containers = await state.list_containers(redis)
        stale = 0
        for rc in redis_containers:
            rname = rc.get("name", "")
            if rname and rname not in backend_names and rc.get("status") == "running":
                await state.set_stopped(redis, rname)
                stale += 1

        if count or stale:
            logger.info(f"Reconciled: {count} from backend, {stale} stale entries cleaned")
    except Exception as e:
        logger.warning(f"State reconciliation failed: {e}")
