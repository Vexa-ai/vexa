"""
GIVEN helpers for meeting data setup and validation.
"""
import logging
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from shared_models.models import Meeting, MeetingSession


async def meeting_exists_for_session(db_session, session_id: str):
    """GIVEN a meeting exists for the specified session ID."""
    # Find the meeting session
    session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_id)
    session_result = await db_session.execute(session_stmt)
    meeting_session = session_result.scalars().first()
    
    if not meeting_session:
        return None, None
        
    # Load meeting with user relationship
    meeting = await db_session.get(
        Meeting, 
        meeting_session.meeting_id, 
        options=[selectinload(Meeting.user)]
    )
    
    return meeting, meeting_session


def meeting_data_is_valid(meeting, meeting_session, session_id: str, logger: logging.Logger):
    """GIVEN meeting data exists and is valid for replay."""
    if not meeting:
        raise AssertionError(f"No meeting found for session_uid: {session_id}")
    
    if not meeting.user:
        raise AssertionError(f"Meeting {meeting.id} has no associated user")
    
    # Log meeting info
    logger.info(f"Meeting ID: {meeting.id}, User: {meeting.user.email}")
    logger.info(f"Platform: {meeting.platform}, Status: {meeting.status}")
    
    # Validate basic meeting data
    assert meeting.id is not None, "Meeting ID should be set"
    assert meeting.user_id is not None, "Meeting should have user_id"
    assert meeting.platform is not None, "Meeting should have platform"
    logger.info(f"Meeting {meeting.id} has basic data")
    
    # Validate user relationship data
    assert meeting.user is not None, "Meeting should have user relationship loaded"
    assert meeting.user.email is not None, "User should have email"
    logger.info(f"User relationship loaded: {meeting.user.email}")
    
    # Validate webhook configuration if present
    if meeting.user.data and meeting.user.data.get('webhook_url'):
        webhook_url = meeting.user.data['webhook_url']
        assert webhook_url.startswith(('http://', 'https://')), "Webhook URL should be valid"
        logger.info(f"Webhook configured: {webhook_url}")
    else:
        logger.info("No webhook configured (will skip webhook delivery)")
    
    # Validate session data
    assert meeting_session is not None, "Meeting session should exist"
    assert meeting_session.session_uid == session_id, "Session UID should match"
    logger.info(f"Meeting session exists: {meeting_session.session_uid}")
    
    # Check participants data if available
    if meeting.data and 'participants' in meeting.data:
        participants = meeting.data['participants']
        logger.info(f"Participants data: {len(participants)} participants")
    else:
        logger.info("No participants data (will use empty array)")
    
    logger.info("All validation checks passed - ready for replay")
    return meeting, meeting_session