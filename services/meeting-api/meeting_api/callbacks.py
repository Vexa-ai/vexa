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

from sqlalchemy import func

from .database import get_db
from .models import Meeting, MeetingSession, Transcription
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


# v0.10.5 Pack J — exit classification routing rule (#255 silent class).
#
# [PLATFORM] data on #255 showed 557 of 1183 (47%) `completed` meetings
# in 30d are actually misclassified — 432 pre-admission + 125 substantive
# (transcribe-enabled, ≥30s, 0 segments). The classifier already produces
# the correct completion_reason; the meeting-api callback handler ignored
# the signal and wrote status='completed' regardless. This helper closes
# the silent class by inspecting the same fields the data showed:
#   - reached_active (from status_transition[]) → distinguishes 432 class
#   - duration_seconds (from start_time/end_time) → 30s threshold
#   - transcribe_enabled (from data) → opt-out for recording-only mode
#   - transcription_count (count(*) from transcriptions table)
def _failure_stage_from_status(status: str) -> MeetingFailureStage:
    """Derive failure_stage from current meeting.status at write time.

    v0.10.5 FM-003: payload.failure_stage from the bot's gracefulLeave
    path can be stale (it reflects the bot's internal stage tracker, not
    the meeting's actual state). Server-side derivation removes the
    parallel-surface drift — the failure_stage value matches what
    status_transition[] would tell you anyway.
    """
    return {
        MeetingStatus.REQUESTED.value: MeetingFailureStage.REQUESTED,
        MeetingStatus.JOINING.value: MeetingFailureStage.JOINING,
        MeetingStatus.AWAITING_ADMISSION.value: MeetingFailureStage.AWAITING_ADMISSION,
        MeetingStatus.ACTIVE.value: MeetingFailureStage.ACTIVE,
    }.get(status, MeetingFailureStage.ACTIVE)


