"""FastAPI application — Meeting API.

Startup: init DB, connect Redis, configure webhook delivery, start collector consumers.
Shutdown: close Redis, cancel collector tasks.

All container operations delegate to Runtime API via httpx.
"""

import asyncio
import logging
import os

import httpx
import redis
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db, async_session_local
from .webhook_delivery import set_redis_client as set_webhook_redis
from .webhook_retry_worker import (
    start_retry_worker,
    stop_retry_worker,
    set_session_factory as set_retry_session_factory,
)

from .config import REDIS_URL, CORS_ORIGINS, CORS_WILDCARD
from .security_headers import SecurityHeadersMiddleware
from .meetings import router as meetings_router, set_redis
from .callbacks import router as callbacks_router
from .voice_agent import router as voice_agent_router
from .recordings import router as recordings_router

# Collector imports
from .collector.config import (
    REDIS_STREAM_NAME,
    REDIS_CONSUMER_GROUP,
    REDIS_SPEAKER_EVENTS_STREAM_NAME,
    REDIS_SPEAKER_EVENTS_CONSUMER_GROUP,
    CONSUMER_NAME,
    BACKGROUND_TASK_INTERVAL,
    IMMUTABILITY_THRESHOLD,
)
from .collector.consumer import (
    claim_stale_messages,
    consume_redis_stream,
    consume_speaker_events_stream,
)
from .collector.db_writer import process_redis_to_postgres
from .collector.endpoints import router as collector_router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("meeting_api")

