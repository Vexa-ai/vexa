"""Meeting CRUD — POST /bots, DELETE, GET /bots/status, PUT config.

All container operations delegate to Runtime API.
All endpoint paths and response shapes are frozen (see tests/contracts/).
Redis channels use the frozen bm: prefix.
"""

import asyncio
import base64
import hmac
import json
import logging
import os
import secrets
import time
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .database import get_db, async_session_local
from .models import Meeting, MeetingSession
from .schemas import (
    MeetingCreate,
    MeetingResponse,
    Platform,
    BotStatusResponse,
    MeetingConfigUpdate,
    MeetingStatus,
    MeetingCompletionReason,
    MeetingFailureStage,
    is_valid_status_transition,
    get_status_source,
)

from .auth import get_user_and_token
from .config import (
    REDIS_URL,
    RUNTIME_API_URL,
    MEETING_API_URL,
    BOT_IMAGE_NAME,
    BOT_STOP_DELAY_SECONDS,
)
from .post_meeting import run_all_tasks, run_status_webhook_task

logger = logging.getLogger("meeting_api.meetings")

router = APIRouter()


# ---------------------------------------------------------------------------
# Globals (set during startup)
# ---------------------------------------------------------------------------
redis_client: Optional[aioredis.Redis] = None


def set_redis(client: Optional[aioredis.Redis]):
    global redis_client
    redis_client = client


def get_redis() -> Optional[aioredis.Redis]:
    return redis_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_meeting_token(
    meeting_id: int,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    ttl_seconds: int = 3600,
) -> str:
    """Mint a MeetingToken (HS256 JWT) using ADMIN_TOKEN."""
    secret = os.environ.get("ADMIN_TOKEN")
    if not secret:
        raise ValueError("ADMIN_TOKEN not configured; cannot mint MeetingToken")

    now = int(datetime.utcnow().timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "meeting_id": meeting_id,
        "user_id": user_id,
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "scope": "transcribe:write",
        "iss": "meeting-api",
        "aud": "transcription-collector",
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid_lib.uuid4()),
    }

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, digestmod="sha256").digest()
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


