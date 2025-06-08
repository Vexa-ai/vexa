import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting, User
from shared_models.schemas import MeetingResponse 

logger = logging.getLogger(__name__)

async def run(meeting: Meeting, db: AsyncSession):
    """
    Sends a webhook with the completed meeting details to a user-configured URL.
    """
    logger.info(f"Executing send_webhook task for meeting {meeting.id}")

    try:
        user = await db.get(User, meeting.user_id)
        if not user:
            logger.error(f"Could not find user with ID {meeting.user_id} for meeting {meeting.id}")
            return

        if not hasattr(user, 'data') or not isinstance(user.data, dict):
            logger.info(f"User {user.id} does not have a data field or it's not a dictionary. Skipping webhook.")
            return

        webhook_url = user.data.get("webhook_url")
        if not webhook_url:
            logger.info(f"Webhook URL not configured for user {user.id}. Skipping webhook for meeting {meeting.id}")
            return

        payload = MeetingResponse.from_orm(meeting).dict()
        logger.debug(f"Webhook payload for meeting {meeting.id}: {payload}")

        async with httpx.AsyncClient() as client:
            logger.info(f"Sending webhook for meeting {meeting.id} to {webhook_url}")
            response = await client.post(webhook_url, json=payload, timeout=15.0)
            response.raise_for_status() 
            logger.info(f"Successfully sent webhook for meeting {meeting.id}. Status code: {response.status_code}")

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error sending webhook for meeting {meeting.id} to {e.request.url}: {e.response.status_code} - {e.response.text}", exc_info=True)
    except httpx.RequestError as e:
        logger.error(f"Request error sending webhook for meeting {meeting.id} to {e.request.url}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred in send_webhook task for meeting {meeting.id}: {e}", exc_info=True) 