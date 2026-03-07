import logging
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting, User
from shared_models.webhook_url import validate_webhook_url
from shared_models.webhook_delivery import deliver
from typing import Optional

logger = logging.getLogger(__name__)


async def run(meeting: Meeting, db: AsyncSession):
    """
    Sends a webhook with the completed meeting details to a user-configured URL.
    Uses exponential backoff retry and HMAC signing when webhook_secret is set.
    """
    logger.info(f"Executing send_webhook task for meeting {meeting.id}")

    try:
        user = meeting.user
        if not user:
            logger.error(f"Could not find user on meeting object {meeting.id}")
            return

        webhook_url = user.data.get('webhook_url') if user.data and isinstance(user.data, dict) else None
        if not webhook_url:
            logger.info(f"No webhook URL configured for user {user.email} (meeting {meeting.id})")
            return

        # SSRF defense: validate URL before sending
        try:
            validate_webhook_url(webhook_url)
        except ValueError as e:
            logger.warning(f"Webhook URL validation failed for meeting {meeting.id}: {e}. Skipping.")
            return

        webhook_secret = None
        if user.data and isinstance(user.data, dict):
            webhook_secret = user.data.get('webhook_secret')

        payload = {
            'id': meeting.id,
            'user_id': meeting.user_id,
            'platform': meeting.platform,
            'native_meeting_id': meeting.native_meeting_id,
            'constructed_meeting_url': meeting.constructed_meeting_url,
            'status': meeting.status,
            'bot_container_id': meeting.bot_container_id,
            'start_time': meeting.start_time.isoformat() if meeting.start_time else None,
            'end_time': meeting.end_time.isoformat() if meeting.end_time else None,
            'data': meeting.data or {},
            'created_at': meeting.created_at.isoformat() if meeting.created_at else None,
            'updated_at': meeting.updated_at.isoformat() if meeting.updated_at else None,
        }

        await deliver(
            url=webhook_url,
            payload=payload,
            webhook_secret=webhook_secret,
            timeout=30.0,
            label=f"client-webhook meeting={meeting.id} user={user.email}",
        )

    except Exception as e:
        logger.error(f"Unexpected error sending webhook for meeting {meeting.id}: {e}", exc_info=True)
