"""The ``POST /bots`` flow — port of the parent ``meetings.request_bot`` (P2 core + P3 control-plane).

P3 added (all behind the same injected ports, so the flow still runs offline):
  * **continue_meeting** (P3c) — when the prior meeting for (platform, native_id) is TERMINAL,
    reuse the SAME meeting row + create a NEW ``MeetingSession`` instead of a fresh meeting (the
    409 only fires for a CONCURRENT, still-active prior meeting). Transcripts/recordings stay keyed
    by the meeting row, so a continued run preserves them.
  * **max-bots** (P3e) — a per-user concurrency pre-check: count the user's ACTIVE bots (excluding
    infra ``browser_session``) and reject the N+1th with 429 BEFORE spawning; the runtime kernel's
    own ``QuotaExceeded`` remains the defense-in-depth backstop.

The flow (parent ``meetings.py`` lines ~1010-1403, reduced to the standard-bot branch):
  1. construct the meeting URL (or use the supplied one),
  2. dedup — 409 if the user already has an active/requested meeting for (platform, native_id),
  2b. max-bots — 429 if the user is at their per-user concurrency cap (P3e),
  2c. continue_meeting — reuse a TERMINAL prior meeting row + add a session (P3c),
  3. insert the ``Meeting`` row (status ``requested``) → meeting_id (unless reusing one),
  4. mint the MeetingToken + build the ``invocation.v1`` invocation (BOT_CONFIG),
  5. spawn the meeting-bot workload over ``runtime.v1`` (``RuntimeClient.create_workload``),
  6. eager-create the ``MeetingSession`` keyed by the bot's ``connectionId`` (== session_uid),
  7. write the kernel workload id back as ``bot_container_id``,
  8. return the ``api.v1`` ``MeetingResponse`` (now listing its ``sessions``).
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from ..obs import log_event
from .env_flags import env_flag
from .invocation import build_invocation, build_workload_spec, mint_meeting_token
from .ports import (
    AuthSessionBusy,
    AuthSessionNotConfigured,
    DuplicateMeeting,
    MaxBotsExceeded,
    MeetingRepo,
    QuotaExceeded,
    RuntimeClient,
    SpawnFailed,
    TranscriptionNotConfigured,
)

# Re-exported here (defined in ports.py to avoid an adapters→service circular import) so callers that
# already do ``from .service import DuplicateMeeting`` (the router) keep working.
__all__ = ["request_bot", "construct_meeting_url", "DuplicateMeeting"]

# Non-terminal statuses (parent's active set) — a prior meeting in one of these blocks a new spawn.
_ACTIVE_STATUSES = ("requested", "joining", "awaiting_admission", "active", "stopping")
_TERMINAL_STATUSES = ("completed", "failed")

# Construct-URL templates per platform (the parent's ``Platform.construct_meeting_url``, core set).
# NO jitsi template: a jitsi room name is scoped to a DEPLOYMENT (meet.jit.si is only the public
# one), so constructing a URL from the bare id would silently join the public room of that name —
# the wrong meeting, on someone else's deployment. jitsi callers pass an explicit ``meeting_url``
# (same passthrough zoom uses); the UI, MCP, and calendar paths all carry it.
_URL_TEMPLATES = {
    "google_meet": "https://meet.google.com/{native_meeting_id}",
    "teams": "https://teams.microsoft.com/l/meetup-join/{native_meeting_id}",
}


async def _resolve_transcription_backend(user_id: int) -> dict:
    """The Settings-configured transcription backend for this spawn: admin-api's bot-context
    resolves user pref > platform setting into ``{"transcription": {url, token}}``. Best-effort
    by contract — identity unreachable / unset ADMIN_API_URL degrades to the process env (the
    pre-Settings behaviour), never blocks a spawn. The token crosses ONLY this internal hop."""
    admin_api_url = (os.getenv("ADMIN_API_URL") or "").rstrip("/")
    internal_secret = os.getenv("INTERNAL_API_SECRET") or ""
    if not (admin_api_url and internal_secret):
        return {}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{admin_api_url}/internal/users/{user_id}/bot-context",
                headers={"X-Internal-Secret": internal_secret},
            )
        if r.status_code != 200:
            return {}
        body = r.json()
        transcription = body.get("transcription") if isinstance(body, dict) else None
        return transcription if isinstance(transcription, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def construct_meeting_url(platform: str, native_meeting_id: str) -> Optional[str]:
    """Best-effort meeting URL for ``(platform, native_id)`` (zoom needs more than the id →
    None; the caller may pass an explicit ``meeting_url`` instead)."""
    tmpl = _URL_TEMPLATES.get(platform)
    return tmpl.format(native_meeting_id=native_meeting_id) if tmpl else None


def _meeting_response(row: dict, *, sessions: Optional[list] = None) -> dict:
    """Shape a meeting row into an ``api.v1`` MeetingResponse-conforming dict (required:
    id, user_id, status, bot_container_id, start_time, end_time, created_at, updated_at).

    P3c — when ``sessions`` is supplied, the response also lists the meeting's ``session_uid``s
    (the N bots that ran against this meeting row). This rides in ``data.sessions`` (the api.v1
    ``data`` field is an open object — see the contract note in the bot_spawn README) so the
    SEALED ``MeetingResponse`` schema is honoured without an edit; a public typed ``sessions``
    field would need a ``vN+1`` (flagged)."""
    data = dict(row.get("data")) if isinstance(row.get("data"), dict) else {}
    if sessions is not None:
        data["sessions"] = list(sessions)
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "platform": row.get("platform"),
        "native_meeting_id": row.get("native_meeting_id") or row.get("platform_specific_id"),
        "constructed_meeting_url": data.get("constructed_meeting_url"),
        "status": row.get("status", "requested"),
        "bot_container_id": row.get("bot_container_id"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "completion_reason": data.get("completion_reason"),
        "failure_stage": data.get("failure_stage"),
        "data": data,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def request_bot(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    bot_name: Optional[str] = None,
    passcode: Optional[str] = None,
    meeting_url: Optional[str] = None,
    language: Optional[str] = None,
    task: Optional[str] = None,
    transcription_tier: str = "realtime",
    recording_enabled: bool = False,
    transcribe_enabled: bool = True,
    continue_meeting: bool = False,
    max_concurrent: Optional[int] = None,
    redis_url: Optional[str] = None,
    meeting_api_url: Optional[str] = None,
    internal_secret: Optional[str] = None,
    token_secret: Optional[str] = None,
    # Per-user webhook config (the gateway forwards it from identity's /internal/validate). Persisted
    # into meeting.data so the lifecycle callback delivers status_change events with no users-table read.
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    webhook_events: Optional[dict] = None,
) -> dict:
    """Run the spawn flow and return a MeetingResponse-shaped dict.

    Raises ``DuplicateMeeting`` (409), ``MaxBotsExceeded`` / ``QuotaExceeded`` (429), or
    ``SpawnFailed`` (502/failed).

    ``continue_meeting`` (P3c): if the prior meeting for (platform, native_id) is TERMINAL, reuse
    that row + add a new session instead of creating a fresh meeting. ``max_concurrent`` (P3e): the
    per-user cap — the spawn is rejected if the user already has that many ACTIVE bots. A cap
    ``<= 0`` means the quota is DEPLETED (every spawn rejected) — 0 is never "unlimited"; ``None``
    means no cap was provided, so no pre-check.
    """
    # 1. URL.
    constructed_url = meeting_url or construct_meeting_url(platform, native_meeting_id)

    # 1b. Resolve the transcription backend and gate BEFORE any DB write (C1, reorder not
    #     duplicate): the old router gate refused pre-insert; resolving here keeps that property —
    #     a refused spawn must not leave an orphaned `requested` meeting row (whose retry after
    #     fixing config would then 409 on the dedup guard). STT creds the bot transcribes with —
    #     the process env is the bottom fallback; a configured backend from Settings (user pref >
    #     platform setting, resolved by admin-api's bot-context) overrides it per spawn. The
    #     resolved values flow down unchanged to the invocation build. Note: config.v1's `stt`
    #     capability tri-state still drives boot preflight + /health; the spawn path trusts THIS
    #     resolver instead (issue #502 C1) because Settings-configured STT is invisible to the
    #     env-only capability check.
    transcription_service_url = os.getenv("TRANSCRIPTION_SERVICE_URL") or None
    transcription_service_token = os.getenv("TRANSCRIPTION_SERVICE_TOKEN") or None
    transcription_model = os.getenv("TRANSCRIPTION_MODEL") or None
    configured = await _resolve_transcription_backend(user_id)
    if configured.get("url"):
        transcription_service_url = configured["url"]
        # A configured backend's token replaces the env token even when empty — the env token
        # belongs to the ENV backend, never to a user-supplied endpoint. Same rule for the
        # model id: the env model names the ENV backend's served model, so a configured
        # backend carries its own (unset → the client's whisper-1 default).
        transcription_service_token = configured.get("token") or None
        transcription_model = configured.get("model") or None
    if transcribe_enabled and not transcription_service_url:
        raise TranscriptionNotConfigured(
            "no transcription backend configured — set it in Settings or environment variables "
            "TRANSCRIPTION_SERVICE_URL + TRANSCRIPTION_SERVICE_TOKEN"
        )

    # 1c. Authenticated-bot mode (#724, deployment-scoped knob — Q1-A): when BOT_AUTHENTICATED is
    #     set, EVERY spawn carries the sealed invocation.v1 auth block, so the bot restores the
    #     deployment's provisioned browser session (`make login`) and joins signed-in. Config is
    #     gated loud BEFORE any DB write (the TranscriptionNotConfigured precedent) — a half-
    #     configured knob must never spawn a bot that silently joins anonymous. Env vocabulary
    #     matches the provisioning CLI: BOT_USERDATA_S3_PATH + BOT_S3_{ENDPOINT,BUCKET,ACCESS_KEY,
    #     SECRET_KEY} (scoped userdata credentials — never the deployment's admin S3 creds; they
    #     ride the invocation env into the bot container, so their blast radius must stay the
    #     userdata prefix).
    authenticated = env_flag("BOT_AUTHENTICATED", False)
    auth_userdata_path: Optional[str] = None
    auth_s3: dict[str, Optional[str]] = {}
    if authenticated:
        auth_userdata_path = os.getenv("BOT_USERDATA_S3_PATH") or None
        auth_s3 = {
            "s3_endpoint": os.getenv("BOT_S3_ENDPOINT") or None,
            "s3_bucket": os.getenv("BOT_S3_BUCKET") or None,
            "s3_access_key": os.getenv("BOT_S3_ACCESS_KEY") or None,
            "s3_secret_key": os.getenv("BOT_S3_SECRET_KEY") or None,
        }
        if not (auth_userdata_path and auth_s3["s3_endpoint"] and auth_s3["s3_bucket"]):
            raise AuthSessionNotConfigured(
                "BOT_AUTHENTICATED is set but the userdata store is incomplete — set "
                "BOT_USERDATA_S3_PATH + BOT_S3_ENDPOINT + BOT_S3_BUCKET (and scoped "
                "BOT_S3_ACCESS_KEY/BOT_S3_SECRET_KEY); provision the session with `make login`"
            )

    # 2c. continue_meeting (P3c): reuse a TERMINAL prior meeting row if asked. The reused row keeps
    #     its id (so its transcripts/recordings survive); a fresh session is appended below. This read
    #     stays a plain query — the reused-row path reopens an existing terminal row (no NEW active row
    #     is inserted), so it is not part of the dedup/cap TOCTOU window.
    reused_row: Optional[dict] = None
    if continue_meeting:
        latest = await repo.find_latest(user_id, platform, native_meeting_id)
        if latest and latest.get("status") in _TERMINAL_STATUSES:
            reused_row = latest

    # 2+2b+3. Dedup + max-bots cap + INSERT, made ATOMIC (ROB1/ROB2). Replaces the old read-check-
    #     then-act sequence (find_active → count_active_bots → create_meeting), whose three separate
    #     transactions opened a TOCTOU window: under concurrent POST /bots, every coroutine passed the
    #     pre-checks before any inserted its `requested` row → over-provision past the cap / double-
    #     spawn one meeting. create_meeting_guarded does dedup + cap + insert in ONE transaction (the
    #     real adapter serializes per-user with a pg advisory lock + a unique partial index backstop;
    #     the fake has no await between the check and the insert). The continue_meeting (reused-
    #     terminal-row) path reopens an existing row and is unchanged.
    # 2d. Per-identity serialization (#725 C2): one stored session = one live bot. Refuse a
    #     second concurrent authenticated spawn against the same userdata path with a typed 409
    #     naming the conflicting meeting. Control-plane pre-check against the tracked active set
    #     (the issue's default); it runs just before the row insert, so the remaining window is a
    #     single request interleaving — the storage-side lock fork stays available if a multi-
    #     replica deployment ever witnesses it.
    if authenticated and auth_userdata_path:
        conflict = await repo.find_active_by_userdata(auth_userdata_path)
        if conflict is not None and (reused_row is None or conflict["id"] != reused_row["id"]):
            log_event(
                "bot_spawn_auth_session_busy", audience="user", level="warning",
                span="bots.create", user_id=user_id,
                fields={"conflicting_meeting_id": conflict["id"]},
            )
            raise AuthSessionBusy(conflict["id"], auth_userdata_path)

    if reused_row is not None:
        # continue_meeting reopens an EXISTING terminal row (no new active row inserted), so it is not
        # part of the fresh-insert TOCTOU window — but the per-user cap still applies (a continued run
        # is an active bot). Keep the original pre-check here, excluding the row being reopened from the
        # count, to preserve the P3e semantics (test_max_bots.test_continue_meeting_session_counts_against_cap).
        if max_concurrent is not None:
            # cap <= 0 = depleted: reject without counting (0 >= cap holds for any cap <= 0).
            active = 0
            if max_concurrent > 0:
                active = await repo.count_active_bots(
                    user_id=user_id, exclude_meeting_id=reused_row["id"],
                )
            if active >= max_concurrent:
                log_event(
                    "bot_spawn_max_bots_exceeded", audience="user", level="warning",
                    span="bots.create", user_id=user_id,
                    fields={"active": active, "cap": max_concurrent},
                )
                raise MaxBotsExceeded(user_id, max_concurrent)
        row = await repo.reopen_meeting(meeting_id=reused_row["id"])
    else:
        meeting_data: dict[str, Any] = {}
        if constructed_url:
            meeting_data["constructed_meeting_url"] = constructed_url
        meeting_data["transcribe_enabled"] = transcribe_enabled
        meeting_data["recording_enabled"] = recording_enabled
        # The serialization key for authenticated spawns — find_active_by_userdata matches on it.
        if authenticated and auth_userdata_path:
            meeting_data["auth_userdata_path"] = auth_userdata_path
        # Per-user webhook config carried on the meeting (delivered by the lifecycle callback). These
        # are stripped from any outbound meeting projection (webhooks.delivery._INTERNAL_DATA_KEYS).
        if webhook_url:
            meeting_data["webhook_url"] = webhook_url
            if webhook_secret:
                meeting_data["webhook_secret"] = webhook_secret
            if webhook_events:
                meeting_data["webhook_events"] = webhook_events
        try:
            row = await repo.create_meeting_guarded(
                user_id=user_id,
                platform=platform,
                native_meeting_id=native_meeting_id,
                data=meeting_data,
                max_concurrent=max_concurrent,
            )
        except MaxBotsExceeded:
            log_event(
                "bot_spawn_max_bots_exceeded", audience="user", level="warning",
                span="bots.create", user_id=user_id,
                fields={"cap": max_concurrent},
            )
            raise
    meeting_id = row["id"]

    # 4. MeetingToken + invocation. connection_id IS the session_uid (parent's connectionId).
    connection_id = str(uuid.uuid4())
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    meeting_api_url = meeting_api_url or os.getenv("MEETING_API_URL", "http://meeting-api:8080")
    internal_secret = internal_secret if internal_secret is not None else os.getenv(
        "INTERNAL_API_SECRET"
    )
    # STT creds were resolved and gated at step 1b (before the meeting-row write); the resolved
    # transcription_service_url/token/model flow into the invocation below. Without either the bot
    # joins + captures but cannot transcribe — None-safe: omitted from the invocation when unset
    # (transcribe still gated by transcribe_enabled, which step 1b refuses when unresolvable).
    # Token must outlive the bot's max active time (default 4h, see bot deriveMaxActiveMs) or
    # transcription dies mid-meeting when the JWT expires. Default 5h; override per deployment.
    token_ttl_seconds = int(os.getenv("MEETING_TOKEN_TTL_SECONDS") or 18000)
    token = mint_meeting_token(
        meeting_id, user_id, platform, native_meeting_id, secret=token_secret, ttl_seconds=token_ttl_seconds
    )
    invocation = build_invocation(
        meeting_id=meeting_id,
        platform=platform,
        meeting_url=constructed_url,
        bot_name=bot_name or f"VexaBot-{uuid.uuid4().hex[:6]}",
        passcode=passcode,
        token=token,
        native_meeting_id=native_meeting_id,
        connection_id=connection_id,
        language=language,
        task=task,
        transcription_tier=transcription_tier,
        redis_url=redis_url,
        meeting_api_callback_url=f"{meeting_api_url}/bots/internal/callback/lifecycle",
        internal_secret=internal_secret,
        transcribe_enabled=transcribe_enabled,
        transcription_service_url=transcription_service_url,
        transcription_service_token=transcription_service_token,
        transcription_model=transcription_model,
        recording_enabled=recording_enabled,
        capture_modes=(["audio", "video"] if recording_enabled else None),
        recording_upload_url=f"{meeting_api_url}/internal/recordings/upload",
        authenticated=True if authenticated else None,
        userdata_s3_path=auth_userdata_path,
        s3_endpoint=auth_s3.get("s3_endpoint"),
        s3_bucket=auth_s3.get("s3_bucket"),
        s3_access_key=auth_s3.get("s3_access_key"),
        s3_secret_key=auth_s3.get("s3_secret_key"),
        # A human-in-the-loop dashboard join needs a forgiving lobby window so a late admit does not
        # fail the meeting; everyoneLeftTimeout matches the O6 config.
        automatic_leave={"waitingRoomTimeout": 600000, "everyoneLeftTimeout": 900000},
    )

    # 5. Spawn over runtime.v1.
    spec = build_workload_spec(
        workload_id=f"mtg-{meeting_id}-{connection_id[:8]}",
        invocation=invocation,
        callback_url=f"{meeting_api_url}/runtime/callback",
    )
    try:
        result = await runtime.create_workload(spec)
    except QuotaExceeded:
        log_event(
            "bot_spawn_quota_exceeded", audience="user", level="warning",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise
    except SpawnFailed:
        log_event(
            "bot_spawn_failed", audience="system", level="error",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise

    workload_id = result.get("workloadId") or result.get("name") or spec["workloadId"]

    # 6+7. Eager-create the MeetingSession (connectionId == session_uid) + write the kernel workload id
    #      back as bot_container_id. The workload is ALREADY running, so a failure here would orphan it
    #      (a live bot with no session row to resolve its uploads, the meeting stuck `requested`) —
    #      ROB3. Wrap both DB writes: on failure, tear the just-created workload DOWN (best-effort) and
    #      re-raise as SpawnFailed so the route maps it to 502 and no half-spawned state is left behind.
    try:
        # For a continued meeting this APPENDS a session to the reused row — N sessions per meeting (P3c).
        await repo.create_session(meeting_id=meeting_id, session_uid=connection_id)
        row = await repo.set_bot_container(meeting_id=meeting_id, bot_container_id=workload_id)
    except Exception as e:  # noqa: BLE001 — any post-spawn DB failure must trigger compensation
        try:
            await runtime.delete_workload(workload_id)
        except Exception as teardown_err:  # noqa: BLE001 — teardown is best-effort, never masks the cause
            log_event(
                "bot_spawn_orphan_teardown_failed", audience="system", level="error",
                span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                fields={"workload_id": workload_id, "error": str(teardown_err)},
            )
        log_event(
            "bot_spawn_post_spawn_db_failed", audience="system", level="error",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
            fields={"workload_id": workload_id, "error": str(e)},
        )
        raise SpawnFailed(
            f"post-spawn DB write failed; workload {workload_id} torn down"
        ) from e

    # Reconcile a stop that RACED the spawn (the spawn/stop design-gap fix): if a DELETE marked this
    # meeting stopping/terminal while the workload was being created, tear the just-spawned workload down
    # now — otherwise it boots, joins, and never receives the (already-published) leave command → orphan.
    # The stop's own direct teardown can't target a workload whose id wasn't written yet; this closes that
    # window (DELETE arriving before set_bot_container).
    raced_status = await repo.get_status_by_session(session_uid=connection_id)
    if raced_status in ("stopping", "completed", "failed"):
        try:
            await runtime.delete_workload(workload_id)
            log_event("bot_spawn_raced_stop_torn_down", audience="system", level="warning",
                      span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                      fields={"workload_id": workload_id, "raced_status": raced_status})
        except Exception as teardown_err:  # noqa: BLE001 — teardown is best-effort, never masks the spawn
            log_event("bot_spawn_raced_stop_teardown_failed", audience="system", level="error",
                      span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                      fields={"workload_id": workload_id, "error": str(teardown_err)})

    # The response lists the meeting's sessions (P3c) — all session_uids that ran against this row.
    sessions = await repo.list_sessions(meeting_id=meeting_id)

    # USER-facing: a bot was requested for this user.
    log_event(
        "bot_join_requested", audience="user", span="bots.create",
        user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
        fields={
            "platform": platform, "status": row.get("status", "requested"),
            "continued": reused_row is not None, "session_count": len(sessions),
        },
    )
    return _meeting_response(row, sessions=sessions)
