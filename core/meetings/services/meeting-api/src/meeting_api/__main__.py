"""``python -m meeting_api`` — the production meeting-api (P4 compose CMD).

Assembles the unified modular-monolith (``meeting_api.create_app``) with the REAL per-module
adapters (SQLAlchemy + redis + MinIO/S3 + httpx-runtime), then — per P4 — ALSO starts the
control-plane background loops alongside the HTTP app via the FastAPI lifespan:

  * **collector segment consumer** — drains the ``transcription_segments`` redis stream
    (``consume_segments`` → ``ingest`` → publish ``tc:…:mutable``) on a poll interval.
  * **webhook retry-drain** — one ``drain_retry_queue`` sweep per interval over the redis retry
    queue (failed ``meeting.status_change`` deliveries are retried with backoff).
  * **scheduler tick** — fires due ``schedule.v1`` jobs (this also drives the join-retry re-spawns
    that ``JoinRetryController`` schedules) on the tick interval.

Each loop is a single-tick function the eval drives explicitly; here the entrypoint wraps it in the
``while True: tick; sleep`` poll the deployment uses. uvicorn-target: ``uvicorn meeting_api.__main__:app``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

log = logging.getLogger("meeting_api.entrypoint")


def _database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    host = os.getenv("DB_HOST", "postgres")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "vexa")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


def build_production_app():
    """Wire the unified meeting-api with the real adapters + the lifespan-driven loops."""
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from . import create_app
    from .bot_spawn.adapters import HttpRuntimeClient, SqlAlchemyMeetingRepo
    from .collector.adapters import RedisStreamBus, SqlAlchemyTranscriptStore
    from .recordings.adapters import S3Storage, SqlAlchemyRecordingRepo

    database_url = _database_url()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    runtime_api_url = os.getenv("RUNTIME_API_URL", "http://runtime:8090")
    # MeetingToken is HS256-signed (mint) AND verified (recordings upload) with the SAME secret =
    # ADMIN_TOKEN, exactly like main. (INTERNAL_API_SECRET is for the gateway↔admin-api internal
    # validation only — a different concern.) None → the recordings verifier falls back to ADMIN_TOKEN.
    token_secret = os.getenv("ADMIN_TOKEN") or None

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)

    # Per-module production adapters (each module's adapters.* builders) injected into create_app.
    transcript_store = SqlAlchemyTranscriptStore(session_factory, redis_client=redis_client)
    segment_bus = RedisStreamBus(redis_client)
    meeting_repo = SqlAlchemyMeetingRepo(session_factory)

    import httpx

    runtime_http = httpx.AsyncClient(timeout=30.0)
    runtime_client = HttpRuntimeClient(runtime_http, runtime_api_url)

    recording_repo = SqlAlchemyRecordingRepo(session_factory)
    storage = S3Storage(
        bucket=os.getenv("MINIO_BUCKET", os.getenv("RECORDING_BUCKET", "vexa")),
        endpoint_url=os.getenv("S3_ENDPOINT") or _minio_endpoint_url(),
        access_key=os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY"),
        secret_key=os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY"),
    )

    app = create_app(
        transcript_store=transcript_store,
        redis=segment_bus,
        meeting_repo=meeting_repo,
        runtime=runtime_client,
        recording_repo=recording_repo,
        storage=storage,
        token_secret=token_secret,
    )

    _attach_background_loops(app, transcript_store, segment_bus, redis_client)
    return app


def _minio_endpoint_url() -> str:
    """Build an http(s) MinIO URL from MINIO_ENDPOINT (host:port) + MINIO_SECURE, mirroring 0.11."""
    endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    scheme = "https" if os.getenv("MINIO_SECURE", "false").lower() == "true" else "http"
    return f"{scheme}://{endpoint}"


def _attach_background_loops(app, transcript_store, segment_bus, redis_client) -> None:
    """Register the FastAPI lifespan that starts/stops the control-plane poll loops."""
    from .collector.ingest import consume_segments

    seg_interval = float(os.getenv("SEGMENT_CONSUMER_INTERVAL", "0.5"))
    webhook_interval = float(os.getenv("WEBHOOK_DRAIN_INTERVAL", "5"))
    scheduler_interval = float(os.getenv("SCHEDULER_TICK_INTERVAL", "1"))

    async def _segment_consumer_loop() -> None:
        # Drain the transcription_segments stream → persist + publish tc:…:mutable.
        while True:
            try:
                await consume_segments(transcript_store, segment_bus)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("segment consumer tick failed")
            await asyncio.sleep(seg_interval)

    async def _webhook_drain_loop() -> None:
        import httpx

        from .webhooks.retry import drain_retry_queue

        # The injected Transport: POST the signed envelope; return the response (its .status_code
        # drives the retry/permanent decision in retry._deliver_one).
        async def _transport(url: str, body: bytes, headers: dict):
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await client.post(url, content=body, headers=headers)

        while True:
            try:
                await drain_retry_queue(redis_client, _transport)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("webhook retry-drain tick failed")
            await asyncio.sleep(webhook_interval)

    async def _scheduler_tick_loop() -> None:
        # The scheduler fires due schedule.v1 jobs — including the join-retry re-spawns that
        # JoinRetryController enqueues. The Scheduler instance lives on app.state when wired.
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is None:
            return
        while True:
            try:
                scheduler.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(scheduler_interval)

    @asynccontextmanager
    async def lifespan(_app):
        tasks = [
            asyncio.create_task(_segment_consumer_loop(), name="segment-consumer"),
            asyncio.create_task(_webhook_drain_loop(), name="webhook-drain"),
            asyncio.create_task(_scheduler_tick_loop(), name="scheduler-tick"),
        ]
        log.info("meeting-api background loops started: %s", [t.get_name() for t in tasks])
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # FastAPI supports assigning .router.lifespan_context post-construction.
    app.router.lifespan_context = lifespan


# uvicorn ``meeting_api.__main__:app`` resolves this. Exposed LAZILY via PEP 562 so merely importing
# this module never wires SQLAlchemy/asyncpg/boto3 (NOT in the offline gate venv). The app + loops
# are constructed only when uvicorn touches ``__main__.app`` at boot; the loops start under the
# lifespan, once the event loop is running.
def __getattr__(name: str):
    if name == "app":
        return build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_production_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
