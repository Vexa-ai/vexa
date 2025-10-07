import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting, MeetingSession, User
from sqlalchemy import select
from dependency_injector.wiring import inject, Provide

from app.core.protocols.logger_protocol import LoggerProtocol

@inject
async def run(
    meeting: Meeting, 
    db: AsyncSession,
    logger: LoggerProtocol = Provide["logging.webhook_logger"]
):
    """
    Sends a webhook with the completed meeting details to a user-configured URL.
    """
    logger.info(f"Executing send_webhook task for meeting {meeting.id}")

    # The user should be loaded on the meeting object already by the task runner
    user = meeting.user
    if not user:
        error_msg = f"Could not find user on meeting object {meeting.id}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    try:

        # Check if user has a webhook URL configured
        webhook_url = user.data.get('webhook_url') if user.data and isinstance(user.data, dict) else None

        if not webhook_url:
            logger.info(f"No webhook URL configured for user {user.email} (meeting {meeting.id})")
            return
        
        session_statement = (
            select(MeetingSession.session_uid)
            .where(MeetingSession.meeting_id == meeting.id)
            .order_by(MeetingSession.session_start_time.asc())
            .limit(1)
        )

        session_result = await db.execute(session_statement)
        connection_id = session_result.scalars().first() 

        data = meeting.data or {}

        # Prepare the webhook payload
        payload = {
            'id': meeting.id,
            'user_id': meeting.user_id,
            'platform': meeting.platform,
            'native_meeting_id': meeting.native_meeting_id,
            'constructed_meeting_url': meeting.constructed_meeting_url,
            'status': meeting.status,
            'bot_container_id': meeting.bot_container_id,
            'connection_id': connection_id if connection_id else None,
            'start_time': meeting.start_time.isoformat() if meeting.start_time else None,
            'end_time': meeting.end_time.isoformat() if meeting.end_time else None,
            'data': data,
            'created_at': meeting.created_at.isoformat() if meeting.created_at else None,
            'updated_at': meeting.updated_at.isoformat() if meeting.updated_at else None,
            'participants': data.get('participants', []),
        }

        # Send the webhook
        async with httpx.AsyncClient() as client:
            logger.info(f"Sending webhook to {webhook_url} for meeting {meeting.id}")
            logger.info(f"Webhook payload keys: {list(payload.keys())}")
            logger.info(f"Payload connection_id: {payload.get('connection_id')}")
            logger.info(f"Payload meeting status: {payload.get('status')}")
            
            response = await client.post(
                webhook_url,
                json=payload,
                timeout=30.0,
                headers={'Content-Type': 'application/json'}
            )
            
            logger.info(f"Webhook response status: {response.status_code}")
            logger.info(f"Webhook response headers: {dict(response.headers)}")
            logger.info(f"Webhook response body: {response.text[:500]}")  # First 500 chars
            
            if response.status_code >= 200 and response.status_code < 300:
                logger.info(f"Successfully sent webhook for meeting {meeting.id} to {webhook_url}")
            else:
                logger.warning(f"Webhook for meeting {meeting.id} returned status {response.status_code}: {response.text}")

    except httpx.RequestError as e:
        logger.error(f"Failed to send webhook for meeting {meeting.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending webhook for meeting {meeting.id}: {e}", exc_info=True) 