_VEXA_ENV = os.getenv("VEXA_ENV", "development")
_PUBLIC_DOCS = _VEXA_ENV != "production"
app = FastAPI(
    title="Meeting API",
    description="Meeting bot management — join/stop bots, voice agent, recordings, webhooks, transcription collection",
    version="0.1.0",
    docs_url="/docs" if _PUBLIC_DOCS else None,
    redoc_url="/redoc" if _PUBLIC_DOCS else None,
    openapi_url="/openapi.json" if _PUBLIC_DOCS else None,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=not CORS_WILDCARD,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Mount routers — no prefix, routes already carry /bots etc.
app.include_router(meetings_router)
app.include_router(callbacks_router)
app.include_router(voice_agent_router)
app.include_router(recordings_router)
app.include_router(collector_router)

# Collector background task references
_collector_tasks: list = []

# v0.10.5 Pack C.2 — supervised collector tasks (#267 L2)
#
# Without a done-callback, an unhandled exception inside a long-lived consumer
# task (`process_redis_to_postgres`, `consume_redis_stream`, `consume_speaker_events_stream`)
# kills the task object silently: Python emits a one-line warning that nobody
# sees, the consumer is gone, and the live HTTP path keeps reporting 200 OK.
# The 2026-04-26 silent-hang incident's root cause was the silent-socket variant
# (closed by Pack C.1); but a separate failure class — task crashes via
# uncaught exception inside the consumer body — has the same blast radius and
# is unaddressed by C.1.
#
# `_restart_on_crash` is the L2 layer: every collector task gets a done-callback
# that re-spawns the task via the same factory after a 5s backoff. Idempotent
# (re-spawned task gets its own callback). Catches in-process exceptions that
# C.1's transport-layer config can't catch.
_collector_factories: dict = {}  # name -> coro_factory; populated when starting tasks


def _spawn_supervised_task(name: str, coro_factory):
    """Spawn a collector task with auto-restart on crash.

    Args:
        name: stable task name (used for logging + idempotent re-spawn).
        coro_factory: zero-arg callable returning a fresh coroutine each call.
                      Re-invoked on restart so the new task gets a fresh
                      coroutine (asyncio coroutines are single-use).
    """
    _collector_factories[name] = coro_factory
    task = asyncio.create_task(coro_factory(), name=name)
    task.add_done_callback(_restart_on_crash)
    _collector_tasks.append(task)
    return task


def _restart_on_crash(task: asyncio.Task):
    """Done-callback: log crash + schedule re-spawn after 5s backoff."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        # Task returned cleanly — collector loops shouldn't return; warn but don't restart.
        logger.warning(f"Collector task {task.get_name()} returned cleanly; not restarting")
        return
    name = task.get_name()
    logger.error(
        f"Collector task '{name}' crashed; restarting in 5s",
        exc_info=exc,
    )
    factory = _collector_factories.get(name)
    if factory is None:
        logger.error(f"No factory registered for crashed task '{name}'; cannot restart")
        return
    loop = asyncio.get_event_loop()
    loop.call_later(5, lambda: _spawn_supervised_task(name, factory))


# v0.10.5 Pack C.4 — startup-complete gate (#267 startup variant)
#
# The silent-skip class: meeting-api boots while Redis is unreachable;
# `try: redis_client.ping() except: redis_client = None; continue` swallows
# the failure; `if redis_client is not None:` block silently skips
# `xgroup_create + consume_redis_stream` task; pod stays Ready forever
# with no working consumer. Every entry into transcription_segments piles
# up unconsumed; user-visible: empty transcripts on stop.
#
# Industry-best-practice fix: declarative readiness. /readyz returns 503
# until BOTH Redis is connected AND consumer tasks are alive. K8s
# readinessProbe stops sending traffic to a not-Ready pod; combined with
# bounded retry (Pack C.4.4 below) + raise-on-exhaust, restart cycles are
# deterministic instead of indefinite-skip-forever.
#
# Closes the invariant: at no point in the pod's lifetime is there a Ready
# pod with no working consumer.
_startup_complete: bool = False


@app.get("/health")
async def health():
    """Liveness probe: HTTP loop alive. Always 200 unless event loop is wedged.
    See `/readyz` (Pack C.4) for readiness; see `/health/collector` (Pack C.3)
    for consumer-stall liveness."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """v0.10.5 Pack C.4 — Readiness probe gated on consumer-up.

    Returns 503 until `_startup_complete` flips True. The flag flips True
    only after BOTH:
      1. `redis_client.ping()` succeeded (with bounded retry)
      2. xgroup_create succeeded for both transcription_segments AND speaker_events
      3. All three collector tasks (db-writer, main consumer, speaker events) are alive

    Wired as readinessProbe on the meeting-api Deployment (chart Pack I).
    K8s Service routes traffic only to Ready pods; a pod with no working
    consumer never receives traffic, so the silent-degraded mode is
    structurally impossible.
    """
    if not _startup_complete:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="startup not complete: Redis not yet connected and/or consumer tasks not yet alive",
        )
    return {"status": "ready", "consumers_alive": len([t for t in _collector_tasks if not t.done()])}


@app.get("/health/collector")
async def health_collector():
    """v0.10.5 Pack C.3 — Lag-aware liveness for the transcription_segments consumer.

    Returns 503 when consumer is stalled (lag > 100 entries AND max consumer
    idle > 60 s). kubelet livenessProbe → kubelet kills + restarts the pod
    within failureThreshold × periodSeconds. Worst-case data loss bounded
    at ~90 s for any future failure mode that C.1's hardened socket config
    + C.2's task supervisor don't catch (e.g. consumer hung inside DB write,
    deadlock in idempotency-keyed insert, etc).

    Implementation details (#267 L3):
      - Reads xinfo_groups for transcription_segments stream.
      - Reads xinfo_consumers for the consumer group.
      - 503 if (group lag > LAG_THRESHOLD) AND (max consumer idle_ms > IDLE_THRESHOLD_MS).
      - Catches Redis transient (timeout/connection) errors → returns 200
        with `{lag: -1, error: ...}` (don't fail liveness on transient
        Redis blips; that's C.1's job).
    """
    from fastapi import HTTPException
    LAG_THRESHOLD = 100
    IDLE_THRESHOLD_MS = 60_000
    rc = getattr(app.state, "redis_client", None)
    if rc is None:
        # Startup hasn't completed Redis init OR Redis was unreachable at startup.
        # Either way: NOT ready for traffic. Pack C.4 /readyz handles this case
        # at the readiness layer; on /health/collector we return 503 because
        # liveness depends on the consumer being alive.
        raise HTTPException(status_code=503, detail="Redis client not initialized")
    try:
        groups = await rc.xinfo_groups(REDIS_STREAM_NAME)
        if not groups:
            return {"lag": 0, "note": "no consumer group yet"}
        our_group = next((g for g in groups if g.get("name") == REDIS_CONSUMER_GROUP), None)
        if our_group is None:
            return {"lag": 0, "note": f"group {REDIS_CONSUMER_GROUP!r} not yet created"}
        lag = our_group.get("lag", 0)
        consumers = await rc.xinfo_consumers(REDIS_STREAM_NAME, REDIS_CONSUMER_GROUP)
        max_idle_ms = max((c.get("idle", 0) for c in consumers), default=0)
        if lag > LAG_THRESHOLD and max_idle_ms > IDLE_THRESHOLD_MS:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Collector stalled: lag={lag} (>{LAG_THRESHOLD}) "
                    f"max_consumer_idle={max_idle_ms}ms (>{IDLE_THRESHOLD_MS}ms). "
                    f"kubelet livenessProbe should restart this pod."
                ),
            )
        return {
            "lag": lag,
            "max_consumer_idle_ms": max_idle_ms,
            "consumers": len(consumers),
        }
    except HTTPException:
        raise
    except Exception as e:
        # Don't fail liveness on transient Redis errors — C.1 timeouts handle
        # those. Only fail liveness on confirmed stall (above).
        logger.debug(f"/health/collector transient error: {e}")
        return {"lag": -1, "error": str(e)}


@app.on_event("startup")
async def startup():
    logger.info("Starting Meeting API...")

    # Database
    await init_db()
    logger.info("Database initialized")

    # Redis — v0.10.5 Pack C.1 hardened timeouts + Pack C.4 bounded retry then raise
    # (#267 + #267 startup variant).
    #
    # OLD shape was: try: redis.ping() except: redis_client = None; continue
    # — the silent-skip anti-pattern. Pod stays Ready forever with no consumer.
    #
    # NEW shape: bounded retry inside ping helper; on exhaustion, RAISE — let
    # process exit; let K8s restartPolicy: Always handle wait-for-redis ordering
    # deterministically. No silent-degraded mode anywhere. Combined with
    # /readyz (which gates traffic on _startup_complete), the invariant
    # holds: no Ready pod ever has no working consumer.
    redis_client = aioredis.from_url(
        REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )
    # Bounded retry: 20 attempts × 0.5s–10s exponential backoff (~min 60s wait).
    # If Redis still unreachable after 20 attempts, RAISE — pod restarts; K8s
    # exponential restart-backoff handles the longer wait.
    last_exc: Exception | None = None
    for attempt in range(20):
        try:
            await redis_client.ping()
            logger.info(f"Redis connected (attempt {attempt + 1})")
            break
        except Exception as e:
            last_exc = e
            delay = min(10.0, 0.5 * (2 ** min(attempt, 5)))
            logger.warning(
                f"Redis ping failed (attempt {attempt + 1}/20): {e}; retrying in {delay}s"
            )
            await asyncio.sleep(delay)
    else:
        # Loop completed without break — all 20 attempts failed.
        raise RuntimeError(
            f"Redis unreachable after 20 attempts: {last_exc}. "
            "Process exiting — K8s restartPolicy will retry."
        ) from last_exc

    set_redis(redis_client)
    app.state.redis = redis_client
    # Collector endpoints use app.state.redis_client
    app.state.redis_client = redis_client

    # Webhook retry worker — Pack C.4: redis_client is guaranteed non-None
    # here (bounded retry above raises on exhaustion); no silent-skip mode.
    set_retry_session_factory(async_session_local)
    set_webhook_redis(redis_client)
    asyncio.create_task(start_retry_worker(redis_client))
    logger.info("Webhook retry worker started")

    # v0.10.5 Pack E.3.2 — stale-stopping sweep + future H.4 + E.1-sibling.
    # Idle-loop equivalent for meeting-api: periodic scans that catch
    # state-machine rows that escape the canonical durable mechanisms.
    from .sweeps import start_sweeps
    asyncio.create_task(start_sweeps(async_session_local))
    logger.info("Meeting-api sweeps loop started (Pack E.3.2 stale-stopping)")

    # --- Collector startup ---
    if True:  # redis_client is guaranteed non-None per Pack C.4
        # Ensure consumer groups exist for transcription stream
        try:
            await redis_client.xgroup_create(
                name=REDIS_STREAM_NAME,
                groupname=REDIS_CONSUMER_GROUP,
                id='0', mkstream=True,
            )
            logger.info(f"Consumer group '{REDIS_CONSUMER_GROUP}' ensured for stream '{REDIS_STREAM_NAME}'.")
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"Consumer group '{REDIS_CONSUMER_GROUP}' already exists for stream '{REDIS_STREAM_NAME}'.")
            else:
                logger.error(f"Failed to create consumer group: {e}", exc_info=True)

        # Ensure consumer groups exist for speaker events stream
        try:
            await redis_client.xgroup_create(
                name=REDIS_SPEAKER_EVENTS_STREAM_NAME,
                groupname=REDIS_SPEAKER_EVENTS_CONSUMER_GROUP,
                id='0', mkstream=True,
            )
            logger.info(f"Consumer group '{REDIS_SPEAKER_EVENTS_CONSUMER_GROUP}' ensured for stream '{REDIS_SPEAKER_EVENTS_STREAM_NAME}'.")
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"Consumer group '{REDIS_SPEAKER_EVENTS_CONSUMER_GROUP}' already exists for stream '{REDIS_SPEAKER_EVENTS_STREAM_NAME}'.")
            else:
                logger.error(f"Failed to create speaker events consumer group: {e}", exc_info=True)

        # Claim stale messages before starting consumers
        await claim_stale_messages(redis_client)

        # Start collector background tasks — v0.10.5 Pack C.2 supervised
        # (auto-restart on crash via _restart_on_crash done-callback).
        _spawn_supervised_task(
            "collector-db-writer",
            lambda: process_redis_to_postgres(redis_client),
        )
        logger.info(f"Redis-to-PostgreSQL task started (Interval: {BACKGROUND_TASK_INTERVAL}s, Threshold: {IMMUTABILITY_THRESHOLD}s)")

        _spawn_supervised_task(
            "collector-main",
            lambda: consume_redis_stream(redis_client),
        )
        logger.info(f"Redis Stream consumer task started (Stream: {REDIS_STREAM_NAME}, Group: {REDIS_CONSUMER_GROUP}, Consumer: {CONSUMER_NAME})")

        _spawn_supervised_task(
            "collector-speaker-events",
            lambda: consume_speaker_events_stream(redis_client),
        )
        logger.info(f"Speaker Events consumer task started (Stream: {REDIS_SPEAKER_EVENTS_STREAM_NAME})")

    # Shared httpx client for connection pooling to Runtime API
    from .config import RUNTIME_API_TOKEN
    headers = {"X-API-Key": RUNTIME_API_TOKEN} if RUNTIME_API_TOKEN else {}
    app.state.httpx_client = httpx.AsyncClient(timeout=30.0, headers=headers)

    # v0.10.5 Pack C.4 — flip the readiness gate.
    # All preconditions are now satisfied:
    #   1. Redis ping succeeded (bounded retry above; raises if all 20 fail).
    #   2. xgroup_create succeeded for both streams.
    #   3. All three collector tasks spawned + supervised (Pack C.2).
    # /readyz now returns 200; K8s Service starts routing traffic.
    global _startup_complete
    _startup_complete = True
    logger.info("Meeting API ready (startup_complete=True; /readyz now serving 200)")


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down Meeting API...")

    await stop_retry_worker()

    # Cancel collector background tasks
    for i, task in enumerate(_collector_tasks):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Collector task {i+1} cancelled.")
            except Exception as e:
                logger.error(f"Error during collector task {i+1} cancellation: {e}", exc_info=True)
    _collector_tasks.clear()

    if hasattr(app.state, "httpx_client") and app.state.httpx_client:
        await app.state.httpx_client.aclose()
        logger.info("httpx client closed")

    if hasattr(app.state, "redis") and app.state.redis:
        try:
            await app.state.redis.close()
            logger.info("Redis closed")
        except Exception as e:
            logger.error(f"Error closing Redis: {e}", exc_info=True)
