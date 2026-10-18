"""workspace_store_service.py — agent-api's side of the s3 workspace backend (WS-S3).

With ``VEXA_WORKSPACE_BACKEND=s3`` the workspace repositories live in a bucket and
``VEXA_WORKSPACES_DIR`` is a disposable cache (``core/workspaces/store``). This module wires that
into the service, and holds the founder's guarantee (2026-10-17) at the service edge: **no response
says a write happened before the write is durable in the bucket.**

* :func:`start` — at boot, before the app is built: self-test the bucket (fails loud), hydrate the
  cache, install the ``gitexec`` ref observer (every ref-moving git command publishes before it
  returns).
* :func:`install` — on the built app: a 503/409/403 answer for ``WorkspaceStoreError``; a
  middleware that, after every state-changing request and before its response leaves, mirrors
  whole-tree moves (``CacheSync``); a 30-second reconciler that also runs LRU eviction. Single
  writes outside git are durable before they return through the ``workspace_paths`` observer. The two routes workers use are ``routers/workspace_store.py``.
* :func:`record_unit_mounts` — the dispatcher records each unit's mount set; a worker may fetch and
  push only the mounts of its own dispatch, and push only the writable ones.

Workers never hold a bucket credential: they reach these routes through the gateway with their
delegation token (``routes.v1.json``: ``/agent/workspace-store/{fetch,push}``, ``delegation``).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from control_plane.identity_token import DELEGATION_HEADERS

logger = logging.getLogger("agent_api.workspace_store")

UNIT_HEADER = DELEGATION_HEADERS["unit"]
#: How long a unit's recorded mount set outlives its dispatch (re-recorded on every dispatch).
MOUNTS_TTL_S = 24 * 3600
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_running = None          # boot.Running, when the s3 backend is on
_local_mounts: dict = {}  # unit -> record, without Redis (tests)


def running():
    return _running


def backend() -> str:
    from workspaces.store import boot  # noqa: PLC0415

    return boot.backend()


def lazy() -> bool:
    """True when workspaces load on first use (the s3 backend is running)."""
    return _running is not None and getattr(_running, "cache", None) is not None


def health() -> Optional[dict]:
    """``/health``'s ``workspace_store`` block, or None on the volume backend."""
    return _running.health() if _running is not None else None


def start(workspaces_dir: str, redis_url: str):
    """Start the s3 backend (or do nothing on ``volume``). Raises ``StoreMisconfigured``: the boot
    stops with a sentence naming what to fix."""
    global _running
    from workspaces.store import boot  # noqa: PLC0415

    if boot.backend() != "s3":
        return None
    client = None
    if redis_url:
        import redis  # noqa: PLC0415

        client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=5)
    _running = boot.start(workspaces_dir, client)
    return _running


def worker_url(settings) -> str:
    """Where a worker reaches the store routes on the s3 backend: the gateway's
    ``/agent/workspace-store`` (the toolbelt's edge, ``VEXA_MCP_URL`` without ``/mcp``), or
    ``VEXA_WORKSPACE_STORE_URL`` when it is an http(s) URL. Empty on the volume backend."""
    if _running is None:
        return ""
    explicit = str(getattr(settings, "workspace_store_url", "") or "")
    if explicit.startswith(("http://", "https://")):
        return explicit.rstrip("/")
    mcp = str(getattr(settings, "mcp_url", "") or "").rstrip("/")
    return (mcp[: -len("/mcp")] + "/agent/workspace-store") if mcp.endswith("/mcp") else ""


def mount_key(unit_id: str) -> str:
    return f"wsstore:unit:{unit_id}"


def record_unit_mounts(r, unit_id: str, subject: str, env: dict) -> None:
    """Record what ``unit_id``'s worker may fetch and push: its subject and its mount set
    (``VEXA_MOUNTS``: path + write). Only on the s3 backend. Never raises; a unit with no record
    fetches and pushes nothing (fail closed)."""
    if _running is None:
        return
    try:
        mounts = json.loads(env.get("VEXA_MOUNTS") or "[]")
    except ValueError:
        mounts = []
    record = {"subject": str(subject),
              "mounts": [{"path": m["path"], "write": bool(m.get("write", True))}
                         for m in mounts if isinstance(m, dict) and m.get("path") and not m.get("source")]}
    if r is None:
        _local_mounts[unit_id] = record
        return
    try:
        r.set(mount_key(unit_id), json.dumps(record), ex=MOUNTS_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace store: mounts of %s not recorded (%s)", unit_id, type(exc).__name__)


def unit_record(r, unit_id: str) -> Optional[dict]:
    if r is None:
        return _local_mounts.get(unit_id)
    try:
        raw = r.get(mount_key(unit_id))
    except Exception:  # noqa: BLE001 — unreadable is no grant
        return None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def install(app: FastAPI, *, redis_url: str, workspaces_dir: str, sync_interval_s: float = 30.0) -> None:
    """Wire the running s3 backend into ``app``. No-op on the volume backend."""
    run = _running
    if run is None:
        return
    from workspaces.store.observer import WorkspaceStoreError  # noqa: PLC0415

    def refusal(exc: WorkspaceStoreError) -> JSONResponse:
        return JSONResponse({"detail": {"refused": exc.reason, "message": str(exc)}}, status_code=exc.status)

    # NO STORE WRITE AFTER THE RESPONSE HAS BEGUN: innermost, in the endpoint's own task, so the
    # mark reaches a streamed body (and the threads a sync generator runs in) — a write there
    # raises instead of being acknowledged by a 200 that already left.
    from workspaces.store.observer import StreamGuard  # noqa: PLC0415

    if not isinstance(app.router.middleware_stack, StreamGuard):
        app.router.middleware_stack = StreamGuard(app.router.middleware_stack)

    @app.exception_handler(WorkspaceStoreError)
    async def _store_error(_request, exc):  # noqa: ANN001
        return refusal(exc)

    @app.middleware("http")
    async def _durable_before_answer(request: Request, call_next):
        response = await call_next(request)
        if request.method.upper() in _SAFE_METHODS or response.status_code >= 400:
            return response
        try:
            import anyio  # noqa: PLC0415

            await anyio.to_thread.run_sync(run.sync.sync)
        except WorkspaceStoreError as exc:
            logger.error("workspace store: %s %s answered %d but was not made durable: %s",
                         request.method, request.url.path, response.status_code, exc)
            return refusal(exc)
        return response

    stop = threading.Event()

    # A RECONCILER, NOT A WRITE PATH. Every write is durable before it returns (the gitexec and
    # workspace_paths observers, atomic_json, notify_written in the by-name writers, and the
    # middleware above for whole-tree moves inside a request); this pass only re-checks the cache
    # against the bucket and applies LRU eviction under the cache size limit.
    def _tick() -> None:
        while not stop.wait(sync_interval_s):
            try:
                if run.cache is not None:
                    run.cache.refresh_sizes()
                    run.cache.maybe_evict()
                run.sync.sync()
            except Exception as exc:  # noqa: BLE001 — reported on /health, retried next tick
                logger.warning("workspace store: background sync failed: %s", exc)

    threading.Thread(target=_tick, name="workspace-store-sync", daemon=True).start()
    app.state.workspace_store = run
    app.state.workspace_store_stop = stop
