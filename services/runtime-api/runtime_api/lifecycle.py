"""Lifecycle management — idle timeouts and callback delivery.

Idle loop: periodically checks running containers and stops those that
have exceeded their profile's idle_timeout without a /touch heartbeat.

Callback delivery: POSTs {container_id, name, profile, status, exit_code, metadata}
to the callback_url provided at container creation time.
Retries with exponential backoff (default: 1s, 5s, 30s).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from runtime_api import config, state
from runtime_api.backends import Backend
from runtime_api.profiles import get_profile

logger = logging.getLogger("runtime_api.lifecycle")


async def idle_loop(redis, backend: Backend) -> None:
    """Background task: stop containers that have been idle too long."""
    while True:
        await asyncio.sleep(config.IDLE_CHECK_INTERVAL)
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
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("Idle check error", exc_info=True)


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
    """Attempt to deliver a callback with exponential backoff."""
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

    logger.error(f"Callback delivery exhausted for {name} after {config.CALLBACK_RETRIES} attempts")


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
