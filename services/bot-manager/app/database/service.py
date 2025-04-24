from .models import User, Meeting, Transcription
import logging
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.future import select

# Import async session maker
from shared_models.database import async_session_local

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Service for managing transcription data in the database"""
    
    @staticmethod
    async def get_or_create_user(user_id, name=None, email=None):
        """Get or create a user record asynchronously."""
        async with async_session_local() as session:
            try:
                result = await session.execute(select(User).filter_by(id=user_id))
                user = result.scalars().first()
                if not user:
                    user = User(id=user_id, name=name, email=email)
                    session.add(user)
                    await session.commit()
                    logger.info(f"Created user: {user_id}")
                return user
            except SQLAlchemyError as e:
                logger.error(f"Error getting/creating user: {e}")
                raise
    
    @staticmethod
    def create_meeting(meeting_id, user_id, title=None):
        """Create a new meeting record"""
        session = get_session()
        try:
            # Ensure user exists
            TranscriptionService.get_or_create_user(user_id)
            
            # Create meeting
            meeting = Meeting(
                id=meeting_id,
                user_id=user_id,
                title=title,
                start_time=datetime.utcnow()
            )
            session.add(meeting)
            session.commit()
            logger.info(f"Created meeting: {meeting_id} for user: {user_id}")
            return meeting
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error creating meeting: {e}")
            raise
        finally:
            session.close()
    
    @staticmethod
    def end_meeting(meeting_id):
        """Mark a meeting as ended"""
        session = get_session()
        try:
            meeting = session.query(Meeting).filter_by(id=meeting_id).first()
            if meeting:
                meeting.end_time = datetime.utcnow()
                session.commit()
                logger.info(f"Ended meeting: {meeting_id}")
                return meeting
            logger.warning(f"Meeting not found: {meeting_id}")
            return None
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error ending meeting: {e}")
            raise
        finally:
            session.close()
    
    @staticmethod
    def add_transcription(meeting_id, content, speaker=None, confidence=None):
        """Add a transcription entry to a meeting"""
        session = get_session()
        try:
            # Check if meeting exists
            meeting = session.query(Meeting).filter_by(id=meeting_id).first()
            if not meeting:
                logger.warning(f"Meeting not found: {meeting_id}, cannot add transcription")
                return None
            
            # Create transcription
            transcription = Transcription(
                meeting_id=meeting_id,
                speaker=speaker,
                content=content,
                timestamp=datetime.utcnow(),
                confidence=confidence
            )
            session.add(transcription)
            session.commit()
            logger.info(f"Added transcription to meeting: {meeting_id}")
            return transcription
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error adding transcription: {e}")
            raise
        finally:
            session.close()
    
    @staticmethod
    def get_meeting_transcriptions(meeting_id, start_time=None, end_time=None):
        """Get all transcriptions for a meeting, optionally filtered by time range"""
        session = get_session()
        try:
            query = session.query(Transcription).filter_by(meeting_id=meeting_id)
            
            if start_time:
                query = query.filter(Transcription.timestamp >= start_time)
            if end_time:
                query = query.filter(Transcription.timestamp <= end_time)
                
            query = query.order_by(Transcription.timestamp)
            transcriptions = query.all()
            
            result = []
            for t in transcriptions:
                result.append({
                    "id": t.id,
                    "speaker": t.speaker,
                    "content": t.content,
                    "timestamp": t.timestamp.isoformat(),
                    "confidence": t.confidence
                })
            
            return result
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving transcriptions: {e}")
            raise
        finally:
            session.close()
    
    @staticmethod
    def get_user_meetings(user_id):
        """Get all meetings for a user"""
        session = get_session()
        try:
            meetings = session.query(Meeting).filter_by(user_id=user_id).all()
            
            result = []
            for m in meetings:
                result.append({
                    "id": m.id,
                    "title": m.title,
                    "start_time": m.start_time.isoformat() if m.start_time else None,
                    "end_time": m.end_time.isoformat() if m.end_time else None
                })
            
            return result
        except SQLAlchemyError as e:
            logger.error(f"Error retrieving user meetings: {e}")
            raise
        finally:
            session.close() 