async def update_meeting_status(
    meeting: Meeting,
    new_status: MeetingStatus,
    db: AsyncSession,
    completion_reason: Optional[MeetingCompletionReason] = None,
    failure_stage: Optional[MeetingFailureStage] = None,
    error_details: Optional[str] = None,
    transition_reason: Optional[str] = None,
    transition_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Update meeting status with validation and transition tracking.

    Uses SELECT FOR UPDATE to prevent TOCTOU race on concurrent callbacks.
    """
    # Fix 4: Re-fetch with row lock to prevent concurrent callback race
    locked_stmt = (
        select(Meeting)
        .where(Meeting.id == meeting.id)
        .with_for_update()
    )
    result = await db.execute(locked_stmt)
    meeting = result.scalar_one()

    try:
        current_status = MeetingStatus(meeting.status)
    except ValueError:
        logger.warning(f"Invalid meeting status '{meeting.status}' for meeting {meeting.id}, normalizing to 'failed'")
        current_status = MeetingStatus.FAILED
        meeting.status = MeetingStatus.FAILED.value
        await db.commit()

    if not is_valid_status_transition(current_status, new_status):
        # v0.10.5 Pack T (#278) — Already-terminal idempotent re-fire is benign,
        # not noise-worthy. Three independent code paths (chat persistence,
        # status update, post-meeting tasks) racing the documented
        # _delayed_stop_finalizer / runtime-api callback completion can each
        # try `completed → completed`. Pre-Pack-T this fired WARNING ×3 per
        # completed meeting (~30 in 90 min in prod logs). Now: log at DEBUG +
        # return True so callers' `if not success` short-circuit doesn't break
        # post-meeting tasks (per the documented race comment in callbacks.py).
        terminal = {MeetingStatus.COMPLETED, MeetingStatus.FAILED}
        if current_status == new_status and current_status in terminal:
            logger.debug(
                f"Idempotent re-fire of '{current_status.value}' for meeting {meeting.id} "
                f"(documented race; benign no-op; see callbacks.py:217)"
            )
            return True
        logger.warning(f"Invalid status transition '{current_status.value}' -> '{new_status.value}' for meeting {meeting.id}")
        return False

    old_status = meeting.status
    meeting.status = new_status.value

    current_data: Dict[str, Any] = {}
    if meeting.data:
        try:
            current_data = dict(meeting.data)
        except Exception:
            current_data = {}

    # v0.10.5 Pack R (#276) — failure_stage tracks the stage the meeting
    # WAS IN when failure happened, not the first ever-stuck value. Pre-
    # Pack-R, a meeting that failed in 'active' showed failure_stage='joining'
    # if some early-signal capture had set it; never updated. Result:
    # dashboards grouping by failure_stage misattributed in-meeting failures
    # to admission/join issues. Now: every transition to FAILED overwrites.
    #
    # v0.10.5 iter-6 fix (caught by compose smoke MEETINGS_LIST 500): only
    # overwrite when current_status maps to a valid MeetingFailureStage
    # value. MeetingStatus has more values than MeetingFailureStage —
    # `stopping`, `completed`, `left_alone` etc. are statuses but not
    # lifecycle stages. Writing `failure_stage='stopping'` produces a
    # JSONB record that the Pydantic response-validator rejects (HTTP
    # 500 on /meetings list). When current_status is transitional/
    # terminal (not a lifecycle stage), keep whatever was set during the
    # prior progression; that's the most recent ACTUAL stage reached.
    if new_status == MeetingStatus.FAILED:
        try:
            valid_failure_stages = {s.value for s in MeetingFailureStage}
            if current_status.value in valid_failure_stages:
                current_data["failure_stage"] = current_status.value
            # Else: current_status is transitional (e.g. 'stopping') or
            # terminal-equivalent. Don't overwrite — last lifecycle stage
            # set by the natural progression is the right value.
        except Exception:
            pass

    if new_status == MeetingStatus.COMPLETED:
        if completion_reason:
            current_data["completion_reason"] = completion_reason.value
        meeting.end_time = datetime.utcnow()
    elif new_status == MeetingStatus.FAILED:
        # v0.10.5 Pack X finding (2026-04-27): persist completion_reason
        # on FAILED transitions too. Pack J's classifier routes to
        # FAILED + STOPPED_WITH_NO_AUDIO (or other terminal failure
        # reasons), but the previous code only wrote completion_reason
        # to data on COMPLETED. Result: Pack J classification was
        # CORRECT in the transition_entry log but invisible at top-
        # level data.completion_reason — dashboards grouping by
        # data.completion_reason saw empty for the entire #255 silent
        # class. Surfaced by tests3/synthetic/scenarios/pack-j-via-
        # exit-callback.sh which asserts completion_reason on the
        # FAILED meeting and caught it.
        if completion_reason:
            current_data["completion_reason"] = completion_reason.value
        if failure_stage:
            current_data["failure_stage"] = failure_stage.value
        if error_details:
            current_data["error_details"] = error_details
        meeting.end_time = datetime.utcnow()

    transition_entry: Dict[str, Any] = {
        "from": old_status,
        "to": new_status.value,
        "timestamp": datetime.utcnow().isoformat(),
        "source": get_status_source(current_status, new_status),
    }
    if transition_reason:
        transition_entry["reason"] = transition_reason
    if completion_reason:
        transition_entry["completion_reason"] = completion_reason.value
    if failure_stage:
        transition_entry["failure_stage"] = failure_stage.value
    if error_details:
        transition_entry["error_details"] = error_details
    if isinstance(transition_metadata, dict) and transition_metadata:
        for k, v in transition_metadata.items():
            if k not in transition_entry:
                transition_entry[k] = v

    existing = current_data.get("status_transition")
    if isinstance(existing, dict):
        transitions_list = [existing]
    elif isinstance(existing, list):
        transitions_list = existing
    else:
        transitions_list = []
    transitions_list = list(transitions_list) + [transition_entry]
    current_data["status_transition"] = transitions_list
    current_data.pop("status_transitions", None)

    meeting.data = current_data
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await db.refresh(meeting)
    logger.info(f"Meeting {meeting.id} status: '{old_status}' -> '{new_status.value}'")

    # Cancel scheduler timeout job when reaching terminal state
    if new_status in (MeetingStatus.COMPLETED, MeetingStatus.FAILED):
        job_id = (meeting.data or {}).get("scheduler_job_id") if isinstance(meeting.data, dict) else None
        if job_id:
            asyncio.create_task(_cancel_bot_timeout(job_id, meeting.id))

    return True


async def publish_meeting_status_change(
    meeting_id: int,
    new_status: str,
    redis: Optional[aioredis.Redis],
    platform: str,
    native_meeting_id: str,
    user_id: int,
    extra_data: Optional[Dict[str, Any]] = None,
):
    """Publish to bm:meeting:{id}:status — frozen channel prefix."""
    if not redis:
        return
    try:
        status_payload: Dict[str, Any] = {"status": new_status}
        if extra_data:
            status_payload["data"] = extra_data
        payload = {
            "type": "meeting.status",
            "meeting": {"id": meeting_id, "platform": platform, "native_id": native_meeting_id},
            "payload": status_payload,
            "user_id": user_id,
            "ts": datetime.utcnow().isoformat(),
        }
        channel = f"bm:meeting:{meeting_id}:status"
        await redis.publish(channel, json.dumps(payload))
        logger.info(f"Published status '{new_status}' to '{channel}'")
    except Exception as e:
        logger.error(f"Failed to publish status for meeting {meeting_id}: {e}")


async def schedule_status_webhook_task(
    meeting: Meeting,
    background_tasks: BackgroundTasks,
    old_status: str,
    new_status: str,
    reason: Optional[str] = None,
    transition_source: Optional[str] = None,
):
    background_tasks.add_task(
        run_status_webhook_task,
        meeting.id,
        {
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            "transition_source": transition_source,
        },
    )


def _get_httpx_client() -> httpx.AsyncClient:
    """Return the shared httpx client from app.state, or a fallback."""
    from .main import app
    client = getattr(app.state, "httpx_client", None)
    if client is None:
        # Fallback for cases where app hasn't started yet (tests, etc.)
        return httpx.AsyncClient(timeout=30.0)
    return client


async def _schedule_bot_timeout(
    meeting_id: int,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    max_bot_time_ms: int,
) -> Optional[str]:
    """Schedule a timeout job to kill the bot after max_bot_time.

    Returns the scheduler job_id on success, None on failure.
    """
    try:
        client = _get_httpx_client()
        execute_at = time.time() + (max_bot_time_ms / 1000.0)
        resp = await client.post(
            f"{RUNTIME_API_URL}/scheduler/jobs",
            json={
                "execute_at": execute_at,
                "request": {
                    "method": "DELETE",
                    "url": f"{MEETING_API_URL}/bots/internal/timeout/{meeting_id}",
                    "timeout": 30,
                },
                "metadata": {
                    "type": "bot_timeout",
                    "meeting_id": meeting_id,
                    "user_id": user_id,
                },
                "idempotency_key": f"bot_timeout_{meeting_id}",
            },
            timeout=10.0,
        )
        if resp.status_code == 201:
            job = resp.json()
            job_id = job.get("job_id")
            logger.info(f"Scheduled bot timeout job {job_id} for meeting {meeting_id} (max_bot_time={max_bot_time_ms}ms)")
            return job_id
        else:
            logger.error(f"Failed to schedule bot timeout: HTTP {resp.status_code}: {resp.text}")
            return None
    except Exception as e:
        logger.error(f"Failed to schedule bot timeout for meeting {meeting_id}: {e}")
        return None


async def _cancel_bot_timeout(job_id: str, meeting_id: int) -> None:
    """Cancel the scheduler timeout job for a meeting, if one exists."""
    try:
        client = _get_httpx_client()
        resp = await client.delete(
            f"{RUNTIME_API_URL}/scheduler/jobs/{job_id}",
            timeout=10.0,
        )
        if resp.status_code in (200, 404):
            logger.info(f"Cancelled bot timeout job {job_id} for meeting {meeting_id}")
        else:
            logger.warning(f"Failed to cancel bot timeout job {job_id}: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to cancel bot timeout for meeting {meeting_id}: {e}")


async def _spawn_via_runtime_api(
    profile: str,
    config: Dict[str, Any],
    user_id: int,
    callback_url: str,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Create a container via Runtime API POST /containers."""
    try:
        client = _get_httpx_client()
        resp = await client.post(
            f"{RUNTIME_API_URL}/containers",
            json={
                "profile": profile,
                "config": config,
                "user_id": str(user_id),
                "callback_url": callback_url,
                "metadata": metadata,
            },
            timeout=30.0,
        )
        if resp.status_code == 201:
            return resp.json()
        elif resp.status_code == 429:
            raise HTTPException(status_code=429, detail=resp.json().get("detail", "Concurrency limit reached"))
        else:
            logger.error(f"Runtime API returned {resp.status_code}: {resp.text}")
            return None
    except httpx.RequestError as e:
        logger.error(f"Runtime API request failed: {e}")
        return None


async def _get_container_info(container_name: str) -> Optional[dict]:
    """Get container info from Runtime API GET /containers/{name}."""
    try:
        client = _get_httpx_client()
        resp = await client.get(
            f"{RUNTIME_API_URL}/containers/{container_name}",
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except httpx.RequestError:
        pass
    return None


async def _stop_via_runtime_api(container_name: str) -> bool:
    """Stop a container via Runtime API DELETE /containers/{name}."""
    try:
        client = _get_httpx_client()
        resp = await client.delete(
            f"{RUNTIME_API_URL}/containers/{container_name}",
            timeout=30.0,
        )
        return resp.status_code in (200, 404)
    except httpx.RequestError as e:
        logger.error(f"Runtime API stop failed for {container_name}: {e}")
        return False


async def _get_running_bots_from_runtime(user_id: int) -> list:
    """Get running containers for a user from Runtime API + enrich with DB data."""
    try:
        client = _get_httpx_client()
        containers = []
        for profile in ("meeting", "browser-session"):
            resp = await client.get(
                f"{RUNTIME_API_URL}/containers",
                params={"user_id": str(user_id), "profile": profile},
                timeout=15.0,
            )
            if resp.status_code == 200:
                containers.extend(resp.json())
    except httpx.RequestError as e:
        logger.error(f"Runtime API list failed for user {user_id}: {e}")
        return []

    # Parse container info and collect meeting IDs to fetch
    parsed_containers = []
    meeting_ids_to_fetch = set()
    for c in containers:
        if c.get("status") != "running":
            continue

        name = c.get("name", "")
        meeting_id_from_name = "unknown"
        meeting_id_int = None

        # Primary: metadata.meeting_id (set at spawn time)
        meta = c.get("metadata", {})
        if meta.get("meeting_id"):
            try:
                meeting_id_int = int(meta["meeting_id"])
                meeting_id_from_name = str(meeting_id_int)
            except (ValueError, TypeError):
                pass

        # Fallback: parse container name (meeting-{user_id}-{hash} or vexa-bot-{id}-...)
        if meeting_id_int is None:
            try:
                parts = name.split("-")
                if len(parts) > 2 and parts[0] == "meeting":
                    meeting_id_from_name = parts[2]
                elif len(parts) > 2 and parts[0] == "vexa" and parts[1] == "bot":
                    meeting_id_from_name = parts[2]
                    meeting_id_int = int(meeting_id_from_name)
            except (ValueError, IndexError):
                pass

        if meeting_id_int is not None:
            meeting_ids_to_fetch.add(meeting_id_int)

        parsed_containers.append((c, name, meeting_id_from_name, meeting_id_int))

    # Batch-fetch all meetings in one short-lived DB session
    meetings_map: Dict[int, Meeting] = {}
    if meeting_ids_to_fetch:
        async with async_session_local() as db:
            for mid in meeting_ids_to_fetch:
                try:
                    meeting = await db.get(Meeting, mid)
                    if meeting:
                        # Snapshot the fields we need so session can close
                        meetings_map[mid] = {
                            "platform": meeting.platform,
                            "native_meeting_id": meeting.platform_specific_id,
                            "data": dict(meeting.data) if meeting.data else {},
                            "start_time": meeting.start_time.isoformat() if meeting.start_time else None,
                            "status": meeting.status,
                            "id": meeting.id,
                        }
                except Exception as e:
                    logger.error(f"DB error fetching meeting {mid}: {e}")
    # DB session closed here — no async I/O while holding connection

    # Redis TTL refreshes (outside DB session)
    if redis_client:
        for mid, mdata in meetings_map.items():
            try:
                await redis_client.expire(f"browser_session:{mdata['id']}", 86400)
                session_token = mdata["data"].get("session_token")
                if session_token:
                    await redis_client.expire(f"browser_session:{session_token}", 86400)
            except Exception:
                pass

    # Build response
    bots_status = []
    for c, name, meeting_id_from_name, meeting_id_int in parsed_containers:
        mdata = meetings_map.get(meeting_id_int, {}) if meeting_id_int else {}
        meeting_data = mdata.get("data", {})

        created_at = None
        if c.get("created_at"):
            try:
                created_at = datetime.fromtimestamp(c["created_at"], timezone.utc).isoformat()
            except Exception:
                pass

        safe_data = {k: v for k, v in meeting_data.items() if k != "webhook_secret"} if meeting_data else {}

        bots_status.append({
            "container_id": c.get("container_id"),
            "container_name": name,
            "platform": mdata.get("platform"),
            "native_meeting_id": mdata.get("native_meeting_id"),
            "status": "running",
            "normalized_status": "Up",
            "created_at": created_at,
            "start_time": mdata.get("start_time"),
            "labels": {},
            "meeting_id_from_name": meeting_id_from_name,
            "meeting_status": mdata.get("status"),
            "data": safe_data,
        })

    return bots_status


async def _delayed_container_stop(container_name: str, meeting_id: int, delay_seconds: int = BOT_STOP_DELAY_SECONDS):
    """Enqueue a delayed container-stop intent onto the durable outbox.

    v0.10.5 Pack D.2 (#266) — REPLACED the in-process `asyncio.sleep + stop`
    body with a single XADD onto `meeting-api:container-stops`. The actual
    runtime-api DELETE is now driven by sweeps.consume_pending_stops on
    every sweep iteration. Pre-D.2, this function was a fire-and-forget
    BackgroundTask: meeting-api restart in the 90 s window dropped the
    pending stop on the floor, leaving bot pods Running indefinitely.
    Production scale test (release-006, 20-bot Google Meet): 3-of-20
    DELETEs returned 500 with the meeting marked COMPLETED while pods
    kept recording for 12+ minutes. Outbox shape mirrors 260421 Pack J's
    durable exit-callback (same problem class, same fix).

    Principle filter: ONE durable mechanism for delayed stop (the outbox).
    No in-process timer competing. Idempotent — runtime-api DELETE is
    already 200-no-op for already-stopped containers.

    v0.10.5 Pack E.3.1 — REMOVED the redundant `_delayed_stop_finalizer`
    safety block. 260421 Pack J shipped durable exit-callback delivery
    in runtime-api's idle_loop; that path is now canonical for
    `stopping → completed`. Pack J's classifier in callbacks.py routes
    correctly per data-driven rules. Pack E.3.2 sweep (sweeps.py) catches
    stale 'stopping' rows that genuinely escape both — operator-actionable
    signal, not silent recovery.

    What this function still does:
      1. XADD the delayed-stop intent to the outbox (durable, retried by
         sweep consumer with exponential backoff and DLQ).
      2. Eagerly clean up the browser_session:* secondary Redis keys —
         these are owned by meeting-api and unrelated to runtime-api stop.

    Status finalization is handled by:
      - Pack J's classifier (canonical, runtime-api callback fires)
      - Pack E.3.2 sweep (escape hatch — runs every 60s, threshold 5 min)
    """
    from .container_stop_outbox import enqueue_stop

    if redis_client is None:
        # If Redis is unreachable at the moment we try to enqueue, fall
        # through to a direct stop attempt — the sweep consumer can't pick
        # up something that wasn't enqueued. /readyz (Pack C.4) gates traffic
        # on Redis being up, so this branch should be rare; logging it loud
        # so operator sees the deviation from the canonical path.
        logger.error(
            f"[Delayed Stop] Redis unavailable — cannot enqueue stop for "
            f"{container_name} (meeting {meeting_id}); attempting direct stop."
        )
        await asyncio.sleep(delay_seconds)
        await _stop_via_runtime_api(container_name)
    else:
        await enqueue_stop(redis_client, container_name, meeting_id, delay_seconds)
        logger.info(
            f"[Delayed Stop] Enqueued stop for {container_name} "
            f"(meeting {meeting_id}) delay={delay_seconds}s — sweep consumer will fire"
        )

    # browser_session:* secondary-key cleanup is local to meeting-api and
    # safe to do immediately (those keys are short-lookup helpers, not
    # tied to the runtime-api stop ack). Mirrors pre-D.2 behaviour.
    try:
        meeting_id_for_redis = None
        session_token_for_redis = None
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                meeting_id_for_redis = meeting.id
                session_token_for_redis = (meeting.data or {}).get("session_token")

        if redis_client and meeting_id_for_redis is not None:
            if session_token_for_redis:
                await redis_client.delete(f"browser_session:{session_token_for_redis}")
            await redis_client.delete(f"browser_session:{meeting_id_for_redis}")
    except Exception as e:
        logger.error(f"[Delayed Stop] Finalizer error for meeting {meeting_id}: {e}", exc_info=True)


async def _find_active_meeting(
    db: AsyncSession, user_id: int, platform_value: str, native_meeting_id: str,
) -> Meeting:
    stmt = (
        select(Meeting)
        .where(
            Meeting.user_id == user_id,
            Meeting.platform == platform_value,
            Meeting.platform_specific_id == native_meeting_id,
            Meeting.status == MeetingStatus.ACTIVE.value,
        )
        .order_by(desc(Meeting.created_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active meeting found for {platform_value}/{native_meeting_id}",
        )
    return meeting


async def _find_meeting_any_status(
    db: AsyncSession, user_id: int, platform_value: str, native_meeting_id: str,
) -> Meeting:
    """Like _find_active_meeting but returns the most recent meeting regardless of status."""
    stmt = (
        select(Meeting)
        .where(
            Meeting.user_id == user_id,
            Meeting.platform == platform_value,
            Meeting.platform_specific_id == native_meeting_id,
        )
        .order_by(desc(Meeting.created_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No meeting found for {platform_value}/{native_meeting_id}",
        )
    return meeting


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/bots",
    response_model=MeetingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a new bot instance to join a meeting",
    dependencies=[Depends(get_user_and_token)],
)
async def request_bot(
    req: MeetingCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    user_token, current_user = auth_data

    # --- Agent-only mode ---
    if req.agent_enabled and req.platform is None:
        new_meeting = Meeting(
            user_id=current_user.id,
            platform="agent",
            platform_specific_id=f"agent-{uuid_lib.uuid4().hex[:8]}",
            status=MeetingStatus.REQUESTED.value,
            data={"agent_enabled": True},
        )
        db.add(new_meeting)
        await db.commit()
        await db.refresh(new_meeting)

        result = await _spawn_via_runtime_api(
            profile="meeting",
            config={"env": {"BOT_MODE": "agent"}},
            user_id=current_user.id,
            callback_url=f"{MEETING_API_URL}/bots/internal/callback/exited",
            metadata={"meeting_id": new_meeting.id},
        )
        if not result:
            new_meeting.status = MeetingStatus.FAILED.value
            await db.commit()
            raise HTTPException(status_code=500, detail="Failed to start agent container")

        new_meeting.bot_container_id = result.get("name") or result.get("container_id")
        new_meeting.status = MeetingStatus.ACTIVE.value
        await db.commit()
        await db.refresh(new_meeting)
        return MeetingResponse.model_validate(new_meeting)

    # --- Browser session mode ---
    if req.mode == "browser_session":
        # Concurrency check
        user_limit = int(getattr(current_user, "max_concurrent_bots", 0) or 0)
        if user_limit > 0:
            count_stmt = select(func.count()).select_from(Meeting).where(
                and_(
                    Meeting.user_id == current_user.id,
                    Meeting.status.in_([s.value for s in (MeetingStatus.REQUESTED, MeetingStatus.JOINING, MeetingStatus.AWAITING_ADMISSION, MeetingStatus.ACTIVE)]),
                )
            )
            active_count = int((await db.execute(count_stmt)).scalar() or 0)
            if active_count >= user_limit:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Concurrent bot limit reached ({active_count}/{user_limit})")

        session_token = secrets.token_urlsafe(24)
        new_meeting = Meeting(
            user_id=current_user.id,
            platform="browser_session",
            platform_specific_id=f"bs-{uuid_lib.uuid4().hex[:8]}",
            status=MeetingStatus.ACTIVE.value,
            start_time=datetime.utcnow(),
            data={"mode": "browser_session", "session_token": session_token},
        )
        db.add(new_meeting)
        await db.commit()
        await db.refresh(new_meeting)

        # Record initial status transition
        meeting_data = dict(new_meeting.data or {})
        meeting_data["status_transition"] = [{"from": None, "to": "active", "timestamp": datetime.utcnow().isoformat(), "source": "creation"}]
        new_meeting.data = meeting_data
        await db.commit()
        await db.refresh(new_meeting)

        # S3/MinIO config for browser data persistence.
        # When MINIO_ENDPOINT is set, browser userdata syncs to S3 (survives restarts).
        # When empty, userdata lives only in the container filesystem (local-only mode).
        minio_endpoint = (os.environ.get("MINIO_ENDPOINT") or "").strip()
        s3_config = {}
        if minio_endpoint:
            minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
            s3_config = {
                "userdataS3Path": f"users/{current_user.id}/browser-userdata",
                "s3Endpoint": f"{'https' if minio_secure else 'http'}://{minio_endpoint}",
                "s3Bucket": os.environ.get("MINIO_BUCKET", "vexa-recordings"),
                "s3AccessKey": os.environ.get("MINIO_ACCESS_KEY", ""),
                "s3SecretKey": os.environ.get("MINIO_SECRET_KEY", ""),
            }

        bot_config = {
            "mode": "browser_session",
            "meeting_id": new_meeting.id,
            "session_token": session_token,
            "redisUrl": REDIS_URL,
            "meetingApiCallbackUrl": f"{MEETING_API_URL}/bots/internal/callback/exited",
            **s3_config,
        }

        result = await _spawn_via_runtime_api(
            profile="browser-session",
            config={"image": BOT_IMAGE_NAME, "env": {"BOT_CONFIG": json.dumps(bot_config), "BOT_MODE": "browser_session"}},
            user_id=current_user.id,
            callback_url=f"{MEETING_API_URL}/bots/internal/callback/exited",
            metadata={"meeting_id": new_meeting.id},
        )
        if not result:
            new_meeting.status = MeetingStatus.FAILED.value
            await db.commit()
            raise HTTPException(status_code=500, detail="Failed to start browser session container")

        new_meeting.bot_container_id = result.get("name") or result.get("container_id")
        await db.commit()
        await db.refresh(new_meeting)

        # Store in Redis for gateway proxy (by session_token for backward compat + by meeting ID)
        if redis_client:
            container_name = result.get("name")
            container_ip = result.get("ip")

            # K8s: pod IP may not be available at creation time. Poll for it.
            if not container_ip and container_name:
                for _attempt in range(10):
                    await asyncio.sleep(1)
                    try:
                        info = await _get_container_info(container_name)
                        if info and info.get("ip"):
                            container_ip = info["ip"]
                            break
                    except Exception:
                        pass

            container_info = json.dumps({
                "container_name": container_name,
                "container_ip": container_ip,
                "meeting_id": new_meeting.id,
                "user_id": current_user.id,
            })
            await redis_client.set(f"browser_session:{session_token}", container_info, ex=86400)
            await redis_client.set(f"browser_session:{new_meeting.id}", container_info, ex=86400)

        return MeetingResponse.model_validate(new_meeting)

    # --- Standard meeting bot ---
    native_meeting_id = req.native_meeting_id

    # v0.10.5 (2026-04-27) — Path 3: (platform + meeting_url) without
    # parser-extractable native_meeting_id is valid. Synthesize a
    # placeholder native_meeting_id from the URL hash so internal
    # tracking, dedupe, and cancel flows still work. The bot uses
    # `meeting_url` directly (browser navigates; Zoom/Meet/Teams
    # backends resolve white-label/enterprise URLs server-side).
    #
    # This is the "no proliferating per-vendor URL parsers" boundary
    # (per project-owner 2026-04-27 — "we will have endless [LFX-style
    # white-label URLs]; cannot create a parser for every one. Allow
    # users to supply (URL + platform) and trust them.").
    # Best-effort passcode extraction from `?password=...` or `?pwd=...`
    # query params (white-label URLs use either). Only extracts if
    # caller didn't supply passcode explicitly. Zoom's join URL accepts
    # the embedded passcode either way; this just makes the field
    # available for dashboards/analytics.
    if req.meeting_url and not req.passcode:
        import re as _re
        pw_match = _re.search(r"[?&](?:password|pwd)=([^&\s]+)", req.meeting_url)
        if pw_match:
            req.passcode = pw_match.group(1)
            logger.info(f"Path 3: extracted passcode from meeting_url query")

    if not native_meeting_id and req.meeting_url:
        # Best-effort numeric-ID extraction (Path 3 enhancement).
        # Bot adapters validate native_meeting_id format per platform
        # (Zoom expects 9-11 digits). White-label URLs typically embed
        # the canonical ID in the path: LFX has /meeting/<numeric>,
        # AWS has /j/<numeric>, etc. We grep for the first 9-11 digit
        # run in the URL and use it if found. Falls back to URL hash
        # only when no numeric ID is present (e.g., truly opaque URLs).
        import re, hashlib
        if req.platform.value == "zoom":
            zoom_id_match = re.search(r"\b(\d{9,11})\b", req.meeting_url)
            if zoom_id_match:
                native_meeting_id = zoom_id_match.group(1)
                logger.info(
                    f"Path 3 (URL+zoom): extracted native_meeting_id='{native_meeting_id}' "
                    f"from meeting_url='{req.meeting_url[:60]}...'"
                )
        if not native_meeting_id:
            # Fallback for opaque URLs (no extractable ID).
            h = hashlib.sha256(req.meeting_url.encode()).hexdigest()[:10]
            native_meeting_id = f"url-{h}"
            logger.info(
                f"Path 3 (URL+{req.platform.value}): meeting_url='{req.meeting_url[:60]}...' "
                f"→ synthesized native_meeting_id='{native_meeting_id}' (no numeric ID found)"
            )

    # Construct meeting URL
    if req.meeting_url:
        constructed_url = req.meeting_url
    else:
        constructed_url = Platform.construct_meeting_url(
            req.platform.value, native_meeting_id, req.passcode, base_host=req.teams_base_host,
        )
        if not constructed_url:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot construct meeting URL")

    # Check for duplicate active meeting
    existing_stmt = (
        select(Meeting)
        .where(
            Meeting.user_id == current_user.id,
            Meeting.platform == req.platform.value,
            Meeting.platform_specific_id == native_meeting_id,
            Meeting.status.in_(["requested", "joining", "awaiting_admission", "active"]),
        )
        .order_by(desc(Meeting.created_at))
        .limit(1)
    )
    existing = (await db.execute(existing_stmt)).scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active or requested meeting already exists for this platform and meeting ID",
        )

    # Concurrency limit (exclude browser_session from count — they are infrastructure, not bots)
    user_limit = int(getattr(current_user, "max_concurrent_bots", 0) or 0)
    if user_limit > 0:
        count_stmt = select(func.count()).select_from(Meeting).where(
            and_(
                Meeting.user_id == current_user.id,
                Meeting.status.in_([s.value for s in (MeetingStatus.REQUESTED, MeetingStatus.JOINING, MeetingStatus.AWAITING_ADMISSION, MeetingStatus.ACTIVE)]),
                Meeting.platform != "browser_session",
            )
        )
        active_count = int((await db.execute(count_stmt)).scalar() or 0)
        if active_count >= user_limit:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"User has reached the maximum concurrent bot limit ({user_limit}).")

    # Create meeting record
    meeting_data: Dict[str, Any] = {}
    if req.passcode:
        meeting_data["passcode"] = req.passcode
    if req.meeting_url:
        meeting_data["meeting_url"] = req.meeting_url
    if req.teams_base_host:
        meeting_data["teams_base_host"] = req.teams_base_host
    transcribe = True if req.transcribe_enabled is None else bool(req.transcribe_enabled)
    meeting_data["transcribe_enabled"] = transcribe
    if req.video:
        meeting_data["recording_enabled"] = True
        meeting_data["capture_modes"] = ["audio", "video"]
    elif req.recording_enabled is not None:
        meeting_data["recording_enabled"] = bool(req.recording_enabled)
    else:
        meeting_data["recording_enabled"] = os.getenv("RECORDING_ENABLED", "true").lower() == "true"

    # Store webhook config in meeting.data (from gateway headers or user config)
    webhook_url = request.headers.get("X-User-Webhook-URL", "")
    if webhook_url:
        meeting_data["webhook_url"] = webhook_url
        webhook_secret = request.headers.get("X-User-Webhook-Secret", "")
        if webhook_secret:
            meeting_data["webhook_secret"] = webhook_secret
        webhook_events_raw = request.headers.get("X-User-Webhook-Events", "")
        if webhook_events_raw:
            meeting_data["webhook_events"] = {
                evt.strip(): True for evt in webhook_events_raw.split(",") if evt.strip()
            }

    new_meeting = Meeting(
        user_id=current_user.id,
        platform=req.platform.value,
        platform_specific_id=native_meeting_id,
        status=MeetingStatus.REQUESTED.value,
        data=meeting_data,
    )
    db.add(new_meeting)
    await db.commit()
    await db.refresh(new_meeting)
    meeting_id = new_meeting.id

    # Publish initial status
    try:
        await publish_meeting_status_change(meeting_id, "requested", redis_client, req.platform.value, native_meeting_id, current_user.id)
    except Exception:
        pass

    # Mint meeting token
    try:
        meeting_token = mint_meeting_token(meeting_id, current_user.id, req.platform.value, native_meeting_id, ttl_seconds=7200)
    except Exception as e:
        logger.error(f"Failed to mint MeetingToken for meeting {meeting_id}: {e}")
        new_meeting.status = MeetingStatus.FAILED.value
        await db.commit()
        raise HTTPException(status_code=500, detail="Failed to mint meeting token")

    # Build BOT_CONFIG — load user data directly from DB (UserProxy doesn't carry it)
    user_recording_config = {}
    user_bot_config = {}
    try:
        user_row = await db.execute(
            sa_text("SELECT data FROM users WHERE id = :uid"),
            {"uid": current_user.id},
        )
        user_data = user_row.scalar()
        if user_data and isinstance(user_data, dict):
            user_recording_config = user_data.get("recording_config", {})
            user_bot_config = user_data.get("bot_config", {})
    except Exception:
        pass

    # System defaults for timeouts (ms)
    SYSTEM_DEFAULTS = {
        "max_bot_time": 7200000,          # 2h
        "max_wait_for_admission": 900000, # 15 min
        "max_time_left_alone": 900000,    # 15 min
        "no_one_joined_timeout": 120000,  # 2 min
    }

    # Resolution order: per-request → user.data.bot_config → system defaults
    def resolve_timeout(field_name: str) -> int:
        # Per-request override
        if req.automatic_leave:
            val = getattr(req.automatic_leave, field_name, None)
            if val is not None:
                return val
        # User-level default from user.data.bot_config
        if isinstance(user_bot_config, dict):
            val = user_bot_config.get(field_name)
            if val is not None:
                return int(val)
        # System default
        return SYSTEM_DEFAULTS[field_name]

    resolved_max_bot_time = resolve_timeout("max_bot_time")
    resolved_max_wait_for_admission = resolve_timeout("max_wait_for_admission")
    resolved_max_time_left_alone = resolve_timeout("max_time_left_alone")
    resolved_no_one_joined_timeout = resolve_timeout("no_one_joined_timeout")

    # Store resolved timeouts in meeting.data for GET /bots visibility
    meeting_data["resolved_timeouts"] = {
        "max_bot_time": resolved_max_bot_time,
        "max_wait_for_admission": resolved_max_wait_for_admission,
        "max_time_left_alone": resolved_max_time_left_alone,
        "no_one_joined_timeout": resolved_no_one_joined_timeout,
    }
    new_meeting.data = meeting_data
    await db.commit()
    await db.refresh(new_meeting)

    connection_id = str(uuid_lib.uuid4())
    bot_config = {
        "meeting_id": meeting_id,
        "platform": req.platform.value,
        "meetingUrl": constructed_url,
        "botName": req.bot_name or f"VexaBot-{uuid_lib.uuid4().hex[:6]}",
        "token": meeting_token,
        "nativeMeetingId": native_meeting_id,
        "connectionId": connection_id,
        "language": req.language,
        "task": req.task,
        "transcriptionTier": req.transcription_tier or "realtime",
        "redisUrl": REDIS_URL,
        # Map API names → bot-side frozen names
        "automaticLeave": {
            "waitingRoomTimeout": resolved_max_wait_for_admission,
            "noOneJoinedTimeout": resolved_no_one_joined_timeout,
            "everyoneLeftTimeout": resolved_max_time_left_alone,
        },
        "meetingApiCallbackUrl": f"{MEETING_API_URL}/bots/internal/callback/exited",
        "recordingEnabled": user_recording_config.get("enabled", os.getenv("RECORDING_ENABLED", "true").lower() == "true"),
        "transcribeEnabled": transcribe,
        "captureModes": user_recording_config.get("capture_modes", os.getenv("CAPTURE_MODES", "audio").split(",")),
        "recordingUploadUrl": f"{MEETING_API_URL}/internal/recordings/upload",
        "transcriptionServiceUrl": os.getenv("TRANSCRIPTION_SERVICE_URL"),
        "transcriptionServiceToken": os.getenv("TRANSCRIPTION_SERVICE_TOKEN"),
    }
    if req.recording_enabled is not None:
        bot_config["recordingEnabled"] = bool(req.recording_enabled)
    if req.voice_agent_enabled is not None:
        bot_config["voiceAgentEnabled"] = bool(req.voice_agent_enabled)
    if req.default_avatar_url:
        bot_config["defaultAvatarUrl"] = req.default_avatar_url
    if os.getenv("SHOW_AVATAR", "true").lower() == "false":
        bot_config["showAvatar"] = False
    if req.zoom_obf_token:
        bot_config["obfToken"] = req.zoom_obf_token
    if req.zoom_zak_token:
        bot_config["zakToken"] = req.zoom_zak_token
    if meeting_data.get("capture_modes"):
        bot_config["captureModes"] = meeting_data["capture_modes"]
    if req.authenticated:
        minio_endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
        s3_endpoint_url = f"{'https' if minio_secure else 'http'}://{minio_endpoint}"
        s3_bucket = os.environ.get("MINIO_BUCKET", "vexa-recordings")
        bot_config["authenticated"] = True
        bot_config["userdataS3Path"] = f"users/{current_user.id}/browser-userdata"
        bot_config["s3Endpoint"] = s3_endpoint_url
        bot_config["s3Bucket"] = s3_bucket
        bot_config["s3AccessKey"] = os.environ.get("MINIO_ACCESS_KEY", "")
        bot_config["s3SecretKey"] = os.environ.get("MINIO_SECRET_KEY", "")
    # Remove None values
    bot_config = {k: v for k, v in bot_config.items() if v is not None}

    # Build env for Runtime API
    env_vars = {
        "BOT_CONFIG": json.dumps(bot_config),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO").upper(),
        "VIDEO_HWACCEL": os.getenv("VIDEO_HWACCEL", "none").lower(),
    }
    tts_url = os.getenv("TTS_SERVICE_URL", "").strip()
    if tts_url:
        env_vars["TTS_SERVICE_URL"] = tts_url
    raw_capture = os.getenv("RAW_CAPTURE", "").strip()
    if raw_capture:
        env_vars["RAW_CAPTURE"] = raw_capture

    # Zoom credentials.
    # Zoom Web is the bot's default — no env propagation needed for the
    # web path. The native SDK path is opt-in via operator-set
    # ZOOM_SDK=true plus ZOOM_CLIENT_ID/ZOOM_CLIENT_SECRET. We forward
    # the legacy ZOOM_WEB=true too so operators with explicit overrides
    # keep working until Wave 3 retires both env vars in favour of
    # `platform: zoom_sdk`.
    if req.platform.value == "zoom":
        if os.getenv("ZOOM_WEB", "").strip() == "true":
            env_vars["ZOOM_WEB"] = "true"
        if os.getenv("ZOOM_SDK", "").strip() == "true":
            env_vars["ZOOM_SDK"] = "true"
            zoom_cid = os.getenv("ZOOM_CLIENT_ID")
            zoom_csec = os.getenv("ZOOM_CLIENT_SECRET")
            if zoom_cid and zoom_csec:
                env_vars["ZOOM_CLIENT_ID"] = zoom_cid
                env_vars["ZOOM_CLIENT_SECRET"] = zoom_csec

    # v0.10.5 Pack X — synthetic dry-run mode.
    # When req.dry_run=True, skip the runtime-api bot launch + scheduler
    # + Redis registration. Test driver drives the meeting lifecycle via
    # /bots/internal/test/* + /bots/internal/callback/* endpoints. The
    # meeting record + MeetingSession get bootstrapped by the test
    # surface; the rig owns end-to-end. Catches OSS-side regressions
    # without contamination from real bot subprocess firing its own
    # callbacks (which races synthetic callbacks under the previous
    # "real bot launches and exits in 5s" path).
    #
    # Production gate: VEXA_ENV != "production". 422 in prod.
    if req.dry_run:
        if os.getenv("VEXA_ENV", "development") == "production":
            raise HTTPException(
                status_code=422,
                detail="dry_run=true is a test-mode flag; not allowed in production",
            )
        # Persist meeting record without launching bot. bot_container_id
        # stays None; test driver bootstraps session via
        # /bots/internal/test/session-bootstrap.
        await db.commit()
        await db.refresh(new_meeting)
        logger.info(
            f"[Pack X dry_run] Meeting {meeting_id} created without bot launch; "
            f"test driver controls lifecycle via callback endpoints."
        )
        return MeetingResponse.model_validate(new_meeting)

    # Spawn via Runtime API
    result = await _spawn_via_runtime_api(
        profile="meeting",
        config={"image": BOT_IMAGE_NAME, "env": env_vars},
        user_id=current_user.id,
        callback_url=f"{MEETING_API_URL}/bots/internal/callback/exited",
        metadata={"meeting_id": meeting_id, "connection_id": connection_id},
    )

    if not result:
        new_meeting.status = MeetingStatus.FAILED.value
        await db.commit()
        await publish_meeting_status_change(meeting_id, MeetingStatus.FAILED.value, redis_client, req.platform.value, native_meeting_id, current_user.id)
        raise HTTPException(status_code=500, detail="Failed to start bot container")

    # Record session start
    try:
        async with async_session_local() as session_db:
            new_session = MeetingSession(
                meeting_id=meeting_id,
                session_uid=connection_id,
                session_start_time=datetime.now(timezone.utc),
            )
            session_db.add(new_session)
            await session_db.commit()
    except Exception as e:
        logger.error(f"Failed to record session start for meeting {meeting_id}: {e}")

    # Update meeting with container info
    container_name = result.get("name", "")
    # v0.10.5 R1 fix — store NAME as bot_container_id, not container_id.
    # runtime-api state is keyed by name in Redis (api.py:315 GET
    # /containers/{name} → state.get_container(redis, name)). Storing the
    # container_id instead caused every lookup to 404, routing user-DELETE
    # through the Pack J no-container branch (no stop signal). Same swap
    # applied at lines 751, 828.
    new_meeting.bot_container_id = container_name or result.get("container_id")
    await db.commit()
    await db.refresh(new_meeting)

    # Register container in Redis for gateway VNC proxy (keyed by meeting ID)
    if redis_client:
        await redis_client.set(
            f"browser_session:{meeting_id}",
            json.dumps({"container_name": container_name, "meeting_id": meeting_id, "user_id": current_user.id}),
            ex=86400,
        )

    # Schedule bot timeout job (max_bot_time enforcement via scheduler)
    scheduler_job_id = await _schedule_bot_timeout(
        meeting_id=meeting_id,
        user_id=current_user.id,
        platform=req.platform.value,
        native_meeting_id=native_meeting_id,
        max_bot_time_ms=resolved_max_bot_time,
    )
    if scheduler_job_id:
        current_data = dict(new_meeting.data or {})
        current_data["scheduler_job_id"] = scheduler_job_id
        new_meeting.data = current_data
        await db.commit()
        await db.refresh(new_meeting)

    return MeetingResponse.model_validate(new_meeting)


@router.post("/internal/browser-sessions/{token}/save")
async def save_browser_session(token: str):
    """Save browser session storage to S3 via Redis command."""
    if not redis_client:
        raise HTTPException(status_code=500, detail="Redis not available")

    # Look up container name from session token stored in Redis
    session_data = await redis_client.get(f"browser_session:{token}")
    if not session_data:
        raise HTTPException(status_code=404, detail="Browser session not found")

    try:
        session = json.loads(session_data)
        container_name = session.get("container_name")
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(status_code=500, detail="Invalid session data")

    if not container_name:
        raise HTTPException(status_code=500, detail="Session missing container_name")

    # Try container_name channel first, fall back to 'default' (browser sessions
    # use config.container_name || 'default' as their Redis channel).
    channels_to_try = [f"browser_session:{container_name}"]
    if container_name != "default":
        channels_to_try.append("browser_session:default")

    channel = None
    for ch in channels_to_try:
        listeners = await redis_client.publish(ch, "save_storage")
        if listeners > 0:
            channel = ch
            break

    if not channel:
        raise HTTPException(status_code=404, detail="No browser session listening")

    # Subscribe to listen for the response
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)

    # Wait for save_storage:done or save_storage:error response
    try:
        for _ in range(120):  # 120 second timeout (S3 sync can take a while)
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not msg:
                continue
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            if data == "save_storage:done":
                return {"message": "Storage saved successfully"}
            if isinstance(data, str) and data.startswith("save_storage:error:"):
                error_msg = data[len("save_storage:error:"):]
                raise HTTPException(status_code=500, detail=f"Save failed: {error_msg}")
        raise HTTPException(status_code=504, detail="Save timed out")
    finally:
        await pubsub.unsubscribe(channel)


@router.delete("/internal/browser-sessions/{user_id}/storage")
async def delete_browser_storage(user_id: int):
    """Delete stored browser data from S3 for a user via MinIO API."""
    import boto3
    from botocore.config import Config as BotoConfig

    minio_endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
    minio_secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    s3_endpoint = f"{'https' if minio_secure else 'http'}://{minio_endpoint}"
    s3_bucket = os.environ.get("MINIO_BUCKET", "vexa-recordings")
    prefix = f"users/{user_id}/browser-userdata/"

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY", ""),
            aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY", ""),
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )

        # List and delete all objects under the prefix
        deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            delete_keys = [{"Key": obj["Key"]} for obj in objects]
            s3.delete_objects(Bucket=s3_bucket, Delete={"Objects": delete_keys})
            deleted += len(delete_keys)

        return {"message": f"Deleted {deleted} files for user {user_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


@router.get(
    "/bots",
    summary="List recent meetings/bots for the authenticated user",
    dependencies=[Depends(get_user_and_token)],
)
async def list_user_bots(
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    status: Optional[str] = None,
    include: Optional[str] = None,  # v0.10.5 Pack L — opt-in full-data backward-compat (?include=data)
    platform: Optional[str] = None,
):
    """Returns recent meetings (all statuses) from the database."""
    _, current_user = auth_data
    stmt = select(Meeting).where(Meeting.user_id == current_user.id)
    if search:
        q = f"%{search}%"
        stmt = stmt.where(
            (Meeting.platform_specific_id.ilike(q))
            | (Meeting.data["name"].astext.ilike(q))
            | (Meeting.data["title"].astext.ilike(q))
        )
    if status:
        stmt = stmt.where(Meeting.status == status)
    if platform:
        stmt = stmt.where(Meeting.platform == platform)
    stmt = stmt.order_by(desc(Meeting.created_at)).offset(offset).limit(limit + 1)
    meetings = (await db.execute(stmt)).scalars().all()
    has_more = len(meetings) > limit
    meetings = meetings[:limit]
    # v0.10.5 Pack L — slim list endpoint (#263 + #264).
    #
    # OLD shape returned `m.data or {}` — full JSONB blob with
    # status_transition[], recordings[], webhook_deliveries[], etc.
    # ~35 KB per meeting; default limit=50 → ~1.7 MB per /meetings page
    # load. Beyond perf, this is a DoS vector at scale and bad REST hygiene
    # (list view should be summary; detail endpoint returns full data).
    #
    # Audited dashboard usage in services/dashboard/src/components/meetings/
    # meeting-card.tsx + ai-chat-panel.tsx + export.ts. The list view actually
    # consumes: name/title, completion_reason, participants[:3], notes (preview),
    # languages, last status_transition entry, has_recording.
    #
    # Backward-compat: ?include=data restores the old behavior (full blob);
    # opt-in for callers that genuinely need it. Default off.
    include_full_data = include == "data"

    def _data_summary(d: dict) -> dict:
        d = d or {}
        participants = d.get("participants") or []
        notes = d.get("notes")
        transitions = d.get("status_transition") or []
        return {
            "name": d.get("name") or d.get("title"),
            "completion_reason": d.get("completion_reason"),
            "participants": participants[:3],
            "participants_count": len(participants),
            "notes_preview": (notes[:120] if isinstance(notes, str) else None),
            "languages": d.get("languages"),
            "last_transition": transitions[-1] if transitions else None,
            "has_recording": bool(d.get("recordings")),
        }

    return {
        "meetings": [
            {
                "id": m.id,
                "platform": m.platform,
                "native_meeting_id": m.platform_specific_id,
                "status": m.status,
                "bot_container_id": m.bot_container_id,
                "start_time": m.start_time.isoformat() if m.start_time else None,
                "end_time": m.end_time.isoformat() if m.end_time else None,
                "data": (m.data or {}) if include_full_data else _data_summary(m.data),
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            }
            for m in meetings
        ],
        "has_more": has_more,
    }


@router.get(
    "/bots/id/{meeting_id}",
    summary="Get a single meeting by database ID",
    dependencies=[Depends(get_user_and_token)],
)
async def get_bot_by_id(
    meeting_id: int,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Returns a single meeting owned by the authenticated user."""
    _, current_user = auth_data
    stmt = select(Meeting).where(
        and_(Meeting.id == meeting_id, Meeting.user_id == current_user.id)
    )
    meeting = (await db.execute(stmt)).scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {
        "id": meeting.id,
        "platform": meeting.platform,
        "native_meeting_id": meeting.platform_specific_id,
        "status": meeting.status,
        "bot_container_id": meeting.bot_container_id,
        "start_time": meeting.start_time.isoformat() if meeting.start_time else None,
        "end_time": meeting.end_time.isoformat() if meeting.end_time else None,
        "data": meeting.data or {},
        "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
        "updated_at": meeting.updated_at.isoformat() if meeting.updated_at else None,
    }


@router.get(
    "/bots/status",
    response_model=BotStatusResponse,
    summary="Get status of running bot containers for the authenticated user",
    dependencies=[Depends(get_user_and_token)],
)
async def get_user_bots_status(
    auth_data: tuple = Depends(get_user_and_token),
):
    """Returns {running_bots: [...]} — bot status for the authenticated user."""
    _, current_user = auth_data
    try:
        running_bots = await _get_running_bots_from_runtime(current_user.id)
        return BotStatusResponse(running_bots=running_bots)
    except Exception as e:
        logger.error(f"Error fetching bot status for user {current_user.id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve bot status")


@router.put(
    "/bots/{platform}/{native_meeting_id}/config",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update configuration for an active bot",
    dependencies=[Depends(get_user_and_token)],
)
async def update_bot_config(
    platform: Platform,
    native_meeting_id: str,
    req: MeetingConfigUpdate,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data

    stmt = (
        select(Meeting)
        .where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id,
            Meeting.status == MeetingStatus.ACTIVE.value,
        )
        .order_by(Meeting.created_at.desc())
    )
    active_meeting = (await db.execute(stmt)).scalars().first()

    if not active_meeting:
        existing_stmt = (
            select(Meeting.status)
            .where(Meeting.user_id == current_user.id, Meeting.platform == platform.value, Meeting.platform_specific_id == native_meeting_id)
            .order_by(Meeting.created_at.desc())
            .limit(1)
        )
        existing_status = (await db.execute(existing_stmt)).scalars().first()
        if existing_status:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Meeting found but not active (status: '{existing_status}')")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active meeting found")

    if not redis_client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")

    command = {
        "action": "reconfigure",
        "meeting_id": active_meeting.id,
        "language": req.language,
        "task": req.task,
        "allowed_languages": req.allowed_languages,
    }
    channel = f"bot_commands:meeting:{active_meeting.id}"
    await redis_client.publish(channel, json.dumps(command))

    return {"message": "Reconfiguration request accepted and sent to the bot."}


@router.delete(
    "/bots/{platform}/{native_meeting_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request stop for a bot",
    dependencies=[Depends(get_user_and_token)],
)
async def stop_bot(
    platform: Platform,
    native_meeting_id: str,
    background_tasks: BackgroundTasks,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    platform_value = platform.value

    stmt = (
        select(Meeting)
        .where(Meeting.user_id == current_user.id, Meeting.platform == platform_value, Meeting.platform_specific_id == native_meeting_id)
        .order_by(desc(Meeting.created_at))
    )
    all_meetings = (await db.execute(stmt)).scalars().all()

    if not all_meetings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No meeting found to stop.")

    non_terminal = [m for m in all_meetings if m.status not in [MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]]
    if not non_terminal:
        return {"message": f"Meeting already {all_meetings[0].status}."}

    for meeting in non_terminal:
        # Resolve container name: DB first, fallback to runtime API lookup
        container_name = meeting.bot_container_id
        if not container_name:
            try:
                client = _get_httpx_client()
                resp = await client.get(
                    f"{RUNTIME_API_URL}/containers",
                    params={"user_id": str(current_user.id), "profile": "meeting"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    for c in resp.json():
                        meta = c.get("metadata") or {}
                        if meta.get("meeting_id") == meeting.id and c.get("status") == "running":
                            container_name = c.get("name")
                            break
            except Exception as e:
                logger.warning(f"Runtime API lookup failed for meeting {meeting.id}: {e}")

        # v0.10.5 — Validate the resolved container_name actually exists at
        # runtime-api before treating it as live. Symptom (lite meeting 30,
        # 2026-04-27): meeting.bot_container_id pointed at "519" but the
        # bot container had been wiped by a stack redeploy. Without this
        # check, code assumed bot was alive, sent leave-via-Redis (no bot
        # listening), enqueued a Pack D.2 outbox stop (succeeded — runtime-
        # api logged "Process 519 not in registry"), and left the meeting
        # in `stopping` indefinitely. Pack E.3.2 stale-stopping sweep would
        # eventually reap it after 5 min, but the user-visible UX is "stuck
        # in stopping". Same Pack J classifier branch as the truly
        # no-container case — a stale bot_container_id has identical
        # semantics: there's no live process to ask, so we apply the
        # data-driven classifier to the meeting state we already have.
        if container_name:
            try:
                client = _get_httpx_client()
                cresp = await client.get(
                    f"{RUNTIME_API_URL}/containers/{container_name}",
                    timeout=5.0,
                )
                if cresp.status_code == 404 or (
                    cresp.status_code == 200 and (cresp.json() or {}).get("status") != "running"
                ):
                    logger.info(
                        f"DELETE meeting {meeting.id}: bot_container_id={container_name!r} "
                        f"is stale (runtime-api reports gone/not-running) — routing through "
                        f"no-container Pack J branch"
                    )
                    container_name = None
            except Exception as e:
                # Runtime API unreachable — DON'T null out the container_name
                # (could be a transient network blip). Fall through to the
                # leave-via-Redis path; Pack E.3.2 sweep is the safety net.
                logger.warning(
                    f"DELETE meeting {meeting.id}: runtime-api inspect failed for "
                    f"{container_name!r} ({e!r}) — keeping live-container assumption"
                )

        if not container_name:
            # v0.10.5 Pack X finding (helm meeting 8, 2026-04-27): when
            # runtime-api lookup fails, the previous code went directly
            # COMPLETED + STOPPED — bypassing Pack J classifier. A bot
            # active 60s+ with transcribe_enabled and 0 transcripts gets
            # silently classified as completed/stopped, exactly the
            # #255 silent class. Fix: route through _classify_stopped_exit
            # so every DELETE path produces the same data-driven verdict.
            from .callbacks import _classify_stopped_exit
            target_status, classified_reason = await _classify_stopped_exit(
                meeting, db, MeetingCompletionReason.STOPPED
            )
            old_status = meeting.status
            success = await update_meeting_status(meeting, target_status, db, completion_reason=classified_reason)
            if success:
                await publish_meeting_status_change(meeting.id, target_status.value, redis_client, platform_value, native_meeting_id, meeting.user_id)
                await schedule_status_webhook_task(
                    meeting=meeting, background_tasks=background_tasks,
                    old_status=old_status, new_status=target_status.value,
                    reason="User requested stop (no container)", transition_source="user_stop",
                )
            background_tasks.add_task(run_all_tasks, meeting.id)
            continue

        # Fast-path for very recent pre-active meetings
        try:
            seconds_since_created = (datetime.utcnow() - meeting.created_at).total_seconds() if meeting.created_at else None
        except Exception:
            seconds_since_created = None

        if meeting.status in [MeetingStatus.REQUESTED.value, MeetingStatus.JOINING.value, MeetingStatus.AWAITING_ADMISSION.value] and seconds_since_created is not None and seconds_since_created < 5:
            if meeting.data is None:
                meeting.data = {}
            meeting.data["stop_requested"] = True
            await db.commit()
            background_tasks.add_task(_delayed_container_stop, container_name, meeting.id, 0)
            # v0.10.5 Pack X — same Pack J routing as the no-container
            # branch above. Fast-path (pre-active, <5s old) will
            # naturally classify as STOPPED_BEFORE_ADMISSION via Pack J
            # because reached_active is False — semantically correct.
            from .callbacks import _classify_stopped_exit
            target_status, classified_reason = await _classify_stopped_exit(
                meeting, db, MeetingCompletionReason.STOPPED
            )
            old_status = meeting.status
            success = await update_meeting_status(meeting, target_status, db, completion_reason=classified_reason)
            if success:
                await publish_meeting_status_change(meeting.id, target_status.value, redis_client, platform_value, native_meeting_id, meeting.user_id)
                await schedule_status_webhook_task(
                    meeting=meeting, background_tasks=background_tasks,
                    old_status=old_status, new_status=target_status.value,
                    reason="User requested stop (fast-path)", transition_source="user_stop",
                )
            background_tasks.add_task(run_all_tasks, meeting.id)
            continue

        # Send leave command via Redis
        if redis_client:
            try:
                command_channel = f"bot_commands:meeting:{meeting.id}"
                await redis_client.publish(command_channel, json.dumps({"action": "leave", "meeting_id": meeting.id}))
            except Exception as e:
                logger.error(f"Failed to publish leave command: {e}")

        # Schedule delayed stop
        stop_delay = 0 if platform_value == "browser_session" else BOT_STOP_DELAY_SECONDS
        background_tasks.add_task(_delayed_container_stop, container_name, meeting.id, stop_delay)

        # Set stop_requested flag so late bot status_change callbacks (e.g.
        # `joining` arriving after user DELETE) are returned as "ignored"
        # instead of failing the bot with "Invalid status transition
        # 'stopping' -> 'joining'". Without this flag, the bot retries 3×
        # and ends up writing the meeting status as FAILED from its own
        # error callback, even though the user-requested stop was clean.
        # Mirrors the fast-path at line ~1426.
        if meeting.data is None:
            meeting.data = {}
        meeting.data["stop_requested"] = True
        attributes.flag_modified(meeting, "data")

        # Update to STOPPING
        old_status = meeting.status
        await update_meeting_status(meeting, MeetingStatus.STOPPING, db, transition_reason="User requested stop")
        await publish_meeting_status_change(meeting.id, "stopping", redis_client, platform_value, native_meeting_id, meeting.user_id)
        await schedule_status_webhook_task(
            meeting=meeting, background_tasks=background_tasks,
            old_status=old_status, new_status="stopping",
            reason="User requested stop", transition_source="user_stop",
        )

    return {"message": "Stop request accepted and is being processed."}


@router.delete(
    "/bots/internal/timeout/{meeting_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Internal: scheduler-triggered bot timeout",
    include_in_schema=False,
)
async def scheduler_timeout_stop(
    meeting_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Called by the scheduler when max_bot_time expires.

    No user auth required — this is an internal endpoint called by runtime-api scheduler.
    Transitions the bot to stopping → completed with reason=max_bot_time_exceeded.
    """
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Already terminal — idempotent, no error
    if meeting.status in [MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]:
        return {"message": f"Meeting already in terminal state: {meeting.status}"}

    # Already stopping — let it complete naturally
    if meeting.status == MeetingStatus.STOPPING.value:
        return {"message": "Meeting already stopping"}

    platform_value = meeting.platform
    native_meeting_id = meeting.platform_specific_id

    # Find container to stop
    container_name = meeting.bot_container_id
    if not container_name:
        # No container — just complete the meeting
        old_status = meeting.status
        success = await update_meeting_status(
            meeting, MeetingStatus.COMPLETED, db,
            completion_reason=MeetingCompletionReason.MAX_BOT_TIME_EXCEEDED,
            transition_reason="scheduler_timeout",
        )
        if success:
            await publish_meeting_status_change(
                meeting.id, MeetingStatus.COMPLETED.value, redis_client,
                platform_value, native_meeting_id, meeting.user_id,
            )
            await schedule_status_webhook_task(
                meeting=meeting, background_tasks=background_tasks,
                old_status=old_status, new_status=MeetingStatus.COMPLETED.value,
                reason="max_bot_time_exceeded (no container)", transition_source="scheduler_timeout",
            )
            background_tasks.add_task(run_all_tasks, meeting.id)
        return {"message": "Bot timed out (no container)"}

    # Send leave command
    if redis_client:
        try:
            command_channel = f"bot_commands:meeting:{meeting.id}"
            await redis_client.publish(command_channel, json.dumps({"action": "leave", "meeting_id": meeting.id, "reason": "max_bot_time_exceeded"}))
        except Exception as e:
            logger.error(f"Failed to publish leave command for timeout: {e}")

    # Store pending completion reason so the delayed stop finalizer uses it
    current_data = dict(meeting.data or {})
    current_data["pending_completion_reason"] = MeetingCompletionReason.MAX_BOT_TIME_EXCEEDED.value
    meeting.data = current_data
    await db.commit()

    # Schedule delayed container stop
    background_tasks.add_task(_delayed_container_stop, container_name, meeting.id, BOT_STOP_DELAY_SECONDS)

    # Transition to STOPPING, then the delayed stop finalizer will complete it
    old_status = meeting.status
    await update_meeting_status(
        meeting, MeetingStatus.STOPPING, db,
        transition_reason="scheduler_timeout_max_bot_time",
        transition_metadata={"timeout_trigger": "scheduler"},
    )
    await publish_meeting_status_change(
        meeting.id, "stopping", redis_client,
        platform_value, native_meeting_id, meeting.user_id,
    )
    await schedule_status_webhook_task(
        meeting=meeting, background_tasks=background_tasks,
        old_status=old_status, new_status="stopping",
        reason="max_bot_time_exceeded", transition_source="scheduler_timeout",
    )

    return {"message": "Bot timeout triggered, stopping."}


# --- Recording Config ---

class RecordingConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    capture_modes: Optional[List[str]] = None


@router.get(
    "/recording-config",
    summary="Get recording configuration for the authenticated user",
    dependencies=[Depends(get_user_and_token)],
)
async def get_recording_config(
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    from admin_models.models import User
    user = (await db.execute(select(User).where(User.id == current_user.id))).scalars().first()
    if not user:
        return {
            "enabled": os.getenv("RECORDING_ENABLED", "true").lower() == "true",
            "capture_modes": os.getenv("CAPTURE_MODES", "audio").split(","),
        }
    data = user.data or {}
    rc = data.get("recording_config", {})
    return {
        "enabled": rc.get("enabled", os.getenv("RECORDING_ENABLED", "true").lower() == "true"),
        "capture_modes": rc.get("capture_modes", os.getenv("CAPTURE_MODES", "audio").split(",")),
    }


@router.put(
    "/recording-config",
    summary="Update recording configuration for the authenticated user",
    dependencies=[Depends(get_user_and_token)],
)
async def update_recording_config(
    req: RecordingConfigRequest,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    from admin_models.models import User
    user = (await db.execute(select(User).where(User.id == current_user.id))).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    data = dict(user.data or {})
    rc = dict(data.get("recording_config", {}))
    if req.enabled is not None:
        rc["enabled"] = req.enabled
    if req.capture_modes is not None:
        rc["capture_modes"] = req.capture_modes
    data["recording_config"] = rc
    user.data = data
    attributes.flag_modified(user, "data")
    await db.commit()
    return rc


# --- Deferred Transcription ---

def _map_speakers_to_segments(speaker_events, segments):
    """Map speaker names to transcription segments using speaking_start/stop events."""
    ranges = []
    active = {}
    for event in sorted(speaker_events, key=lambda e: e.get('relative_timestamp_ms', 0)):
        name = event.get('participant_name', 'Unknown')
        ts_sec = event.get('relative_timestamp_ms', 0) / 1000.0
        etype = event.get('event_type', '')
        if etype in ('SPEAKER_START', 'speaking_start'):
            active[name] = ts_sec
        elif etype in ('SPEAKER_END', 'speaking_stop') and name in active:
            ranges.append((name, active.pop(name), ts_sec))
    for name, start in active.items():
        ranges.append((name, start, float('inf')))

    for seg in segments:
        best_speaker = "Unknown"
        best_overlap = 0
        for speaker, r_start, r_end in ranges:
            overlap = max(0, min(seg['end'], r_end) - max(seg['start'], r_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        seg['speaker'] = best_speaker
    return segments


class TranscribeRequest(BaseModel):
    language: Optional[str] = Field(None, description="Language code (e.g., 'en'). If omitted, auto-detect.")


class TranscribeResponse(BaseModel):
    meeting_id: int
    segment_count: int
    message: str


@router.post(
    "/meetings/{meeting_id}/transcribe",
    summary="Trigger deferred transcription for a completed meeting",
    response_model=TranscribeResponse,
    dependencies=[Depends(get_user_and_token)],
)
async def transcribe_meeting(
    meeting_id: int,
    req: TranscribeRequest = TranscribeRequest(),
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == current_user.id)
    )).scalars().first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.status not in ("completed", "failed"):
        raise HTTPException(status_code=400, detail=f"Meeting status is '{meeting.status}', expected 'completed' or 'failed'")

    # 0. Check if realtime segments already exist — deferred would create duplicates
    from .models import Recording, MediaFile, Transcription
    existing_count = (await db.execute(
        select(func.count(Transcription.id)).where(Transcription.meeting_id == meeting_id)
    )).scalar() or 0
    if existing_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"This meeting is already transcribed ({existing_count} segments). Multiple transcripts per meeting not implemented.",
        )

    # 1. Find recording — check recordings table first, then meeting.data (legacy)
    from .storage import create_storage_client
    import subprocess
    import tempfile

    storage_path = None
    media_format = "webm"
    session_uid = None

    recording = (await db.execute(
        select(Recording).where(Recording.meeting_id == meeting_id, Recording.status == "completed")
    )).scalars().first()
    if recording:
        media_file = (await db.execute(
            select(MediaFile).where(
                MediaFile.recording_id == recording.id,
                MediaFile.type.in_(["audio", "video"]),
            )
        )).scalars().first()
        if media_file:
            storage_path = media_file.storage_path
            media_format = media_file.format
            session_uid = recording.session_uid

    # Fallback: check meeting.data['recordings'] (legacy inline storage)
    if not storage_path:
        meeting_data = meeting.data or {}
        recs = meeting_data.get("recordings", [])
        for rec in (recs if isinstance(recs, list) else [recs]):
            if rec.get("status") == "completed":
                for mf in rec.get("media_files", []):
                    if mf.get("type") in ("audio", "video") and mf.get("storage_path"):
                        storage_path = mf["storage_path"]
                        media_format = mf.get("format", "webm")
                        session_uid = rec.get("session_uid")
                        break
            if storage_path:
                break

    if not storage_path:
        raise HTTPException(status_code=404, detail="No completed recording with audio found for this meeting")

    # 2. Download audio from storage
    try:
        storage = create_storage_client()
        audio_data = storage.download_file(storage_path)
    except Exception as e:
        logger.error(f"Failed to download recording for meeting {meeting_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download recording: {e}")

    # 3. Convert to WAV if needed (Whisper requires PCM-decodable formats)
    if media_format in ("webm", "opus", "ogg", "mp4", "m4a"):
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{media_format}", delete=False) as src:
                src.write(audio_data)
                src_path = src.name
            dst_path = src_path.rsplit(".", 1)[0] + ".wav"
            result = subprocess.run(
                ["ffmpeg", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path, "-y"],
                capture_output=True, timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg conversion failed: {result.stderr.decode()[:500]}")
                raise HTTPException(status_code=500, detail="Audio conversion failed")
            with open(dst_path, "rb") as f:
                audio_data = f.read()
            media_format = "wav"
            os.unlink(src_path)
            os.unlink(dst_path)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=500, detail="Audio conversion timed out")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Audio conversion error: {e}")
            raise HTTPException(status_code=500, detail=f"Audio conversion error: {e}")

    # 4. Send to transcription service
    tx_url = os.environ.get("TRANSCRIPTION_SERVICE_URL", "")
    tx_token = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
    if not tx_url:
        raise HTTPException(status_code=503, detail="TRANSCRIPTION_SERVICE_URL not configured")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {"file": (f"recording.{media_format}", audio_data, f"audio/{media_format}")}
            form_data = {"model": "large-v3-turbo"}
            if req.language:
                form_data["language"] = req.language
            headers = {}
            if tx_token:
                headers["Authorization"] = f"Bearer {tx_token}"

            resp = await client.post(
                tx_url,
                files=files,
                data=form_data,
                headers=headers,
            )
            resp.raise_for_status()
            tx_result = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Transcription service error: {e.response.status_code} {e.response.text}")
        raise HTTPException(status_code=502, detail=f"Transcription service error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Transcription service request failed: {e}")
        raise HTTPException(status_code=502, detail=f"Transcription service unavailable: {e}")

    # 5. Parse and filter segments
    segments = tx_result.get("segments", [])
    segments = [s for s in segments if 'start' in s and 'end' in s and s.get('text', '').strip()]
    detected_language = tx_result.get("language", req.language or "unknown")

    # 6. Map speakers using speaker_events from meeting.data
    meeting_data = meeting.data or {}
    speaker_events = meeting_data.get("speaker_events", [])
    if speaker_events:
        segments = _map_speakers_to_segments(speaker_events, segments)
        logger.info(f"Mapped {len(speaker_events)} speaker events to {len(segments)} segments")

    # 7. Store segments in transcriptions table
    stored = 0
    for seg in segments:
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        text = seg.get("text", "").strip()
        if not text:
            continue
        segment_id = f"deferred:{meeting_id}:{start:.3f}"
        t = Transcription(
            meeting_id=meeting_id,
            start_time=start,
            end_time=end,
            text=text,
            speaker=seg.get("speaker"),
            language=detected_language,
            session_uid=session_uid,
            segment_id=segment_id,
            created_at=datetime.utcnow(),
        )
        db.add(t)
        stored += 1

    # 8. Update meeting.data with transcribed_at timestamp
    meeting_data["transcribed_at"] = datetime.utcnow().isoformat()
    meeting.data = meeting_data
    await db.commit()

    speakers = list(set(seg.get("speaker", "Unknown") for seg in segments if seg.get("text", "").strip()))
    logger.info(f"Deferred transcription for meeting {meeting_id}: {stored} segments, speakers={speakers}")

    return TranscribeResponse(
        meeting_id=meeting_id,
        segment_count=stored,
        message=f"Transcribed {stored} segments from recording ({len(speakers)} speakers: {', '.join(speakers)})",
    )
