import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting, User

logger = logging.getLogger(__name__)

async def run(meeting: Meeting, db: AsyncSession):
    """
    Sends a webhook with the completed meeting details to a user-configured URL.
    """
    logger.info(f"Executing send_webhook task for meeting {meeting.id}")

    try:
        # The user should be loaded on the meeting object already by the task runner
        user = meeting.user
        if not user:
            logger.error(f"Could not find user on meeting object {meeting.id}")
            return

        # Check if user has a webhook URL configured
        webhook_url = user.data.get('webhook_url') if user.data and isinstance(user.data, dict) else None

        if not webhook_url:
            logger.info(f"No webhook URL configured for user {user.email} (meeting {meeting.id})")
            return

        # Prepare the webhook payload
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

        # Send the webhook
        async with httpx.AsyncClient() as client:
            logger.info(f"Sending webhook to {webhook_url} for meeting {meeting.id}")
            response = await client.post(
                webhook_url,
                json=payload,
                timeout=30.0,
                headers={'Content-Type': 'application/json'}
            )
            
            if response.status_code >= 200 and response.status_code < 300:
                logger.info(f"Successfully sent webhook for meeting {meeting.id} to {webhook_url}")
            else:
                logger.warning(f"Webhook for meeting {meeting.id} returned status {response.status_code}: {response.text}")

    except httpx.RequestError as e:
        logger.error(f"Failed to send webhook for meeting {meeting.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending webhook for meeting {meeting.id}: {e}", exc_info=True) 