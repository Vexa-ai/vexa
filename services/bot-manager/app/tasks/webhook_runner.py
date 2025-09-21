import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from shared_models.models import Meeting
from shared_models.database import async_session_local
from .send_status_webhook import run as send_status_webhook

logger = logging.getLogger(__name__)

async def run_status_webhook_task(meeting_id: int, status_change_info: dict = None):
    """
    Run webhook task with proper database session management.
    
    Args:
        meeting_id: ID of the meeting to send webhook for
        status_change_info: Optional dict containing status change details
    """
    logger.info(f"Starting webhook task runner for meeting {meeting_id}")
    
    async with async_session_local() as db:
        try:
            # Eager load the User object to avoid separate queries in webhook task
            meeting = await db.get(Meeting, meeting_id, options=[selectinload(Meeting.user)])
            if not meeting:
                logger.error(f"Could not find meeting with ID {meeting_id} for webhook task")
                return

            # Run the webhook task
            await send_status_webhook(meeting, db, status_change_info)
            
        except Exception as e:
            logger.error(f"Error in webhook task runner for meeting_id {meeting_id}: {e}", exc_info=True)


