"""Post-meeting tasks — aggregation, webhooks, hooks.

Post-meeting aggregation, webhooks, and hooks.
Same logic, same webhook payloads.
"""

import logging
import os

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Meeting
from .database import async_session_local
from .webhook_delivery import deliver, build_envelope

from .config import TRANSCRIPTION_COLLECTOR_URL, POST_MEETING_HOOKS
from .webhooks import send_completion_webhook

logger = logging.getLogger("meeting_api.post_meeting")


async def aggregate_transcription(meeting: Meeting, db: AsyncSession):
    """Fetch transcription segments and aggregate participants + languages into meeting.data."""
    meeting_id = meeting.id
    try:
        collector_url = f"{TRANSCRIPTION_COLLECTOR_URL}/internal/transcripts/{meeting_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(collector_url, timeout=30.0)

        if response.status_code != 200:
            logger.error(f"Collector returned {response.status_code} for meeting {meeting_id}")
            return

        segments = response.json()
        if not segments:
            return

        unique_speakers = set()
        unique_languages = set()
        for seg in segments:
            speaker = seg.get("speaker")
            language = seg.get("language")
            if speaker and speaker.strip():
                unique_speakers.add(speaker.strip())
            if language and language.strip():
                unique_languages.add(language.strip())

        existing_data = meeting.data or {}
        changed = False
        if "participants" not in existing_data and unique_speakers:
            existing_data["participants"] = sorted(unique_speakers)
            changed = True
        if "languages" not in existing_data and unique_languages:
            existing_data["languages"] = sorted(unique_languages)
            changed = True

        if changed:
            meeting.data = existing_data
            logger.info(f"Aggregated transcription data for meeting {meeting_id}")

    except httpx.RequestError as exc:
        logger.error(f"Transcription aggregation request error for meeting {meeting_id}: {exc}")
    except Exception as e:
        logger.error(f"Transcription aggregation failed for meeting {meeting_id}: {e}", exc_info=True)


async def fire_post_meeting_hooks(meeting: Meeting, db: AsyncSession):
    """Fire POST_MEETING_HOOKS to configured internal services (billing, analytics, etc.)."""
    if not POST_MEETING_HOOKS:
        return

    if not meeting.start_time or not meeting.end_time:
        return

    # Resolve real email from users table — billing hooks need it to meter usage
    try:
        from admin_models.models import User
        user = (await db.execute(select(User).where(User.id == meeting.user_id))).scalars().first()
        if not user or not user.email:
            logger.error(f"Cannot resolve email for user {meeting.user_id} — skipping billing hook")
            return
        user_email = user.email
    except Exception as e:
        logger.error(f"DB error resolving email for user {meeting.user_id} — skipping billing hook: {e}")
        return

    duration_seconds = (meeting.end_time - meeting.start_time).total_seconds()
    meeting_data = meeting.data or {}

    payload = build_envelope("meeting.completed", {
        "meeting": {
            "id": meeting.id,
            "user_id": meeting.user_id,
            "user_email": user_email,
            "platform": meeting.platform,
            "status": meeting.status,
            "duration_seconds": duration_seconds,
            "start_time": meeting.start_time.isoformat(),
            "end_time": meeting.end_time.isoformat(),
            "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
            "transcription_enabled": meeting_data.get("transcribe_enabled", False),
        },
    })

    for hook_url in POST_MEETING_HOOKS:
        await deliver(
            url=hook_url,
            payload=payload,
            timeout=10.0,
            label=f"post-meeting-hook meeting={meeting.id}",
        )


async def run_all_tasks(meeting_id: int):
    """Run all post-meeting tasks for a given meeting_id.

    Creates its own DB session.
    """
    logger.info(f"Starting post-meeting tasks for meeting {meeting_id}")

    async with async_session_local() as db:
        try:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.error(f"Meeting {meeting_id} not found for post-meeting tasks")
                return

            # Task 1: Aggregate transcription data
            await aggregate_transcription(meeting, db)

            # Task 2: Send completion webhook to user
            await send_completion_webhook(meeting, db)

            # Task 3: Fire internal post-meeting hooks
            await fire_post_meeting_hooks(meeting, db)

            await db.commit()
            logger.info(f"Post-meeting tasks completed for meeting {meeting_id}")

        except Exception as e:
            logger.error(f"Error in post-meeting tasks for meeting {meeting_id}: {e}", exc_info=True)
            await db.rollback()


async def run_status_webhook_task(meeting_id: int, status_change_info: dict = None):
    """Run status webhook with proper DB session management."""
    from .webhooks import send_status_webhook

    async with async_session_local() as db:
        try:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.error(f"Meeting {meeting_id} not found for status webhook")
                return
            await send_status_webhook(meeting, db, status_change_info)
        except Exception as e:
            logger.error(f"Error in status webhook for meeting {meeting_id}: {e}", exc_info=True)
