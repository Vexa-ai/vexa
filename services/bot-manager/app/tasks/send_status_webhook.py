import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting, User
from shared_models.webhook_url import validate_webhook_url
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _build_webhook_headers(user_data: Optional[dict]) -> dict:
    """Build headers for webhook request, including Authorization if webhook_secret is set."""
    headers = {'Content-Type': 'application/json'}
    if user_data and isinstance(user_data, dict):
        secret = user_data.get('webhook_secret')
        if secret and isinstance(secret, str) and secret.strip():
            headers['Authorization'] = f'Bearer {secret.strip()}'
    return headers


async def run(meeting: Meeting, db: AsyncSession, status_change_info: Optional[Dict[str, Any]] = None):
    """
    Sends a webhook for ANY meeting status change, not just completion.

    Args:
        meeting: Meeting object with current status
        db: Database session
        status_change_info: Optional dict containing status change details like:
            - old_status: Previous status
            - new_status: Current status
            - reason: Reason for change
            - timestamp: When change occurred
    """
    logger.info(f"Executing send_status_webhook task for meeting {meeting.id} with status {meeting.status}")

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

        # SSRF defense: validate URL before sending (catches pre-patch or admin-set URLs)
        try:
            validate_webhook_url(webhook_url)
        except ValueError as e:
            logger.warning(f"Webhook URL validation failed for meeting {meeting.id}: {e}. Skipping.")
            return

        # Prepare the webhook payload with status change information
        payload = {
            'event_type': 'meeting.status_change',
            'meeting': {
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
        }

        # Add status change information if provided
        if status_change_info:
            payload['status_change'] = {
                'from': status_change_info.get('old_status'),
                'to': status_change_info.get('new_status', meeting.status),
                'reason': status_change_info.get('reason'),
                'timestamp': status_change_info.get('timestamp'),
                'transition_source': status_change_info.get('transition_source')
            }

        headers = _build_webhook_headers(user.data)

        # Send the webhook (follow_redirects=True for backward compatibility with
        # receivers that use redirects; URL validated at storage and send time)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            logger.info(f"Sending status webhook to {webhook_url} for meeting {meeting.id} (status: {meeting.status})")
            response = await client.post(
                webhook_url,
                json=payload,
                timeout=30.0,
                headers=headers
            )
            
            if response.status_code >= 200 and response.status_code < 300:
                logger.info(f"Successfully sent status webhook for meeting {meeting.id} to {webhook_url}")
            else:
                logger.warning(f"Status webhook for meeting {meeting.id} returned status {response.status_code}: {response.text}")

    except httpx.RequestError as e:
        logger.error(f"Failed to send status webhook for meeting {meeting.id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending status webhook for meeting {meeting.id}: {e}", exc_info=True)
