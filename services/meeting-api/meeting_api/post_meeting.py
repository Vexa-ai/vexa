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


# v0.10.5 Pack H — aggregation_failure_class taxonomy.
#
# JSONB discriminator (NO PG migration; meetings.data is JSONB and
# meetings.status is String(50) — both already accept new keys/values
# without schema changes). Three values:
#   - "transient_infra"      — tx-gateway 5xx; retry-eligible via Pack H.4 sweep
#   - "permanent_infra"      — 7-day retry budget exhausted; terminal; alerts critical
#   - "user_meaningful"      — bot crash, validation error, etc; terminal as `status=failed`
#
# Single-write-path discipline: every write goes through
# set_aggregation_failure_class() helper. Pack H's registry check
# AGGREGATION_FAILURE_CLASS_VIA_TYPED_HELPER asserts no other call site
# touches data['aggregation_failure_class'] directly.
class AggregationFailureClass:
    TRANSIENT_INFRA = "transient_infra"
    PERMANENT_INFRA = "permanent_infra"
    USER_MEANINGFUL = "user_meaningful"


def set_aggregation_failure_class(meeting: Meeting, cls: str) -> None:
    """Canonical single-write-path for data.aggregation_failure_class.

    Updates meeting.data dict in place + flags it modified for SQLAlchemy.
    Caller commits.
    """
    valid = {
        AggregationFailureClass.TRANSIENT_INFRA,
        AggregationFailureClass.PERMANENT_INFRA,
        AggregationFailureClass.USER_MEANINGFUL,
    }
    if cls not in valid:
        raise ValueError(f"Invalid aggregation_failure_class: {cls!r}; must be one of {valid}")
    data = dict(meeting.data or {})
    data["aggregation_failure_class"] = cls
    from datetime import datetime
    data["aggregation_last_retry_at"] = datetime.utcnow().isoformat()
    data["aggregation_retry_count"] = (data.get("aggregation_retry_count") or 0) + 1
    meeting.data = data
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(meeting, "data")


def clear_aggregation_failure_class(meeting: Meeting) -> None:
    """Clear failure_class on success — also via the canonical write path."""
    if not meeting.data:
        return
    data = dict(meeting.data)
    if "aggregation_failure_class" in data:
        del data["aggregation_failure_class"]
        meeting.data = data
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(meeting, "data")


async def aggregate_transcription(meeting: Meeting, db: AsyncSession):
    """Fetch transcription segments and aggregate into meeting.data.

    v0.10.5 Pack H — distinguishes transient infra failures (5xx, network
    error) from permanent failures (4xx auth/validation) from success.
    Pre-Pack-H: ALL non-200 returned silently; meeting marked `failed`
    via callers' default-to-completed/failed paths regardless of cause.
    Real-world incident 2026-04-23: tx-gateway pod restart during
    aggregate → 23 consecutive meetings marked `failed` from a transient
    flap.

    Now:
      - 5xx OR network error → set aggregation_failure_class='transient_infra'
        — retry-eligible via Pack H.4 sweep in sweeps.py (24 retries × exp
        backoff, 7-day budget). Caller stays in non-terminal state until
        either retry succeeds or budget exhausts.
      - 4xx → set aggregation_failure_class='permanent_infra' (e.g. auth
        misconfig); terminal; alerts critical. Operator action required.
      - 200 + segments → clear failure_class; aggregate normally.

    Returns True on terminal success, False on transient (caller can choose
    to surface aggregation_failed event vs leave for sweep retry).
    """
    meeting_id = meeting.id
    try:
        collector_url = f"{TRANSCRIPTION_COLLECTOR_URL}/internal/transcripts/{meeting_id}"
        internal_secret = os.getenv("INTERNAL_API_SECRET", "")
        headers = {"X-Internal-Secret": internal_secret} if internal_secret else {}
        async with httpx.AsyncClient() as client:
            response = await client.get(collector_url, timeout=30.0, headers=headers)

        # v0.10.5 Pack H — distinguish 5xx (transient) from 4xx (permanent).
        if 500 <= response.status_code < 600:
            logger.warning(
                f"Pack H: tx-gateway returned {response.status_code} for meeting {meeting_id} "
                f"— transient infra, retrying via sweep"
            )
            set_aggregation_failure_class(meeting, AggregationFailureClass.TRANSIENT_INFRA)
            await db.commit()
            return False
        if response.status_code != 200:
            # 4xx — permanent. Auth misconfig, malformed request, etc.
            logger.error(
                f"Pack H: tx-gateway returned {response.status_code} for meeting {meeting_id} "
                f"— permanent infra failure (operator action required)"
            )
            set_aggregation_failure_class(meeting, AggregationFailureClass.PERMANENT_INFRA)
            await db.commit()
            return False

        segments = response.json()
        if not segments:
            # Empty result is legitimate (zero-segment meeting); clear any
            # prior failure_class to indicate aggregation completed cleanly.
            clear_aggregation_failure_class(meeting)
            await db.commit()
            return True

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
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(meeting, "data")
            logger.info(f"Aggregated transcription data for meeting {meeting_id}")

        # Success — clear any prior transient failure_class.
        clear_aggregation_failure_class(meeting)
        await db.commit()
        return True

    except httpx.RequestError as exc:
        # Network error (DNS, TLS, timeout) — same class as 5xx; retry-eligible.
        logger.warning(
            f"Pack H: tx-gateway request error for meeting {meeting_id}: "
            f"{type(exc).__name__}: {exc!r} — transient infra, retrying via sweep"
        )
        set_aggregation_failure_class(meeting, AggregationFailureClass.TRANSIENT_INFRA)
        try:
            await db.commit()
        except Exception:
            pass
        return False
    except Exception as e:
        # Unknown error — log loudly + don't mark transient (don't retry into a code bug).
        logger.error(
            f"Pack H: aggregation failed for meeting {meeting_id}: "
            f"{type(e).__name__}: {e!r}",
            exc_info=True,
        )
        return False


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

    Uses short-lived DB sessions to avoid holding connections during HTTP calls.
    """
    logger.info(f"Starting post-meeting tasks for meeting {meeting_id}")

    # Task 1: Aggregate transcription data (makes HTTP call to collector)
    try:
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.error(f"Meeting {meeting_id} not found for post-meeting tasks")
                return
            await aggregate_transcription(meeting, db)
            await db.commit()
    except Exception as e:
        logger.error(f"Transcription aggregation failed for meeting {meeting_id}: {e}", exc_info=True)

    # Task 2: Send completion webhook to user (makes HTTP call to user's endpoint)
    try:
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                await send_completion_webhook(meeting, db)
                await db.commit()
    except Exception as e:
        logger.error(f"Completion webhook failed for meeting {meeting_id}: {e}", exc_info=True)

    # Task 3: Fire internal post-meeting hooks (makes HTTP calls to hook URLs)
    try:
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting:
                await fire_post_meeting_hooks(meeting, db)
                await db.commit()
    except Exception as e:
        logger.error(f"Post-meeting hooks failed for meeting {meeting_id}: {e}", exc_info=True)

    logger.info(f"Post-meeting tasks completed for meeting {meeting_id}")


async def run_status_webhook_task(meeting_id: int, status_change_info: dict = None):
    """Run status webhook — short-lived DB session, HTTP call outside session."""
    from .webhooks import send_status_webhook

    try:
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.error(f"Meeting {meeting_id} not found for status webhook")
                return
            await send_status_webhook(meeting, db, status_change_info)
            await db.commit()
    except Exception as e:
        logger.error(f"Error in status webhook for meeting {meeting_id}: {e}", exc_info=True)
