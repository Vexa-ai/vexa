import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select
from shared_models.models import Meeting, MeetingSession
from app.redis_utils import get_redis_client

logger = logging.getLogger(__name__)

SPEAKER_EVENT_KEY_PREFIX = "speaker_events"


async def run(meeting: Meeting, db: AsyncSession):
    """
    Persists speaker events from Redis sorted sets to meeting.data JSONB.

    Speaker events (SPEAKER_START/SPEAKER_END with participant names) are stored
    in Redis with a 24h TTL. This task saves them durably so they survive beyond
    the TTL — enabling deferred transcription with speaker mapping later.

    Runs for ALL meetings regardless of transcribe_enabled.
    """
    meeting_id = meeting.id
    logger.info(f"Persisting speaker events for meeting {meeting_id}")

    redis_client = get_redis_client()
    if not redis_client:
        logger.warning(f"Redis not available, cannot persist speaker events for meeting {meeting_id}")
        return

    # Load sessions to get session_uids
    sessions_result = await db.execute(
        select(MeetingSession).where(MeetingSession.meeting_id == meeting_id)
    )
    sessions = sessions_result.scalars().all()

    if not sessions:
        logger.info(f"No sessions found for meeting {meeting_id}, skipping speaker persistence")
        return

    all_events = []
    for session in sessions:
        session_uid = session.session_uid
        key = f"{SPEAKER_EVENT_KEY_PREFIX}:{session_uid}"

        try:
            # Fetch all speaker events from Redis sorted set (score = relative timestamp ms)
            raw_events = await redis_client.zrangebyscore(key, min=0, max="+inf", withscores=True)
            if not raw_events:
                logger.info(f"No speaker events in Redis for session {session_uid}")
                continue

            for event_data, score in raw_events:
                try:
                    event = json.loads(event_data) if isinstance(event_data, str) else event_data
                    event["session_uid"] = session_uid
                    event["relative_timestamp_ms"] = int(score)
                    all_events.append(event)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse speaker event for session {session_uid}: {e}")

            logger.info(f"Read {len(raw_events)} speaker events from Redis for session {session_uid}")
        except Exception as e:
            logger.error(f"Failed to read speaker events from Redis for session {session_uid}: {e}", exc_info=True)

    if not all_events:
        logger.info(f"No speaker events found across all sessions for meeting {meeting_id}")
        return

    # Sort by timestamp
    all_events.sort(key=lambda e: e.get("relative_timestamp_ms", 0))

    # Write to meeting.data JSONB
    data = dict(meeting.data) if meeting.data else {}
    data["speaker_events"] = all_events
    meeting.data = data
    flag_modified(meeting, "data")

    logger.info(f"Persisted {len(all_events)} speaker events to meeting.data for meeting {meeting_id}")
