"""Internal callback handlers — /bots/internal/callback/*.

These endpoints receive status updates from vexa-bot containers.
Payload shapes are frozen (see tests/contracts/test_callback_contracts.py).
"""

import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .database import get_db
from .models import Meeting, MeetingSession
from .schemas import (
    MeetingStatus,
    MeetingCompletionReason,
    MeetingFailureStage,
)

from .meetings import (
    update_meeting_status,
    publish_meeting_status_change,
    schedule_status_webhook_task,
    get_redis,
)
from .post_meeting import run_all_tasks

logger = logging.getLogger("meeting_api.callbacks")

router = APIRouter()


# ---------------------------------------------------------------------------
# Frozen payload models (must match tests/contracts/test_callback_contracts.py)
# ---------------------------------------------------------------------------

class BotExitCallbackPayload(BaseModel):
    connection_id: str = Field(..., description="The connectionId (session_uid) of the exiting bot.")
    exit_code: int = Field(..., description="The exit code of the bot process.")
    reason: Optional[str] = Field("self_initiated_leave")
    error_details: Optional[Dict[str, Any]] = Field(None)
    platform_specific_error: Optional[str] = Field(None)
    completion_reason: Optional[MeetingCompletionReason] = Field(None)
    failure_stage: Optional[MeetingFailureStage] = Field(None)


class BotStartupCallbackPayload(BaseModel):
    connection_id: str = Field(...)
    container_id: str = Field(...)


