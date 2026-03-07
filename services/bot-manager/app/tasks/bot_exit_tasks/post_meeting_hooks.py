import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting
from shared_models.webhook_delivery import deliver

logger = logging.getLogger(__name__)

# Comma-separated list of internal URLs to notify on meeting completion.
# Each URL receives a POST with meeting data (duration, platform, user, etc.)
# Examples:
#   POST_MEETING_HOOKS=http://billing:19000/v1/hooks/meeting-completed
#   POST_MEETING_HOOKS=http://billing:19000/v1/hooks/meeting-completed,http://analytics:8080/events
#
# Leave empty for self-hosted deployments without external integrations.
POST_MEETING_HOOKS = [
    url.strip()
    for url in os.getenv("POST_MEETING_HOOKS", "").split(",")
    if url.strip()
]


async def run(meeting: Meeting, db: AsyncSession):
    """
    Fires post-meeting hooks to configured internal services.

    This is the generic extension point for any deployment-specific
    post-meeting processing: billing, analytics, CRM sync, etc.
    The bot-manager stays agnostic — it just delivers the meeting data.
    """
    if not POST_MEETING_HOOKS:
        return

    if not meeting.start_time or not meeting.end_time:
        logger.debug(f"Meeting {meeting.id} missing start/end time, skipping hooks")
        return

    user = meeting.user
    if not user:
        logger.error(f"No user on meeting {meeting.id}, skipping hooks")
        return

    duration_seconds = (meeting.end_time - meeting.start_time).total_seconds()

    meeting_data = meeting.data or {}
    payload = {
        "event": "meeting.completed",
        "meeting": {
            "id": meeting.id,
            "user_id": meeting.user_id,
            "user_email": user.email,
            "platform": meeting.platform,
            "status": meeting.status,
            "duration_seconds": duration_seconds,
            "start_time": meeting.start_time.isoformat(),
            "end_time": meeting.end_time.isoformat(),
            "created_at": meeting.created_at.isoformat() if meeting.created_at else None,
            "transcription_enabled": meeting_data.get("transcribe_enabled", False),
            "data": meeting_data,
        },
    }

    for hook_url in POST_MEETING_HOOKS:
        await deliver(
            url=hook_url,
            payload=payload,
            timeout=10.0,
            label=f"post-meeting-hook meeting={meeting.id}",
        )
