import sqlalchemy
from sqlalchemy import (Column, String, Text, Integer, DateTime, Float, ForeignKey, Index, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime # Needed for Transcription model default
from shared_models.schemas import Platform # Import Platform for the static method
from typing import Optional # Added for the return type hint in constructed_meeting_url

# Define the base class for declarative models
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True) # Added index=True
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(100))
    image_url = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    max_concurrent_bots = Column(Integer, nullable=False, server_default='1', default=1) # Added field
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})
    
    meetings = relationship("Meeting", back_populates="user")
    api_tokens = relationship("APIToken", back_populates="user")

class APIToken(Base):
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True, index=True) # Added index=True
    token = Column(String(255), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now())
    
    user = relationship("User", back_populates="api_tokens")

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    platform = Column(String(100), nullable=False) # e.g., 'google_meet', 'zoom'
    # Database column name is platform_specific_id but we use native_meeting_id in the code
    platform_specific_id = Column(String(255), index=True, nullable=True)
    status = Column(String(50), nullable=False, default='requested', index=True)  # Values: requested, joining, awaiting_admission, active, completed, failed
    bot_container_id = Column(String(255), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    data = Column(JSONB, nullable=False, default=text("'{}'::jsonb"))
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="meetings")
    transcriptions = relationship("Transcription", back_populates="meeting")
    sessions = relationship("MeetingSession", back_populates="meeting", cascade="all, delete-orphan")
    recordings = relationship("Recording", back_populates="meeting", cascade="all, delete-orphan")

    # Add composite index for efficient lookup by user, platform, and native ID, including created_at for sorting
    __table_args__ = (
        Index(
            'ix_meeting_user_platform_native_id_created_at',
            'user_id',
            'platform',
            'platform_specific_id',
            'created_at' # Include created_at because the query orders by it
        ),
        Index('ix_meeting_data_gin', 'data', postgresql_using='gin'),
        # Optional: Unique constraint (uncomment if needed, ensure native_meeting_id cannot be NULL if unique)
        # UniqueConstraint('user_id', 'platform', 'platform_specific_id', name='_user_platform_native_id_uc'),
    )

    # Add property getters/setters for compatibility
    @property
    def native_meeting_id(self):
        return self.platform_specific_id
        
    @native_meeting_id.setter
    def native_meeting_id(self, value):
        self.platform_specific_id = value
        
    @property
    def constructed_meeting_url(self) -> Optional[str]: # Added return type hint
        # Calculate the URL on demand using the static method from schemas.py
        if self.platform and self.platform_specific_id:
             return Platform.construct_meeting_url(self.platform, self.platform_specific_id)
        return None

class Transcription(Base):
    __tablename__ = "transcriptions"
    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True) # Changed nullable to False, should always link
    # Removed redundant platform, meeting_url, token, client_uid, server_id as they belong to the Meeting
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    speaker = Column(String(255), nullable=True) # Speaker identifier
    language = Column(String(10), nullable=True) # e.g., 'en', 'es'
    created_at = Column(DateTime, default=datetime.utcnow)

    meeting = relationship("Meeting", back_populates="transcriptions")
    
    session_uid = Column(String, nullable=True, index=True) # Link to the specific bot session

    # Index for efficient querying by meeting_id and start_time
    __table_args__ = (Index('ix_transcription_meeting_start', 'meeting_id', 'start_time'),)

# New table to store session start times
class MeetingSession(Base):
    __tablename__ = 'meeting_sessions'
    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey('meetings.id'), nullable=False, index=True)
    session_uid = Column(String, nullable=False, index=True) # Stores the 'uid' (based on connectionId)
    # Store timezone-aware timestamp to avoid ambiguity
    session_start_time = Column(sqlalchemy.DateTime(timezone=True), nullable=False, server_default=func.now())

    meeting = relationship("Meeting", back_populates="sessions") # Define relationship

    __table_args__ = (UniqueConstraint('meeting_id', 'session_uid', name='_meeting_session_uc'),) # Ensure unique session per meeting


class Recording(Base):
    """A recording session — container for one or more media artifacts (audio, video, screenshots)."""
    __tablename__ = "recordings"
    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_uid = Column(String, nullable=True, index=True)

    # Source tracking
    source = Column(String(50), nullable=False, default='bot')  # 'bot', 'upload', 'url'

    # Status
    status = Column(String(50), nullable=False, default='in_progress', index=True)  # 'in_progress', 'uploading', 'completed', 'failed'
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), index=True)
    completed_at = Column(DateTime, nullable=True)

    meeting = relationship("Meeting", back_populates="recordings")
    user = relationship("User")
    media_files = relationship("MediaFile", back_populates="recording", cascade="all, delete-orphan")
    transcription_jobs = relationship("TranscriptionJob", back_populates="recording", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_recording_meeting_session', 'meeting_id', 'session_uid'),
        Index('ix_recording_user_created', 'user_id', 'created_at'),
    )


class MediaFile(Base):
    """An individual media artifact (audio file, video file, screenshot) belonging to a Recording."""
    __tablename__ = "media_files"
    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False, index=True)

    # Type and format
    type = Column(String(50), nullable=False)  # 'audio', 'video', 'screenshot'
    format = Column(String(20), nullable=False)  # 'wav', 'webm', 'opus', 'mp3', 'jpg', 'png'

    # Storage
    storage_path = Column(String(1024), nullable=False)
    storage_backend = Column(String(50), nullable=False, default='minio')  # 'minio', 's3', 'local'

    # Metadata
    file_size_bytes = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)  # For time-based media (audio, video)
    extra_metadata = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})  # sample_rate, resolution, fps, screenshot_timestamp, etc.

    created_at = Column(DateTime, server_default=func.now())

    recording = relationship("Recording", back_populates="media_files")


class TranscriptionJob(Base):
    """A batch transcription job — processes a recording through the transcription service."""
    __tablename__ = "transcription_jobs"
    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Job config
    language = Column(String(10), nullable=True)
    task = Column(String(50), nullable=False, default='transcribe')

    # Status tracking
    status = Column(String(50), nullable=False, default='pending', index=True)  # 'pending', 'processing', 'completed', 'failed'
    error_message = Column(Text, nullable=True)
    progress = Column(Float, nullable=True)  # 0.0 to 1.0

    # Results
    segments_count = Column(Integer, nullable=True)
    session_uid = Column(String, nullable=True, index=True)  # The session_uid used for transcription segments

    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    recording = relationship("Recording", back_populates="transcription_jobs")
    meeting = relationship("Meeting")
    user = relationship("User")

    __table_args__ = (
        Index('ix_transcription_job_status_created', 'status', 'created_at'),
        Index('ix_transcription_job_user_created', 'user_id', 'created_at'),
    )
