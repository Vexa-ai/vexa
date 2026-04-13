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

app = FastAPI(
    title="Meeting API",
    description="Meeting bot management — join/stop bots, voice agent, recordings, webhooks, transcription collection",
    version="0.1.0",
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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    logger.info("Starting Meeting API...")

    # Database
    await init_db()
    logger.info("Database initialized")

    # Redis
    redis_client = None
    try:
        redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}", exc_info=True)
        redis_client = None

    set_redis(redis_client)
    app.state.redis = redis_client
    # Collector endpoints use app.state.redis_client
    app.state.redis_client = redis_client

    # Webhook retry worker
    set_retry_session_factory(async_session_local)
    if redis_client is not None:
        set_webhook_redis(redis_client)
        asyncio.create_task(start_retry_worker(redis_client))
        logger.info("Webhook retry worker started")
    else:
        logger.warning("Webhook retry worker NOT started — Redis unavailable")

    # --- Collector startup ---
    if redis_client is not None:
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

        # Start collector background tasks
        _collector_tasks.append(asyncio.create_task(process_redis_to_postgres(redis_client)))
        logger.info(f"Redis-to-PostgreSQL task started (Interval: {BACKGROUND_TASK_INTERVAL}s, Threshold: {IMMUTABILITY_THRESHOLD}s)")

        _collector_tasks.append(asyncio.create_task(consume_redis_stream(redis_client)))
        logger.info(f"Redis Stream consumer task started (Stream: {REDIS_STREAM_NAME}, Group: {REDIS_CONSUMER_GROUP}, Consumer: {CONSUMER_NAME})")

        _collector_tasks.append(asyncio.create_task(consume_speaker_events_stream(redis_client)))
        logger.info(f"Speaker Events consumer task started (Stream: {REDIS_SPEAKER_EVENTS_STREAM_NAME})")
    else:
        logger.warning("Collector consumers NOT started — Redis unavailable")

    # Shared httpx client for connection pooling to Runtime API
    from .config import RUNTIME_API_TOKEN
    headers = {"X-API-Key": RUNTIME_API_TOKEN} if RUNTIME_API_TOKEN else {}
    app.state.httpx_client = httpx.AsyncClient(timeout=30.0, headers=headers)

    logger.info("Meeting API ready")


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
