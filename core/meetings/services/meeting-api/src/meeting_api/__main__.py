"""``python -m meeting_api`` — the production meeting-api (P4 compose CMD).

Assembles the unified modular-monolith (``meeting_api.create_app``) with the REAL per-module
adapters (SQLAlchemy + redis + MinIO/S3 + httpx-runtime), then — per P4 — ALSO starts the
control-plane background loops alongside the HTTP app via the FastAPI lifespan:

  * **collector segment consumer** — drains the ``transcription_segments`` redis stream
    (``consume_segments`` → ``ingest`` → publish ``tc:…:mutable``) on a poll interval.
  * **db-writer** — the RESTORED parent flush loop (0.10 ``process_redis_to_postgres``): each tick
    moves immutable live segments from the redis hash ``meeting:{id}:segments`` into the
    ``transcriptions`` table (upsert on segment identity; redis trimmed only after the confirmed
    write) and drains the copilot's ``proc:meeting:{id}`` notes into ``meeting.data`` JSONB.
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


def _require_config(env: "os._Environ | dict | None" = None) -> None:
    """Fail-fast on missing required config (A4), driven by the config.v1 declaration (ADR-0026).

    ``config.v1.json`` (next to this module) declares every env key the service consumes; the
    vendored shared preflight raises ``ConfigError`` (a ``RuntimeError``) naming every missing
    *required-explicit* key — today ADMIN_TOKEN, which HS256-signs the MeetingToken every spawn
    mints (invocation.mint_meeting_token) AND the recordings-upload verifier checks; unset, the
    deploy would 500 every POST /bots, so it refuses to boot instead. Capability tri-states
    (stt · object_storage, incl. the STT live auth probe) are logged here and exposed on
    ``/health``; they never block boot.
    """
    from .config_preflight import preflight

    preflight(env)


def build_production_app():
    """Wire the unified meeting-api with the real adapters + the lifespan-driven loops."""
    _require_config()  # A4: refuse to boot a misconfigured deploy (no ADMIN_TOKEN → every spawn 500s).

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

    # Per-user webhook delivery (WebhookSink: SSRF-guard → event-filter → sign → POST → enqueue-retry).
    # httpx transport; failures route to the redis RetryQueue the background drain loop sweeps.
    # WH2: the transport is IP-PINNED — it re-resolves + re-validates the host at connect time and
    # dials the validated IP (preserving Host + TLS SNI), closing the DNS-rebinding TOCTOU window
    # between submit-time validate_webhook_url and the actual socket connect.
    from .webhooks import RetryQueue, WebhookSink
    from .webhooks.ssrf import build_pinned_transport

    async def _webhook_transport(url: str, body: bytes, headers: dict):
        async with httpx.AsyncClient(timeout=10.0, transport=build_pinned_transport()) as client:
            return await client.post(url, content=body, headers=headers)

    webhook_sink = WebhookSink(_webhook_transport, queue=RetryQueue(redis_client))

    # Completion finalization: when the lifecycle FSM lands on a terminal status the callback runs
    # this — flush the meeting's remaining redis segments to Postgres (threshold 0) + persist the
    # processed doc into meeting.data, so a finished meeting is durable IMMEDIATELY (not `whenever
    # the next db-writer tick happens to run`).
    from .collector.db_writer import finalize_meeting

    async def _transcript_finalizer(meeting_id: int) -> None:
        await finalize_meeting(redis_client, transcript_store, meeting_id)

    # Calendar-sync user edges (GET/POST /user/calendar/sync): the SAME one-user pass the
    # background sweep runs, on demand — paste-a-feed gets an immediate result instead of a
    # silent wait for the next tick (fail loud to the user). None-returns mean "no feed / sync
    # unavailable" and the route answers 404/503 accordingly.
    async def _calendar_sync_now(user_id: int):
        admin_api_url = (os.getenv("ADMIN_API_URL") or "").rstrip("/")
        internal_secret = os.getenv("INTERNAL_API_SECRET") or ""
        if not (admin_api_url and internal_secret):
            return None
        import json as _json

        from .calendar_sync import fetch_configs, run_user_sync, store_stamp

        configs = await fetch_configs(admin_api_url, internal_secret)
        cfg = next((c for c in configs or [] if c.get("user_id") == user_id), None)
        if cfg is None:
            return None

        async def _pub(uid, entry):
            frame = {"type": "meeting.status", "meeting_id": entry["id"],
                     "native": entry.get("native"), "status": entry.get("status"),
                     "when": entry.get("when")}
            try:
                await redis_client.publish(f"u:{uid}:meetings", _json.dumps(frame))
            except Exception:
                pass

        stamp = await run_user_sync(transcript_store, cfg, publish=_pub)
        await store_stamp(redis_client, user_id, stamp)
        return stamp

    async def _calendar_sync_status(user_id: int):
        from .calendar_sync import read_stamp
        return await read_stamp(redis_client, user_id)

    app = create_app(
        transcript_store=transcript_store,
        redis=segment_bus,
        meeting_repo=meeting_repo,
        runtime=runtime_client,
        recording_repo=recording_repo,
        storage=storage,
        token_secret=token_secret,
        # The user-stop route (DELETE /bots) publishes the bot's `leave` command on redis pub/sub.
        # redis.asyncio's client satisfies the CommandPublisher port directly (async publish()).
        command_publisher=redis_client,
        webhook_sink=webhook_sink,
        transcript_finalizer=_transcript_finalizer,
        calendar_sync_now=_calendar_sync_now,
        calendar_sync_status=_calendar_sync_status,
    )

    _attach_background_loops(app, transcript_store, segment_bus, redis_client, meeting_repo, runtime_client)
    return app


def _minio_endpoint_url() -> str:
    """Build an http(s) MinIO URL from MINIO_ENDPOINT (host:port) + MINIO_SECURE, mirroring 0.11."""
    endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    scheme = "https" if os.getenv("MINIO_SECURE", "false").lower() == "true" else "http"
    return f"{scheme}://{endpoint}"


def _attach_background_loops(app, transcript_store, segment_bus, redis_client, meeting_repo=None, runtime=None) -> None:
    """Register the FastAPI lifespan that starts/stops the control-plane poll loops."""
    from .collector.ingest import consume_segments

    seg_interval = float(os.getenv("SEGMENT_CONSUMER_INTERVAL", "0.5"))
    webhook_interval = float(os.getenv("WEBHOOK_DRAIN_INTERVAL", "5"))
    scheduler_interval = float(os.getenv("SCHEDULER_TICK_INTERVAL", "1"))
    # The db-writer cadence — the parent's BACKGROUND_TASK_INTERVAL (10s); either env name works.
    db_writer_interval = float(
        os.getenv("DB_WRITER_INTERVAL_S", os.getenv("BACKGROUND_TASK_INTERVAL", "10"))
    )
    # Stop-reconcile backstop: a meeting whose bot was told to leave but never sent its own terminal
    # callback would stay `stopping` forever. After a grace window, complete it through the same
    # lifecycle callback the bot uses — so the FSM, webhook, and ws status frame all fire identically.
    stop_grace = float(os.getenv("STOP_RECONCILE_GRACE_S", "45"))
    stop_interval = float(os.getenv("STOP_RECONCILE_INTERVAL_S", "15"))
    # GENERAL reconcile: ANY non-terminal status whose bot is gone (its row quiet past the grace) is
    # converged to a terminal state through the same lifecycle callback. `stopping` uses stop_grace
    # (a stop was requested); `active`/etc. use `active_grace`. The active-reap is ADDITIONALLY gated on
    # runtime WORKLOAD liveness (reconcile.py `_bot_workload_gone`): a meeting whose bot workload is still
    # alive is NEVER reaped, even past the grace — so a quiet-but-live (silent) bot is safe regardless of
    # this window. With that gate in place, 300s is a SANE default again (the 86400 env stopgap, which
    # only worked because it disabled the time-based reap entirely, is no longer needed).
    active_grace = float(os.getenv("RECONCILE_ACTIVE_GRACE_S", "300"))
    # Bounded untracked escalation (the zombie-loop fix): a meeting whose workload stays UNTRACKED
    # (runtime 404) CONTINUOUSLY past this window — no runtime re-adoption, no bot callback — is
    # presumed lost (runtime restart on the process backend / external removal) and advanced to
    # `failed` with the evidence note, instead of retrying an error + dead DELETE every sweep forever.
    untracked_grace = float(os.getenv("MEETING_UNTRACKED_GRACE_SEC", "600"))

    # #527: per-loop liveness heartbeats. A loop hung inside an await stops stamping, so /health can
    # SEE a dead consumer that would otherwise look alive (live WS keeps flowing on a SEPARATE path —
    # the 2026-04-26 silent-hang, found only by a user opening an empty transcript). Stored on
    # app.state so /health reads them; the raw redis client + stream/group let /health also report
    # collector-group lag (XINFO) — the diagnostic signal that existed but was exposed nowhere.
    import time as _time

    from .collector.ingest import CONSUMER_GROUP as _SEG_GROUP
    from .collector.ingest import STREAM_NAME as _SEG_STREAM

    ticks: dict[str, float] = {}
    app.state.pipeline_ticks = ticks
    app.state.pipeline_redis = redis_client
    app.state.pipeline_stream = _SEG_STREAM
    app.state.pipeline_group = _SEG_GROUP
    app.state.pipeline_tick_stale_s = float(os.getenv("PIPELINE_TICK_STALE_S", "120"))
    app.state.pipeline_lag_alarm = int(os.getenv("PIPELINE_LAG_ALARM", "500"))

    async def _segment_consumer_loop() -> None:
        # Drain the transcription_segments stream → persist + publish tc:…:mutable.
        while True:
            try:
                await consume_segments(transcript_store, segment_bus)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("segment consumer tick failed")
            ticks["segment-consumer"] = _time.monotonic()  # #527: alive this iteration
            await asyncio.sleep(seg_interval)

    async def _db_writer_loop() -> None:
        # The RESTORED parent db-writer (0.10 process_redis_to_postgres): each tick, flush every
        # active meeting's IMMUTABLE redis-hash segments into the transcriptions table (upsert on
        # (meeting_id, segment_id)) and drain its processed-notes stream into meeting.data JSONB.
        # Redis is trimmed only AFTER the confirmed durable write. Without this loop nothing ever
        # moved segments to Postgres — the transcriptions table stayed EMPTY and a redis eviction
        # was unrecoverable transcript loss (the 0.12 release blocker).
        from .collector.db_writer import db_writer_tick

        if not hasattr(transcript_store, "upsert_segments"):
            return  # a store without a durable sink (bare fake) — nothing to flush into
        while True:
            try:
                await db_writer_tick(redis_client, transcript_store)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("db-writer tick failed")
            ticks["db-writer"] = _time.monotonic()  # #527: alive this iteration
            await asyncio.sleep(db_writer_interval)

    async def _webhook_drain_loop() -> None:
        import httpx

        from .webhooks.retry import drain_retry_queue
        from .webhooks.ssrf import build_pinned_transport

        # The injected Transport: POST the signed envelope; return the response (its .status_code
        # drives the retry/permanent decision in retry._deliver_one). WH2: IP-pinned at connect
        # (re-resolve + re-validate + dial the validated IP) so a rebinding flip can't slip an
        # internal target into a retry sweep either.
        async def _transport(url: str, body: bytes, headers: dict):
            async with httpx.AsyncClient(timeout=10.0, transport=build_pinned_transport()) as client:
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

    async def _stop_reconcile_loop() -> None:
        # Complete meetings stuck in `stopping` past the grace window AND kill any orphan workload (CC6 /
        # ADR-0024) — through the importable reconcile sweep, reusing the SAME in-process lifecycle logic
        # so the FSM → persist → webhook → ws-publish path fires identically (no duplicate logic).
        if meeting_repo is None or not hasattr(meeting_repo, "list_stale_stopping"):
            return
        from .lifecycle.machine import TransitionSource as _TS
        from .lifecycle.reconcile import (
            reconcile_stale_nonterminal_sweep,
            reconcile_stale_stopping_sweep,
        )

        # Drive the sweep's synthetic terminals through the in-process lifecycle entry — NOT an httpx
        # POST to 127.0.0.1:PORT. The sweeps only ever post a TERMINAL status after their own evidence
        # gate (confirmed teardown / bounded untracked escalation), so they are a runtime-destroy-class
        # advance: `force_terminal_on_destroy=True` lets the terminal edge land even when the in-process
        # FSM record is a stale non-terminal state the DB already moved past (the loopback self-POST
        # 409'd on exactly that — `joining → completed` for a bot stopped before it reported active —
        # leaving the meeting `stopping` and the reaper re-DELETEing every tick forever).
        apply_lifecycle_event = app.state.apply_lifecycle_event

        async def _post_lifecycle(body: dict):
            status_code, _content = await apply_lifecycle_event(
                body,
                transition_source=_TS.RUNTIME_DESTROY,
                force_terminal_on_destroy=True,
            )
            return status_code

        # The general sweep (any stale non-terminal status whose bot is gone) subsumes the stale-
        # stopping sweep, but we keep the latter as the guaranteed orphan-kill backstop for `stopping`.
        has_general = hasattr(meeting_repo, "list_stale_nonterminal")
        while True:
            try:
                if has_general:
                    await reconcile_stale_nonterminal_sweep(
                        meeting_repo, runtime, _post_lifecycle,
                        stop_grace=stop_grace, active_grace=active_grace, log=log,
                        untracked_grace=untracked_grace,
                    )
                await reconcile_stale_stopping_sweep(
                    meeting_repo, runtime, _post_lifecycle, stop_grace=stop_grace, log=log,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stop-reconcile tick failed")
            await asyncio.sleep(stop_interval)

    # Auto-join: "scheduled" means the bot joins. The sweep spawns a bot for every scheduled row
    # whose data.scheduled_at arrived (lead window) and whose auto_join toggle is on, through the
    # SAME request_bot flow POST /bots runs — the claim/upgrade branch makes it idempotent. The
    # per-user spawn context (max-bots cap + webhook config the gateway would inject as headers)
    # is fetched from admin-api's internal edge; unset ADMIN_API_URL/INTERNAL_API_SECRET degrades
    # to uncapped spawns (the self-host default), an UNREACHABLE identity skips the tick (fail-closed).
    auto_join_interval = float(os.getenv("AUTO_JOIN_SWEEP_INTERVAL_S", "30"))
    auto_join_lead = float(os.getenv("AUTO_JOIN_LEAD_S", "60"))
    auto_join_grace = float(os.getenv("AUTO_JOIN_GRACE_S", "600"))
    auto_join_backoff = float(os.getenv("AUTO_JOIN_RETRY_BACKOFF_S", "300"))
    admin_api_url = (os.getenv("ADMIN_API_URL") or "").rstrip("/")
    internal_secret = os.getenv("INTERNAL_API_SECRET") or ""

    async def _auto_join_loop() -> None:
        if meeting_repo is None or runtime is None or not hasattr(meeting_repo, "list_scheduled_meetings"):
            return
        import json as _json

        import httpx

        from .bot_spawn.auto_join import auto_join_tick

        fetch_bot_context = None
        if admin_api_url and internal_secret:
            async def fetch_bot_context(user_id: int):
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        r = await client.get(
                            f"{admin_api_url}/internal/users/{user_id}/bot-context",
                            headers={"X-Internal-Secret": internal_secret},
                        )
                    if r.status_code != 200:
                        return None
                    body = r.json()
                    return body if isinstance(body, dict) else None
                except Exception:
                    return None  # identity unreachable → the sweep skips the row this tick

        async def publish_status(*, user_id, meeting_id, native_id, status, when):
            frame = {"type": "meeting.status", "meeting_id": meeting_id,
                     "native": native_id, "status": status, "when": when}
            try:
                await redis_client.publish(f"u:{user_id}:meetings", _json.dumps(frame))
            except Exception:
                pass  # best-effort, like the collector's publish

        while True:
            try:
                await auto_join_tick(
                    meeting_repo, runtime,
                    fetch_bot_context=fetch_bot_context,
                    publish_status=publish_status,
                    lead_s=auto_join_lead, grace_s=auto_join_grace,
                    retry_backoff_s=auto_join_backoff,
                    token_secret=os.getenv("ADMIN_TOKEN") or None,
                    redis_url=os.getenv("REDIS_URL"),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("auto-join tick failed")
            await asyncio.sleep(auto_join_interval)

    # Calendar sync: each sweep discovers every user with a connected ICS feed (admin-api internal
    # edge), fetches it over the SSRF-pinned transport, and upserts planned meetings (one row per
    # calendar UID — next occurrence only). Per-user try/except: one bad feed never stalls the
    # sweep. Unset ADMIN_API_URL/INTERNAL_API_SECRET → no-op (capability degrade, not boot-fail).
    calendar_interval = float(os.getenv("CALENDAR_SYNC_INTERVAL_S", "300"))

    async def _cal_publish(user_id, entry):
        import json as _json
        frame = {"type": "meeting.status", "meeting_id": entry["id"],
                 "native": entry.get("native"), "status": entry.get("status"),
                 "when": entry.get("when")}
        try:
            await redis_client.publish(f"u:{user_id}:meetings", _json.dumps(frame))
        except Exception:
            pass

    async def _calendar_sync_loop() -> None:
        if not (admin_api_url and internal_secret):
            return
        if not hasattr(transcript_store, "create_planned_meeting"):
            return
        from .calendar_sync import fetch_configs, run_user_sync, store_stamp

        while True:
            try:
                configs = await fetch_configs(admin_api_url, internal_secret)
                for cfg in configs or []:
                    try:  # one bad feed never stalls the sweep
                        stamp = await run_user_sync(transcript_store, cfg, publish=_cal_publish)
                    except Exception:
                        log.exception("calendar sync failed for user %s", cfg.get("user_id"))
                        continue
                    await store_stamp(redis_client, cfg["user_id"], stamp)
            except Exception:
                log.exception("calendar sync tick failed")
            await asyncio.sleep(calendar_interval)

    @asynccontextmanager
    async def lifespan(_app):
        tasks = [
            asyncio.create_task(_segment_consumer_loop(), name="segment-consumer"),
            asyncio.create_task(_db_writer_loop(), name="db-writer"),
            asyncio.create_task(_webhook_drain_loop(), name="webhook-drain"),
            asyncio.create_task(_scheduler_tick_loop(), name="scheduler-tick"),
            asyncio.create_task(_stop_reconcile_loop(), name="stop-reconcile"),
            asyncio.create_task(_auto_join_loop(), name="auto-join"),
            asyncio.create_task(_calendar_sync_loop(), name="calendar-sync"),
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
