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

Each loop is a single-tick function the eval drives explicitly; here the entrypoint wraps it in the
``while True: tick; sleep`` poll the deployment uses. uvicorn-target: ``uvicorn meeting_api.__main__:app``.

#637 — at ``meetingApi.replicaCount>1`` every replica starts these same loops, so each live tick body
is wrapped in a per-loop Postgres advisory lock (``sweeps.single_flight``): the real work runs once per
interval, not once per replica. ``segment-consumer`` is intentionally left unguarded (Redis competing
consumer). The former ``scheduler-tick`` loop was dead code (``app.state.scheduler`` was never wired —
the ``Scheduler`` in ``scheduling/`` is an eval-only engine) and has been removed.
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
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from . import create_app
    from .db import build_engine
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

    engine = build_engine(database_url)  # #635: env-steered pool (pool_pre_ping preserved in the helper)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # #528: harden the shared Redis client so a Redis outage surfaces as a bounded exception the
    # per-tick handlers already catch — not a hung/zombie socket that only a restart heals. Same
    # kwargs as the gateway (adapters.py): socket_timeout bounds every await, keepalive + health
    # checks detect a dead peer, connect timeout bounds re-dial, retry_on_timeout re-issues once.
    redis_client = aioredis.from_url(
        redis_url, decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, socket_keepalive=True,
        health_check_interval=30, retry_on_timeout=True,
    )

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

    # #841: the per-user delivery ledger — the queryable record GET /webhooks/deliveries serves.
    # A per-user capped Redis list; the lifecycle callback records each delivery outcome so the
    # dashboard's Delivery History reflects real deliveries, not just its own Test button.
    from .webhooks import RedisDeliveryLedger

    delivery_ledger = RedisDeliveryLedger(redis_client)

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
        delivery_ledger=delivery_ledger,
        transcript_finalizer=_transcript_finalizer,
        calendar_sync_now=_calendar_sync_now,
        calendar_sync_status=_calendar_sync_status,
    )

    _attach_background_loops(
        app, transcript_store, segment_bus, redis_client, meeting_repo, runtime_client,
        session_factory=session_factory,
    )
    return app


def _minio_endpoint_url() -> str:
    """Build an http(s) MinIO URL from MINIO_ENDPOINT (host:port) + MINIO_SECURE, mirroring 0.11."""
    endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    scheme = "https" if os.getenv("MINIO_SECURE", "false").lower() == "true" else "http"
    return f"{scheme}://{endpoint}"