async def _classify_stopped_exit(
    meeting: Meeting,
    db: AsyncSession,
    requested_reason: MeetingCompletionReason,
) -> tuple[MeetingStatus, MeetingCompletionReason]:
    """Classify a stopped exit per Pack J's data-driven rules.

    Returns (target_status, completion_reason). When the meeting passes
    positive-proof-of-success, returns (COMPLETED, requested_reason).
    Otherwise routes to FAILED with the closest-fit prod-derived reason.
    """
    # Pack J.4 — every non-success completion_reason routes to FAILED.
    # [PLATFORM] data showed these were ALL being silently routed to
    # COMPLETED despite having explicit failure semantics:
    #   awaiting_admission_timeout (72), awaiting_admission_rejected (9),
    #   evicted (6), max_bot_time_exceeded (10), validation_error.
    # left_alone is debatable (bot legitimately left when alone); routes
    # to COMPLETED unless the data shows otherwise.
    _explicit_failure_reasons = {
        MeetingCompletionReason.AWAITING_ADMISSION_TIMEOUT,
        MeetingCompletionReason.AWAITING_ADMISSION_REJECTED,
        MeetingCompletionReason.EVICTED,
        MeetingCompletionReason.MAX_BOT_TIME_EXCEEDED,
        MeetingCompletionReason.VALIDATION_ERROR,
        MeetingCompletionReason.STOPPED_BEFORE_ADMISSION,
        MeetingCompletionReason.STOPPED_WITH_NO_AUDIO,
    }
    if requested_reason in _explicit_failure_reasons:
        return (MeetingStatus.FAILED, requested_reason)
    # LEFT_ALONE — bot left because everyone else left. Legitimate end of
    # meeting; user got their transcript. Stay COMPLETED.
    if requested_reason == MeetingCompletionReason.LEFT_ALONE:
        return (MeetingStatus.COMPLETED, requested_reason)
    # Only STOPPED reaches the deeper success-proof checks below.
    if requested_reason != MeetingCompletionReason.STOPPED:
        # Defensive: unknown reason. Mark FAILED rather than silent-completed.
        logger.warning(f"Pack J: unknown completion_reason {requested_reason!r} — defaulting to FAILED")
        return (MeetingStatus.FAILED, requested_reason)

    data = meeting.data or {}

    # Did the meeting ever reach active? Walk status_transition[] for it.
    transitions = data.get("status_transition") or []
    reached_active = any(
        isinstance(t, dict) and t.get("to") == MeetingStatus.ACTIVE.value
        for t in transitions
    )
    if not reached_active:
        # 432-case: bot was created + stopped before reaching admission.
        return (
            MeetingStatus.FAILED,
            MeetingCompletionReason.STOPPED_BEFORE_ADMISSION,
        )

    # Compute duration. start_time is set when the meeting reaches active;
    # end_time may not be set yet at exit-callback time, so fall back to now.
    duration_s = 0.0
    if meeting.start_time:
        end_t = meeting.end_time or datetime.utcnow()
        duration_s = (end_t - meeting.start_time).total_seconds()

    # Was transcription requested? Default True (legacy meetings without
    # the explicit flag predate the field; treat them as transcribe-enabled).
    transcribe_enabled = bool(data.get("transcribe_enabled", True))

    # Short meeting OR transcribe disabled — legitimate, route as completed.
    if duration_s < 30 or not transcribe_enabled:
        return (MeetingStatus.COMPLETED, requested_reason)

    # Long meeting + transcribe enabled — check actual transcription rows.
    try:
        count_stmt = select(func.count()).select_from(Transcription).where(
            Transcription.meeting_id == meeting.id
        )
        segment_count = (await db.execute(count_stmt)).scalar() or 0
    except Exception as e:
        # Don't block exit-callback on a transient DB error; log + treat as
        # legitimate completed (conservative — better to under-route to
        # FAILED than to spuriously fail genuinely-successful meetings).
        logger.warning(f"Pack J: segment count query failed for meeting {meeting.id}: {e}")
        return (MeetingStatus.COMPLETED, requested_reason)

    if segment_count == 0:
        # v0.10.5 (post-prod-telemetry 2026-04-30) — DELIVERY-AWARE classification.
        #
        # Pre-fix: any meeting with transcribe_enabled and 0 transcripts was
        # routed to FAILED/STOPPED_WITH_NO_AUDIO. This conflated two distinct
        # outcomes:
        #   (a) Bot couldn't capture audio at all (real failure)
        #   (b) Bot DID capture audio (e.g. recording_enabled=true produced a
        #       multi-MB WAV/webm file delivered to MinIO) but no SPEECH was
        #       present in the captured audio — silent or quiet meeting.
        #
        # (b) is a successful capture from the customer's perspective: the
        # recording is on disk, downloadable, replayable. Marking the whole
        # meeting as `failed` lies — and the dashboard hides the recording
        # because of the failed status, double-burying the delivered artifact.
        #
        # Honest fix: if a recording WAS delivered (chunk_count > 0 or any
        # media_files entry exists with non-zero file_size_bytes), the meeting
        # COMPLETED — we delivered what was captured. Only fall through to
        # STOPPED_WITH_NO_AUDIO for the case where neither transcripts nor a
        # recording entry exists.
        recordings = data.get("recordings") or []
        recording_delivered = False
        for rec in recordings:
            if not isinstance(rec, dict):
                continue
            for mf in (rec.get("media_files") or []):
                if not isinstance(mf, dict):
                    continue
                if int(mf.get("file_size_bytes") or 0) > 0:
                    recording_delivered = True
                    break
            if recording_delivered:
                break

        if recording_delivered:
            # Recording delivered, no transcripts → silent/quiet meeting,
            # not a failure. Map to the closest non-failure reason.
            return (MeetingStatus.COMPLETED, requested_reason)

        # 125-case: bot was active for 30s+ with transcribe enabled, no
        # transcripts AND no recording delivered. Real failure — silent class.
        return (MeetingStatus.FAILED, MeetingCompletionReason.STOPPED_WITH_NO_AUDIO)

    return (MeetingStatus.COMPLETED, requested_reason)

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
        old_status = meeting.status

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
        elif meeting.status == MeetingStatus.STOPPING.value:
            # Meeting was in stopping state — user requested stop.
            # v0.10.5 Pack J — apply data-driven classification rule (#255).
            # OLD shape: any stopped exit → COMPLETED unconditionally. Result:
            # 47% misclassification rate (557/1183 in 30d production data).
            # NEW shape: classify via _classify_stopped_exit() which inspects
            # reached-active + duration + transcribe-enabled + segment count
            # to distinguish legitimate stops from STOPPED_BEFORE_ADMISSION
            # and STOPPED_WITH_NO_AUDIO.
            provided_reason = payload.completion_reason or MeetingCompletionReason.STOPPED
            target_status, classified_reason = await _classify_stopped_exit(
                meeting, db, provided_reason
            )
            logger.info(
                f"Exit callback: session {session_uid} exit_code={exit_code} during stopping "
                f"— Pack J classified as {target_status.value} reason={classified_reason.value} "
                f"(was: completed reason={provided_reason.value})"
            )
            meta = {"exit_code": exit_code, "original_reason": payload.reason, "pack_j_classification": classified_reason.value}
            success = await update_meeting_status(
                meeting, target_status, db,
                completion_reason=classified_reason,
                transition_reason=payload.reason,
                transition_metadata=meta,
            )
            new_status = target_status.value if success else None
        else:
            # v0.10.5 FM-001/FM-002/FM-003 (registered 2026-04-28): every
            # bot exit from a non-stopping state routes through the central
            # classifier. Pre-fix shape had a narrow allowlist gate
            # (`payload.completion_reason or payload.reason in {5 strings}`)
            # for ACTIVE exits, with a `failed + completion_reason=NULL`
            # else-branch for everything else. PLATFORM 7d aggregate showed
            # 182 NULL-bucket rows (FM-002) plus 127 mislabeled failure_stage
            # rows (FM-003) — and meeting 11161 in particular: a 30-min
            # gmeet meeting with 197 segments delivered, painted FAILED
            # because `payload.reason="post_join_setup_error"` (gmeet
            # end-of-meeting page navigation crashing the bot's page.evaluate)
            # was not in the allowlist (FM-001).
            #
            # Two structural changes (per ARCH review 2026-04-28):
            #   (1) Drop the allowlist gate; route ALL non-stopping exits
            #       through _classify_stopped_exit. The classifier already
            #       distinguishes reached_active+segments→COMPLETED from
            #       not-reached-active→STOPPED_BEFORE_ADMISSION. The else
            #       branch's silent NULL bucket becomes structurally
            #       impossible.
            #   (2) failure_stage derives from meeting.status at write time,
            #       not from the bot's payload. The bot reports its own
            #       internal stage tracker which is stale on the catch path
            #       (FM-003).
            #
            # Plus: unknown payload.reason values are logged WARN + stuffed
            # into transition_metadata.unknown_bot_reason so DATA can grep
            # for new vocabulary before it becomes the next FM-002.
            _BOT_REASON_TO_COMPLETION = {
                "self_initiated_leave": MeetingCompletionReason.STOPPED,
                "evicted": MeetingCompletionReason.EVICTED,
                "removed_by_host": MeetingCompletionReason.EVICTED,
                "removed_by_admin": MeetingCompletionReason.EVICTED,
                "left_alone": MeetingCompletionReason.LEFT_ALONE,
                "left_alone_timeout": MeetingCompletionReason.LEFT_ALONE,
                "startup_alone_timeout": MeetingCompletionReason.LEFT_ALONE,
                "meeting_ended_by_host": MeetingCompletionReason.STOPPED,
                "normal_completion": MeetingCompletionReason.STOPPED,
                "post_join_setup_error": MeetingCompletionReason.STOPPED,  # FM-001 — gmeet end-of-meeting nav
                "admission_timeout": MeetingCompletionReason.AWAITING_ADMISSION_TIMEOUT,
                "admission_rejected_by_admin": MeetingCompletionReason.AWAITING_ADMISSION_REJECTED,
                "admission_false_positive": MeetingCompletionReason.STOPPED,
                "stop_requested_pre_admission": MeetingCompletionReason.STOPPED_BEFORE_ADMISSION,
                "missing_meeting_url": MeetingCompletionReason.VALIDATION_ERROR,
                "join_meeting_error": MeetingCompletionReason.STOPPED_BEFORE_ADMISSION,
            }
            unknown_reason = bool(
                payload.reason and payload.reason not in _BOT_REASON_TO_COMPLETION
            )
            if unknown_reason:
                logger.warning(
                    "Unknown bot exit reason %r — defaulting to STOPPED. "
                    "Catalog this in _BOT_REASON_TO_COMPLETION.",
                    payload.reason,
                )
            derived_completion_reason = payload.completion_reason or _BOT_REASON_TO_COMPLETION.get(
                payload.reason or "", MeetingCompletionReason.STOPPED
            )
            target_status, classified_reason = await _classify_stopped_exit(
                meeting, db, derived_completion_reason
            )
            logger.info(
                f"Exit callback: session {session_uid} exit_code={exit_code} "
                f"from {meeting.status} reason={payload.reason} "
                f"completion_reason={derived_completion_reason.value} "
                f"— Pack J classified as {target_status.value}"
            )
            meta = {
                "exit_code": exit_code,
                "original_reason": payload.reason,
                "pack_j_classification": classified_reason.value,
            }
            if payload.platform_specific_error:
                meta["platform_specific_error"] = payload.platform_specific_error
            if unknown_reason:
                meta["unknown_bot_reason"] = payload.reason
            # FM-003: derive failure_stage from current meeting.status, not
            # from the bot's payload. Only used if target_status == FAILED.
            update_kwargs = dict(
                completion_reason=classified_reason,
                transition_reason=payload.reason,
                transition_metadata=meta,
            )
            if target_status == MeetingStatus.FAILED:
                update_kwargs["failure_stage"] = _failure_stage_from_status(meeting.status)
                error_msg = f"Bot exited with code {exit_code}"
                if payload.reason:
                    error_msg += f"; reason: {payload.reason}"
                update_kwargs["error_details"] = error_msg
            success = await update_meeting_status(
                meeting, target_status, db, **update_kwargs
            )
            new_status = target_status.value if success else None

            if success and target_status == MeetingStatus.FAILED and (
                payload.error_details or payload.platform_specific_error
            ):
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

        # Persist chat messages from Redis list → meeting.data.chat_messages JSONB.
        #
        # Runs unconditionally — independent of `success`. Race we're guarding
        # against: when the user sends DELETE, meetings.py's [Delayed Stop]
        # timer can mark the meeting `completed` BEFORE the bot's exit
        # callback fires. The exit callback's status update then tries
        # `completed → completed` and returns False ("Invalid status
        # transition"). If we returned early on `not success`, chat messages
        # would be stuck in Redis forever — which was happening: every
        # DELETE-terminated meeting had zero persisted chat (observed
        # 2026-04-26 across all meetings). The chat-persistence block
        # doesn't depend on status state, so it's safe to run regardless.
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
            await schedule_status_webhook_task(
                meeting=meeting, background_tasks=background_tasks,
                old_status=old_status, new_status=new_status,
                reason=payload.reason, transition_source="bot_callback",
            )

        background_tasks.add_task(run_all_tasks, meeting.id)

        return {"status": "callback processed", "meeting_id": meeting.id, "final_status": meeting.status}

    except Exception as e:
        logger.error(f"Exit callback error: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail="Internal error processing exit callback")


@router.post("/bots/internal/callback/started", status_code=200, include_in_schema=False)
async def bot_startup_callback(
    payload: BotStartupCallbackPayload,
    background_tasks: BackgroundTasks,
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
        # v0.10.5 Pack X finding (2026-04-27): the state machine
        # (schemas.get_valid_status_transitions) FORBIDS direct
        # REQUESTED→ACTIVE — only REQUESTED→JOINING and JOINING→ACTIVE
        # are legal. Pre-fix, this branch silently failed when status
        # was REQUESTED: update_meeting_status returned False, the
        # callback returned 200 with `meeting_status: "requested"` —
        # misleading API. Real bots happen to fire `joining` first so
        # production didn't trip it; synthetic scenarios driving
        # `started` directly did.
        #
        # Fix: drive through legal intermediate transitions. If
        # currently REQUESTED, transition to JOINING first, then to
        # ACTIVE. Each step uses the same legal-transition validator.
        if meeting.status == MeetingStatus.REQUESTED.value:
            await update_meeting_status(meeting, MeetingStatus.JOINING, db)
            await db.refresh(meeting)
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
        await schedule_status_webhook_task(
            meeting=meeting, background_tasks=background_tasks,
            old_status=old_status, new_status=MeetingStatus.ACTIVE.value,
            transition_source="bot_callback",
        )

    return {"status": "startup processed", "meeting_id": meeting.id, "meeting_status": meeting.status}


@router.post("/bots/internal/callback/joining", status_code=200, include_in_schema=False)
async def bot_joining_callback(
    payload: BotStartupCallbackPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    redis_client = get_redis()
    _, meeting = await _find_meeting_by_session(payload.connection_id, db)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting session not found")

    if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
        return {"status": "ignored", "detail": "stop requested"}

    old_status = meeting.status
    success = await update_meeting_status(meeting, MeetingStatus.JOINING, db)
    if success:
        await publish_meeting_status_change(meeting.id, MeetingStatus.JOINING.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)
        await schedule_status_webhook_task(
            meeting=meeting, background_tasks=background_tasks,
            old_status=old_status, new_status=MeetingStatus.JOINING.value,
            transition_source="bot_callback",
        )

    return {"status": "joining processed", "meeting_id": meeting.id, "meeting_status": meeting.status}


@router.post("/bots/internal/callback/awaiting_admission", status_code=200, include_in_schema=False)
async def bot_awaiting_admission_callback(
    payload: BotStartupCallbackPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    redis_client = get_redis()
    _, meeting = await _find_meeting_by_session(payload.connection_id, db)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting session not found")

    if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
        return {"status": "ignored", "detail": "stop requested"}

    old_status = meeting.status
    success = await update_meeting_status(meeting, MeetingStatus.AWAITING_ADMISSION, db)
    if success:
        await publish_meeting_status_change(meeting.id, MeetingStatus.AWAITING_ADMISSION.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)
        await schedule_status_webhook_task(
            meeting=meeting, background_tasks=background_tasks,
            old_status=old_status, new_status=MeetingStatus.AWAITING_ADMISSION.value,
            transition_source="bot_callback",
        )

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

    # Stop was requested: skip the actual status transition (we're winding down),
    # but still fire the status webhook so users subscribed to meeting.status_change
    # / meeting.started / bot.failed don't miss events that legitimately happened
    # on the bot side (see releases/260418-webhooks/triage-log.md candidate b).
    if (meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested")
            and new_status not in [MeetingStatus.COMPLETED, MeetingStatus.FAILED]):
        await schedule_status_webhook_task(
            meeting=meeting,
            background_tasks=background_tasks,
            old_status=meeting.status,
            new_status=new_status.value,
            reason=reason,
            transition_source="bot_callback_post_stop",
        )
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

        # v0.10.5 Pack J — apply data-driven classification rule (#255 silent class).
        #
        # 2026-04-27 live-validation finding (meeting 26): when the bot self-
        # reports new_status=COMPLETED via status_change while in STOPPING
        # state, this handler previously set status='completed' directly with
        # the bot-reported reason — bypassing Pack J's classifier entirely.
        # Result: a meeting that was active 6+ min with transcribe_enabled
        # and 0 transcription segments was marked `completed` instead of
        # `failed/stopped_with_no_audio`. Same silent class as the
        # exit_callback STOPPING branch (callbacks.py:236).
        #
        # Fix: when transitioning STOPPING → COMPLETED (or active → COMPLETED
        # with a stoppable bot-reported reason), apply Pack J's classifier so
        # the same data-driven rules govern both callback paths. The
        # exit_callback STOPPING branch and the status_change STOPPING→
        # COMPLETED branch now produce identical classifications for
        # identical inputs.
        target_status = MeetingStatus.COMPLETED
        classified_reason = effective_reason
        if (
            meeting.status == MeetingStatus.STOPPING.value
            and effective_reason is not None
        ):
            target_status, classified_reason = await _classify_stopped_exit(
                meeting, db, effective_reason
            )
            logger.info(
                f"Pack J (status_change path): meeting {meeting.id} "
                f"STOPPING→{target_status.value} reason={classified_reason.value} "
                f"(bot-reported: {effective_reason.value})"
            )

        success = await update_meeting_status(meeting, target_status, db, completion_reason=classified_reason)
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
        # v0.10.5 Pack X finding (lite m28, 2026-04-27): bot's
        # status_change new_status=failed didn't pass completion_reason
        # through to update_meeting_status — `data.completion_reason`
        # stayed empty even when the bot supplied one. Now it
        # propagates: dashboards/observers grouping by completion_reason
        # see the bot-reported value (or null when bot didn't supply
        # one — caller responsibility to provide a meaningful reason).
        success = await update_meeting_status(
            meeting, MeetingStatus.FAILED, db,
            completion_reason=payload.completion_reason,
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
        publish_extra: Optional[Dict[str, Any]] = None
        if payload.completion_reason or payload.failure_stage or reason:
            publish_extra = {}
            if payload.completion_reason:
                publish_extra["completion_reason"] = payload.completion_reason.value
            if payload.failure_stage:
                publish_extra["failure_stage"] = payload.failure_stage.value
            if reason:
                publish_extra["reason"] = reason
        if new_status == MeetingStatus.NEEDS_HUMAN_HELP and meeting.data and "escalation" in meeting.data:
            publish_extra = publish_extra or {}
            publish_extra.update({
                "escalation_reason": meeting.data["escalation"].get("reason"),
                "vnc_url": meeting.data["escalation"].get("vnc_url"),
                "escalated_at": meeting.data["escalation"].get("escalated_at"),
            })
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


# ---------------------------------------------------------------------------
# v0.10.5 Pack X — Synthetic test harness endpoint
# ---------------------------------------------------------------------------
#
# `POST /bots/internal/test/session-bootstrap` — creates a MeetingSession
# row for an existing meeting WITHOUT requiring the bot to spawn. Lets
# the synthetic test rig (`tests3/synthetic/`) drive the full lifecycle
# via pure HTTP callbacks without external platform dependencies (Zoom
# DOM, Meet WebRTC, Teams). Catches OSS-side regressions that only
# surface in callback orderings (e.g. Pack J coverage gap caught
# 2026-04-27 by real Zoom test — would have caught deterministically
# with this rig).
#
# Path is `/bots/internal/test/...` (not `/internal/test/...`) because
# the api-gateway proxies the `/bots/internal/*` namespace to
# meeting-api but does NOT proxy a top-level `/internal/*` path.
# Mirrors the existing `/bots/internal/callback/*` pattern.
#
# Gated by VEXA_ENV != "production" — endpoint returns 404 in production.
# Synthetic-test traffic must never reach prod meeting-api instances.

class SyntheticSessionBootstrap(BaseModel):
    meeting_id: int
    session_uid: Optional[str] = None  # auto-generated if not provided


@router.post("/bots/internal/test/session-bootstrap", status_code=201, include_in_schema=False)
async def synthetic_session_bootstrap(
    payload: SyntheticSessionBootstrap,
    db: AsyncSession = Depends(get_db),
):
    """Create a MeetingSession row directly — synthetic test harness only.

    Allows the synthetic test rig to drive callback paths without spawning
    a real bot. The bot's natural session-creation path (collector
    process_session_start_event) is bypassed.

    Returns the session_uid so the test driver can pass it as
    connection_id in subsequent callback POSTs.
    """
    import os
    if os.getenv("VEXA_ENV") == "production":
        raise HTTPException(status_code=404, detail="Not Found")

    meeting = await db.get(Meeting, payload.meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting {payload.meeting_id} not found")

    import uuid
    session_uid = payload.session_uid or str(uuid.uuid4())

    # Idempotent — if session_uid already exists, return it as-is.
    existing_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
    existing = (await db.execute(existing_stmt)).scalars().first()
    if existing:
        return {"session_uid": session_uid, "meeting_id": payload.meeting_id, "created": False}

    new_session = MeetingSession(
        meeting_id=payload.meeting_id,
        session_uid=session_uid,
        session_start_time=datetime.utcnow(),
    )
    db.add(new_session)
    await db.commit()

    logger.info(
        f"[Pack X synthetic] Bootstrapped MeetingSession session_uid={session_uid} "
        f"meeting_id={payload.meeting_id}"
    )
    return {"session_uid": session_uid, "meeting_id": payload.meeting_id, "created": True}