class BotStatusChangePayload(BaseModel):
    connection_id: str = Field(...)
    container_id: Optional[str] = Field(None)
    status: MeetingStatus = Field(...)
    reason: Optional[str] = Field(None)
    exit_code: Optional[int] = Field(None)
    error_details: Optional[Dict[str, Any]] = Field(None)
    platform_specific_error: Optional[str] = Field(None)
    completion_reason: Optional[MeetingCompletionReason] = Field(None)
    failure_stage: Optional[MeetingFailureStage] = Field(None)
    timestamp: Optional[str] = Field(None)
    speaker_events: Optional[List[Dict]] = Field(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _find_meeting_by_session(session_uid: str, db: AsyncSession) -> tuple[Optional[MeetingSession], Optional[Meeting]]:
    session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
    meeting_session = (await db.execute(session_stmt)).scalars().first()
    if not meeting_session:
        return None, None
    meeting = await db.get(Meeting, meeting_session.meeting_id)
    return meeting_session, meeting


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/bots/internal/callback/exited", status_code=200, include_in_schema=False)
async def bot_exit_callback(
    payload: BotExitCallbackPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    redis_client = get_redis()
    session_uid = payload.connection_id
    exit_code = payload.exit_code

    try:
        _, meeting = await _find_meeting_by_session(session_uid, db)
        if not meeting:
            logger.error(f"Exit callback: session {session_uid} not found")
            return {"status": "error", "detail": "Meeting session not found"}

        meeting_id = meeting.id

        if exit_code == 0:
            # Check pending_completion_reason (set by scheduler timeout) — overrides bot-reported reason
            pending = (meeting.data or {}).get("pending_completion_reason") if isinstance(meeting.data, dict) else None
            if pending:
                try:
                    provided_reason = MeetingCompletionReason(pending)
                except ValueError:
                    provided_reason = payload.completion_reason or MeetingCompletionReason.STOPPED
            else:
                provided_reason = payload.completion_reason or MeetingCompletionReason.STOPPED
            meta = {"exit_code": exit_code}
            if payload.platform_specific_error:
                meta["platform_specific_error"] = payload.platform_specific_error
            success = await update_meeting_status(
                meeting, MeetingStatus.COMPLETED, db,
                completion_reason=provided_reason,
                error_details=payload.error_details if isinstance(payload.error_details, str) else (json.dumps(payload.error_details) if payload.error_details else None),
                transition_reason=payload.reason,
                transition_metadata=meta,
            )
            new_status = MeetingStatus.COMPLETED.value if success else None
        else:
            provided_stage = payload.failure_stage or MeetingFailureStage.ACTIVE
            error_msg = f"Bot exited with code {exit_code}"
            if payload.reason:
                error_msg += f"; reason: {payload.reason}"
            meta = {"exit_code": exit_code}
            if payload.platform_specific_error:
                meta["platform_specific_error"] = payload.platform_specific_error
            success = await update_meeting_status(
                meeting, MeetingStatus.FAILED, db,
                failure_stage=provided_stage,
                error_details=error_msg,
                transition_reason=payload.reason,
                transition_metadata=meta,
            )
            new_status = MeetingStatus.FAILED.value if success else None

            if success and (payload.error_details or payload.platform_specific_error):
                if not meeting.data:
                    meeting.data = {}
                updated_data = dict(meeting.data)
                updated_data["last_error"] = {
                    "exit_code": exit_code,
                    "reason": payload.reason,
                    "timestamp": datetime.utcnow().isoformat(),
                    "error_details": payload.error_details,
                    "platform_specific_error": payload.platform_specific_error,
                }
                meeting.data = updated_data

        if not success:
            return {"status": "error", "detail": "Failed to update meeting status"}

        # Persist chat messages from Redis
        if redis_client:
            try:
                chat_raw = await redis_client.lrange(f"meeting:{meeting_id}:chat_messages", 0, -1)
                if chat_raw:
                    messages = []
                    for raw in chat_raw:
                        try:
                            messages.append(json.loads(raw))
                        except json.JSONDecodeError:
                            pass
                    if messages:
                        if not meeting.data:
                            meeting.data = {}
                        updated = dict(meeting.data)
                        updated["chat_messages"] = messages
                        meeting.data = updated
            except Exception as e:
                logger.warning(f"Failed to persist chat messages for meeting {meeting_id}: {e}")

        meeting.end_time = datetime.utcnow()
        await db.commit()
        await db.refresh(meeting)

        # Clean up browser_session Redis keys
        if redis_client:
            session_token = (meeting.data or {}).get("session_token")
            if session_token:
                await redis_client.delete(f"browser_session:{session_token}")
            await redis_client.delete(f"browser_session:{meeting.id}")

        if new_status:
            await publish_meeting_status_change(meeting.id, new_status, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

        background_tasks.add_task(run_all_tasks, meeting.id)

        return {"status": "callback processed", "meeting_id": meeting.id, "final_status": meeting.status}

    except Exception as e:
        logger.error(f"Exit callback error: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail="Internal error processing exit callback")


@router.post("/bots/internal/callback/started", status_code=200, include_in_schema=False)
async def bot_startup_callback(
    payload: BotStartupCallbackPayload,
    db: AsyncSession = Depends(get_db),
):
    redis_client = get_redis()
    _, meeting = await _find_meeting_by_session(payload.connection_id, db)
    if not meeting:
        return {"status": "error", "detail": "Meeting session not found"}

    if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
        return {"status": "ignored", "detail": "stop requested"}

    old_status = meeting.status
    if meeting.status in [MeetingStatus.REQUESTED.value, MeetingStatus.JOINING.value, MeetingStatus.AWAITING_ADMISSION.value, MeetingStatus.FAILED.value]:
        success = await update_meeting_status(meeting, MeetingStatus.ACTIVE, db)
        if success:
            if payload.container_id:
                meeting.bot_container_id = payload.container_id
            meeting.start_time = datetime.utcnow()
            await db.commit()
            await db.refresh(meeting)
    elif meeting.status == MeetingStatus.ACTIVE.value:
        if payload.container_id:
            meeting.bot_container_id = payload.container_id
            await db.commit()
            await db.refresh(meeting)

    if meeting.status == MeetingStatus.ACTIVE.value and old_status != MeetingStatus.ACTIVE.value:
        await publish_meeting_status_change(meeting.id, MeetingStatus.ACTIVE.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

    return {"status": "startup processed", "meeting_id": meeting.id, "meeting_status": meeting.status}


@router.post("/bots/internal/callback/joining", status_code=200, include_in_schema=False)
async def bot_joining_callback(
    payload: BotStartupCallbackPayload,
    db: AsyncSession = Depends(get_db),
):
    redis_client = get_redis()
    _, meeting = await _find_meeting_by_session(payload.connection_id, db)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting session not found")

    if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
        return {"status": "ignored", "detail": "stop requested"}

    success = await update_meeting_status(meeting, MeetingStatus.JOINING, db)
    if success:
        await publish_meeting_status_change(meeting.id, MeetingStatus.JOINING.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

    return {"status": "joining processed", "meeting_id": meeting.id, "meeting_status": meeting.status}


@router.post("/bots/internal/callback/awaiting_admission", status_code=200, include_in_schema=False)
async def bot_awaiting_admission_callback(
    payload: BotStartupCallbackPayload,
    db: AsyncSession = Depends(get_db),
):
    redis_client = get_redis()
    _, meeting = await _find_meeting_by_session(payload.connection_id, db)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting session not found")

    if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
        return {"status": "ignored", "detail": "stop requested"}

    success = await update_meeting_status(meeting, MeetingStatus.AWAITING_ADMISSION, db)
    if success:
        await publish_meeting_status_change(meeting.id, MeetingStatus.AWAITING_ADMISSION.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

    return {"status": "awaiting_admission processed", "meeting_id": meeting.id, "meeting_status": meeting.status}


@router.post("/bots/internal/callback/status_change", status_code=200, include_in_schema=False)
async def bot_status_change_callback(
    payload: BotStatusChangePayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Unified callback for all bot status changes."""
    redis_client = get_redis()
    new_status = payload.status
    reason = payload.reason

    _, meeting = await _find_meeting_by_session(payload.connection_id, db)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting session not found")

    await db.refresh(meeting)

    # Ignore non-terminal transitions if stop was requested
    if (meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested")
            and new_status not in [MeetingStatus.COMPLETED, MeetingStatus.FAILED]):
        return {"status": "ignored", "detail": "stop requested"}

    old_status = meeting.status
    success = None

    if new_status == MeetingStatus.COMPLETED:
        # Check pending_completion_reason (set by scheduler timeout) — overrides bot-reported reason
        effective_reason = payload.completion_reason
        pending = (meeting.data or {}).get("pending_completion_reason") if isinstance(meeting.data, dict) else None
        if pending:
            try:
                effective_reason = MeetingCompletionReason(pending)
            except ValueError:
                pass
        success = await update_meeting_status(meeting, MeetingStatus.COMPLETED, db, completion_reason=effective_reason)
        if success:
            meeting.end_time = datetime.utcnow()
            if payload.speaker_events:
                if not meeting.data:
                    meeting.data = {}
                d = dict(meeting.data)
                d["speaker_events"] = payload.speaker_events
                meeting.data = d
                attributes.flag_modified(meeting, "data")
            await db.commit()
            await db.refresh(meeting)
            background_tasks.add_task(run_all_tasks, meeting.id)

    elif new_status == MeetingStatus.FAILED:
        success = await update_meeting_status(
            meeting, MeetingStatus.FAILED, db,
            failure_stage=payload.failure_stage,
            error_details=str(payload.error_details) if payload.error_details else None,
        )
        if success:
            meeting.end_time = datetime.utcnow()
            if payload.error_details or payload.platform_specific_error:
                if not meeting.data:
                    meeting.data = {}
                meeting.data["last_error"] = {
                    "exit_code": payload.exit_code,
                    "reason": payload.reason,
                    "timestamp": datetime.utcnow().isoformat(),
                    "error_details": payload.error_details,
                    "platform_specific_error": payload.platform_specific_error,
                }
            await db.commit()
            await db.refresh(meeting)
            background_tasks.add_task(run_all_tasks, meeting.id)

    elif new_status == MeetingStatus.ACTIVE:
        if meeting.status in [MeetingStatus.REQUESTED.value, MeetingStatus.JOINING.value,
                              MeetingStatus.AWAITING_ADMISSION.value, MeetingStatus.FAILED.value,
                              MeetingStatus.NEEDS_HUMAN_HELP.value]:
            success = await update_meeting_status(meeting, MeetingStatus.ACTIVE, db)
            if success:
                if payload.container_id:
                    meeting.bot_container_id = payload.container_id
                meeting.start_time = datetime.utcnow()
                await db.commit()
                await db.refresh(meeting)
        elif meeting.status == MeetingStatus.ACTIVE.value:
            if payload.container_id:
                meeting.bot_container_id = payload.container_id
                await db.commit()
                await db.refresh(meeting)
            return {"status": "container_updated", "meeting_id": meeting.id, "meeting_status": meeting.status}
        else:
            # Status not in allowed pre-check list and not already ACTIVE — reject
            success = False

    elif new_status == MeetingStatus.NEEDS_HUMAN_HELP:
        success = await update_meeting_status(meeting, MeetingStatus.NEEDS_HUMAN_HELP, db)
        if success:
            if not meeting.data:
                meeting.data = {}
            d = dict(meeting.data)
            escalation_reason = payload.reason or "unknown"
            escalated_at = payload.timestamp or datetime.utcnow().isoformat()
            d["escalation"] = {
                "reason": escalation_reason,
                "escalated_at": escalated_at,
                "session_token": str(meeting.id),
                "vnc_url": f"/b/{meeting.id}",
            }
            meeting.data = d
            attributes.flag_modified(meeting, "data")

            # Ensure container is registered in Redis for gateway VNC proxy (by meeting ID)
            if redis_client:
                await redis_client.set(
                    f"browser_session:{meeting.id}",
                    json.dumps({"container_name": payload.container_id or meeting.bot_container_id, "meeting_id": meeting.id, "user_id": meeting.user_id, "escalation": True}),
                    ex=86400,
                )
            await db.commit()
            await db.refresh(meeting)

    else:
        # joining, awaiting_admission, etc.
        success = await update_meeting_status(meeting, new_status, db)
        if not success:
            return {"status": "error", "detail": "Failed to update meeting status"}

    # Fix 1: Return error when transition was rejected (success is False or None)
    if success is False:
        return {"status": "error", "detail": f"Invalid transition: {old_status} → {new_status.value}", "meeting_id": meeting.id, "meeting_status": meeting.status}

    # Publish status change
    if success or (new_status == MeetingStatus.ACTIVE and meeting.status == MeetingStatus.ACTIVE.value):
        publish_extra = None
        if new_status == MeetingStatus.NEEDS_HUMAN_HELP and meeting.data and "escalation" in meeting.data:
            publish_extra = {
                "escalation_reason": meeting.data["escalation"].get("reason"),
                "vnc_url": meeting.data["escalation"].get("vnc_url"),
                "escalated_at": meeting.data["escalation"].get("escalated_at"),
            }
        await publish_meeting_status_change(meeting.id, new_status.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id, extra_data=publish_extra)

    # Fix 3: Webhook gated on success — only fire for accepted transitions
    if success:
        await schedule_status_webhook_task(
            meeting=meeting,
            background_tasks=background_tasks,
            old_status=old_status,
            new_status=new_status.value,
            reason=reason,
            transition_source="bot_callback",
        )

    return {"status": "processed", "meeting_id": meeting.id, "meeting_status": meeting.status}