def _attach_background_loops(
    app, transcript_store, segment_bus, redis_client, meeting_repo=None, runtime=None,
    session_factory=None,
) -> None:
    """Register the FastAPI lifespan that starts/stops the control-plane poll loops.

    #637 — single-flight sweeps: at ``replicaCount>1`` every replica runs these same loops, so each
    live tick body is wrapped in a per-loop Postgres advisory lock (``_guarded``) — the real work runs
    once per interval, not once per replica. With no ``session_factory`` (Lite / a store without PG)
    the guard degrades to run-the-tick, so single-replica behavior is unchanged. ``segment-consumer``
    is DELIBERATELY left unguarded — it is a Redis competing consumer (``XREADGROUP … ">"``) whose
    single-delivery is already exact, and guarding it would needlessly serialize the replicas' reads.
    """
    from .collector.ingest import RECLAIM_MIN_IDLE_MS, consume_segments, reclaim_segments
    from .sweeps.single_flight import PgAdvisoryLock, run_single_flight, sweep_lock_key

    # One shared advisory-lock backend across the guarded loops (each keyed by its own loop name).
    # None when there is no Postgres session factory → the guard runs every tick (Lite single-replica).
    sweep_lock = PgAdvisoryLock(session_factory) if session_factory is not None else None

    async def _guarded(loop_name: str, body):
        """Run ``body`` (a zero-arg coroutine fn) at most once per interval across replicas."""
        return await run_single_flight(sweep_lock, sweep_lock_key(loop_name), body)

    seg_interval = float(os.getenv("SEGMENT_CONSUMER_INTERVAL", "0.5"))
    # #636: orphan-reclaim cadence — fold a bounded XAUTOCLAIM into the consumer loop every N ticks
    # (default 120 ⇒ ~60s at the 0.5s tick), so a crashed replica's un-acked batch is picked up by a
    # survivor without a dedicated loop (no second /health heartbeat to maintain). The min-idle gate
    # (RECLAIM_MIN_IDLE_MS) ensures a live peer's in-flight batch is never stolen.
    seg_reclaim_every = max(1, int(os.getenv("SEGMENT_RECLAIM_EVERY_N_TICKS", "120")))
    webhook_interval = float(os.getenv("WEBHOOK_DRAIN_INTERVAL", "5"))
    # The db-writer cadence — the parent's BACKGROUND_TASK_INTERVAL (10s); either env name works.
    db_writer_interval = float(
        os.getenv("DB_WRITER_INTERVAL_S", os.getenv("BACKGROUND_TASK_INTERVAL", "10"))
    )
    # #893: the self-healing keyspace SCAN is OFF the 10s hot path. The active_meetings set is
    # authoritative in steady state, so a tick sweeps the set alone; the O(keyspace) scan that
    # self-heals a set/hash divergence runs only on startup + every this-many seconds. The per-tick
    # scan saturated redis and starved /health, restarting healthy pods.
    db_writer_reconcile_interval = float(os.getenv("DB_WRITER_RECONCILE_INTERVAL_S", "300"))
    # Stop-reconcile backstop: a meeting whose bot was told to leave but never sent its own terminal
    # callback would stay `stopping` forever. After a grace window, complete it through the same
    # lifecycle callback the bot uses — so the FSM, webhook, and ws status frame all fire identically.
    stop_grace = float(os.getenv("STOP_RECONCILE_GRACE_S", "45"))
    stop_interval = float(os.getenv("STOP_RECONCILE_INTERVAL_S", "15"))
    # GENERAL reconcile: ANY non-terminal status whose bot is gone (its row quiet past the grace) is
    # converged to a terminal state through the same lifecycle callback. `stopping` uses stop_grace
    # (a stop was requested); `active`/etc. use `active_grace`. The reap is ADDITIONALLY gated on
    # runtime WORKLOAD liveness (reconcile.py `_probe_bot_workload`): a meeting whose bot workload is still
    # alive is NEVER reaped, even past the grace — so a quiet-but-live (silent) bot is safe regardless of
    # this window. With that gate in place, 300s is a SANE default again (the 86400 env stopgap, which
    # only worked because it disabled the time-based reap entirely, is no longer needed).
    active_grace = float(os.getenv("RECONCILE_ACTIVE_GRACE_S", "300"))
    # A bot that has NOT yet reached the meeting gets its OWN, longer window (#862). The control plane
    # hands every spawn a lobby budget (`waitingRoomTimeout`) and the bot reports `awaiting_admission`
    # exactly ONCE before polling silently for the rest of it — so a HEALTHY bot waiting to be let in
    # is indistinguishable from a dead one for the whole wait, and OUR patience has to outlast the
    # deadline WE issued. Hence a floor DERIVED from that budget (+60s of headroom for the bot's own
    # terminal callback to land) rather than a second number free to drift under it. The liveness
    # gate is the primary defence; this is the belt for an inconclusive probe.
    from .lifecycle.reconcile import default_preactive_grace

    preactive_grace = float(
        os.getenv("RECONCILE_PREACTIVE_GRACE_S", str(default_preactive_grace()))
    )
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
    # #636: group PEL depth (delivered-but-un-acked) alarm. Steady state acks within a tick, so a
    # SUSTAINED total above this threshold is an orphaned batch (a crashed replica's un-reclaimed
    # PEL) → /health degrades + 503. Default headroom over one in-flight batch (count=10 default).
    app.state.pipeline_pending_alarm = int(os.getenv("PIPELINE_PENDING_ALARM", "100"))

    async def _segment_consumer_loop() -> None:
        # Drain the transcription_segments stream → persist + publish tc:…:mutable.
        tick_n = 0
        while True:
            try:
                await consume_segments(transcript_store, segment_bus)
                # #636: every N ticks, reclaim any ORPHANED (crashed-replica) un-acked batch idle
                # past RECLAIM_MIN_IDLE_MS and drain it through the same ingest→ack path. Bounded to
                # one XAUTOCLAIM per pass (its cursor continues next time) — never a hang surface.
                tick_n += 1
                if tick_n % seg_reclaim_every == 0:
                    await reclaim_segments(
                        transcript_store, segment_bus, min_idle_ms=RECLAIM_MIN_IDLE_MS
                    )
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

        # #893: reconcile (the O(keyspace) self-healing scan) only on the FIRST tick — catch any
        # mid-upgrade orphan hash on boot — then at most every db_writer_reconcile_interval seconds.
        # Every other tick sweeps the authoritative active_meetings set alone, no scan.
        last_reconcile = [0.0]  # 0 ⇒ the first tick reconciles

        async def _tick(reconcile: bool):
            await db_writer_tick(redis_client, transcript_store, reconcile=reconcile)

        while True:
            now = _time.monotonic()
            do_reconcile = (now - last_reconcile[0]) >= db_writer_reconcile_interval
            try:
                await _guarded("db-writer", lambda: _tick(do_reconcile))  # #637: one flush/interval
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("db-writer tick failed")
            if do_reconcile:
                last_reconcile[0] = now  # bound the scan cadence per replica, run or guarded-skip
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

        async def _tick():
            await drain_retry_queue(redis_client, _transport)

        while True:
            try:
                await _guarded("webhook-drain", _tick)  # #637: one drain sweep per interval
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("webhook retry-drain tick failed")
            await asyncio.sleep(webhook_interval)

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

        async def _tick():
            if has_general:
                await reconcile_stale_nonterminal_sweep(
                    meeting_repo, runtime, _post_lifecycle,
                    stop_grace=stop_grace, active_grace=active_grace, log=log,
                    preactive_grace=preactive_grace, untracked_grace=untracked_grace,
                )
            await reconcile_stale_stopping_sweep(
                meeting_repo, runtime, _post_lifecycle, stop_grace=stop_grace, log=log,
            )

        while True:
            try:
                await _guarded("stop-reconcile", _tick)  # #637: one reconcile pass per interval
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stop-reconcile tick failed")
            await asyncio.sleep(stop_interval)

    # Auto-join: "scheduled" means the bot joins. The sweep spawns a bot for every scheduled row
    # whose data.scheduled_at arrived (lead window) and whose auto_join toggle is on, through the
    # SAME request_bot flow POST /bots runs — the claim/upgrade branch makes it idempotent. The
    # per-user spawn context (max-bots cap + webhook config the gateway would inject as headers)
    # is fetched from admin-api's internal edge. Fail-closed: unset ADMIN_API_URL/INTERNAL_API_SECRET
    # makes the cap unresolvable, so the sweep REFUSES to spawn (AUTO_JOIN_ALLOW_UNCAPPED=1 is the
    # explicit self-host opt-in); an UNREACHABLE identity likewise skips the tick.
    auto_join_interval = float(os.getenv("AUTO_JOIN_SWEEP_INTERVAL_S", "30"))
    auto_join_lead = float(os.getenv("AUTO_JOIN_LEAD_S", "60"))
    auto_join_grace = float(os.getenv("AUTO_JOIN_GRACE_S", "600"))
    auto_join_backoff = float(os.getenv("AUTO_JOIN_RETRY_BACKOFF_S", "300"))
    admin_api_url = (os.getenv("ADMIN_API_URL") or "").rstrip("/")
    internal_secret = os.getenv("INTERNAL_API_SECRET") or ""
    # Fail-closed default: with no admin edge configured the per-user cap is unresolvable, so the
    # sweep refuses to spawn rather than spawn uncapped. AUTO_JOIN_ALLOW_UNCAPPED=1 is the explicit
    # self-host opt-in that chooses the uncapped mode (never defaulted).
    from .bot_spawn.env_flags import env_flag
    auto_join_allow_uncapped = env_flag("AUTO_JOIN_ALLOW_UNCAPPED", default=False)

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

        async def _tick():
            await auto_join_tick(
                meeting_repo, runtime,
                fetch_bot_context=fetch_bot_context,
                publish_status=publish_status,
                lead_s=auto_join_lead, grace_s=auto_join_grace,
                retry_backoff_s=auto_join_backoff,
                token_secret=os.getenv("ADMIN_TOKEN") or None,
                redis_url=os.getenv("REDIS_URL"),
                allow_uncapped=auto_join_allow_uncapped,
            )

        while True:
            try:
                # #637: one sweep per interval — the per-user spawn stays single-flighted by its own
                # xact lock, but this also single-flights the doubled admin-api bot-context fetch.
                await _guarded("auto-join", _tick)
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

        async def _tick():
            configs = await fetch_configs(admin_api_url, internal_secret)
            for cfg in configs or []:
                try:  # one bad feed never stalls the sweep
                    stamp = await run_user_sync(transcript_store, cfg, publish=_cal_publish)
                except Exception:
                    log.exception("calendar sync failed for user %s", cfg.get("user_id"))
                    continue
                await store_stamp(redis_client, cfg["user_id"], stamp)

        while True:
            try:
                # #637 (load-bearing): the external ICS/Google fetch has no other dedup, so at
                # replicaCount>1 it doubled outbound provider requests every interval. Single-flight
                # it — the replica that loses the advisory lock skips the whole fetch this tick.
                await _guarded("calendar-sync", _tick)
            except Exception:
                log.exception("calendar sync tick failed")
            await asyncio.sleep(calendar_interval)

    @asynccontextmanager
    async def lifespan(_app):
        tasks = [
            asyncio.create_task(_segment_consumer_loop(), name="segment-consumer"),
            asyncio.create_task(_db_writer_loop(), name="db-writer"),
            asyncio.create_task(_webhook_drain_loop(), name="webhook-drain"),
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
