import logging
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models.models import Meeting, User
from shared_models.webhook_url import validate_webhook_url
from shared_models.webhook_delivery import deliver
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# Map meeting status to webhook event type
STATUS_TO_EVENT: Dict[str, str] = {
    "completed": "meeting.completed",
    "active": "meeting.started",
    "failed": "bot.failed",
}


def _resolve_event_type(meeting_status: str) -> str:
    """Map a meeting status to the corresponding webhook event type."""
    return STATUS_TO_EVENT.get(meeting_status, "meeting.status_change")


def _is_event_enabled(user_data: Optional[Dict], event_type: str) -> bool:
    """Check if the user has enabled this event type in their webhook config.

    Defaults:
      - meeting.completed and transcript.ready are ON by default.
      - Everything else is OFF unless explicitly enabled.
    """
    default_enabled = {"meeting.completed"}

    events_config = (user_data or {}).get("webhook_events")
    if not events_config or not isinstance(events_config, dict):
        # No explicit config — fall back to defaults
        return event_type in default_enabled

    # Explicit config exists — respect it
    enabled = events_config.get(event_type)
    if enabled is not None:
        return bool(enabled)

    # Event not mentioned in config — use defaults
    return event_type in default_enabled


async def run(meeting: Meeting, db: AsyncSession, status_change_info: Optional[Dict[str, Any]] = None):
    """
    Sends a webhook for meeting status changes, filtered by user's event preferences.
    Uses exponential backoff retry and HMAC signing when webhook_secret is set.

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
        user = meeting.user
        if not user:
            logger.error(f"Could not find user on meeting object {meeting.id}")
            return

        webhook_url = user.data.get('webhook_url') if user.data and isinstance(user.data, dict) else None
        if not webhook_url:
            logger.info(f"No webhook URL configured for user {user.email} (meeting {meeting.id})")
            return

        # Check if user has enabled this event type
        event_type = _resolve_event_type(meeting.status)
        if not _is_event_enabled(user.data, event_type):
            logger.info(
                f"Webhook event '{event_type}' not enabled for user {user.email} "
                f"(meeting {meeting.id}, status {meeting.status}). Skipping."
            )
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
            'event_type': event_type,
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

        if status_change_info:
            payload['status_change'] = {
                'from': status_change_info.get('old_status'),
                'to': status_change_info.get('new_status', meeting.status),
                'reason': status_change_info.get('reason'),
                'timestamp': status_change_info.get('timestamp'),
                'transition_source': status_change_info.get('transition_source')
            }

        await deliver(
            url=webhook_url,
            payload=payload,
            webhook_secret=webhook_secret,
            timeout=30.0,
            label=f"status-webhook meeting={meeting.id} status={meeting.status}",
        )

    except Exception as e:
        logger.error(f"Unexpected error sending status webhook for meeting {meeting.id}: {e}", exc_info=True)
