import sqlalchemy
from sqlalchemy import (
    Column, String, Text, Integer, BigInteger, DateTime, Float,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from typing import Optional

from .schemas import Platform

Base = declarative_base()


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(100), nullable=False)
    platform_specific_id = Column(String(255), index=True, nullable=True)
    status = Column(String(50), nullable=False, default='requested', index=True)
    bot_container_id = Column(String(255), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    data = Column(JSONB, nullable=False, default=text("'{}'::jsonb"))
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    transcriptions = relationship("Transcription", back_populates="meeting")
    sessions = relationship("MeetingSession", back_populates="meeting", cascade="all, delete-orphan")
    recordings = relationship("Recording", back_populates="meeting", cascade="all, delete-orphan")
    recording_frames = relationship("RecordingFrame", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_meeting_user_platform_native_id_created_at',
              'user_id', 'platform', 'platform_specific_id', 'created_at'),
        Index('ix_meeting_data_gin', 'data', postgresql_using='gin'),
    )

    @property
    def native_meeting_id(self):
        return self.platform_specific_id

    @native_meeting_id.setter
    def native_meeting_id(self, value):
        self.platform_specific_id = value

    @property
    def constructed_meeting_url(self) -> Optional[str]:
        if self.platform and self.platform_specific_id:
            passcode = (
                (self.data or {}).get('passcode')
                if isinstance(self.data, dict) else None
            )
            return Platform.construct_meeting_url(
                self.platform, self.platform_specific_id, passcode=passcode,
            )
        return None


class Transcription(Base):
    __tablename__ = "transcriptions"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    speaker = Column(String(255), nullable=True)
    language = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    session_uid = Column(String, nullable=True, index=True)
    segment_id = Column(String, nullable=True)

    meeting = relationship("Meeting", back_populates="transcriptions")

    __table_args__ = (
        Index('ix_transcription_meeting_start', 'meeting_id', 'start_time'),
        Index('ix_transcription_meeting_segment', 'meeting_id', 'segment_id',
              unique=True, postgresql_where=segment_id.isnot(None)),
    )


class MeetingSession(Base):
    __tablename__ = 'meeting_sessions'

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey('meetings.id'), nullable=False, index=True)
    session_uid = Column(String, nullable=False, index=True)
    session_start_time = Column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    meeting = relationship("Meeting", back_populates="sessions")

    __table_args__ = (
        UniqueConstraint('meeting_id', 'session_uid', name='_meeting_session_uc'),
    )


class Recording(Base):
    __tablename__ = "recordings"

    id = Column(BigInteger, primary_key=True, index=True)  # BigInteger: bot generates snowflake IDs exceeding int32 range
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_uid = Column(String, nullable=True, index=True)
    source = Column(String(50), nullable=False, default='bot')
    status = Column(String(50), nullable=False, default='in_progress', index=True)
    error_message = Column(Text, nullable=True)
    frames_status = Column(String(20), nullable=False, default='none', server_default=text("'none'"))
    extra_metadata = Column(
        "metadata", JSONB, nullable=False,
        server_default=text("'{}'::jsonb"), default=lambda: {},
    )
    created_at = Column(DateTime, server_default=func.now(), index=True)
    completed_at = Column(DateTime, nullable=True)

    meeting = relationship("Meeting", back_populates="recordings")
    media_files = relationship("MediaFile", back_populates="recording", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_recording_meeting_session', 'meeting_id', 'session_uid'),
        Index('ix_recording_user_created', 'user_id', 'created_at'),
    )


class RecordingFrame(Base):
    __tablename__ = "recording_frames"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    recording_id = Column(BigInteger, nullable=False)  # NOT a FK — per D-07/D-26 (JSONB mode has no recordings SQL rows). BigInteger: bot generates snowflake IDs exceeding int32 range.
    session_uid = Column(String, nullable=True)  # nullable to match Recording.session_uid
    timestamp_s = Column(Integer, nullable=False)  # Integer, NOT Float — schema-sync cannot alter types after creation
    storage_path = Column(String(1024), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting", back_populates="recording_frames")

    __table_args__ = (
        UniqueConstraint('meeting_id', 'recording_id', 'session_uid', 'timestamp_s', name='uq_recording_frame_identity'),
        Index('ix_frames_meeting_ts', 'meeting_id', 'timestamp_s'),
    )


class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(BigInteger, primary_key=True, index=True)  # BigInteger: bot generates snowflake IDs exceeding int32 range
    recording_id = Column(BigInteger, ForeignKey("recordings.id"), nullable=False, index=True)  # BigInteger: matches recordings.id
    type = Column(String(50), nullable=False)
    format = Column(String(20), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    storage_backend = Column(String(50), nullable=False, default='minio')
    file_size_bytes = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    extra_metadata = Column(
        "metadata", JSONB, nullable=False,
        server_default=text("'{}'::jsonb"), default=lambda: {},
    )
    created_at = Column(DateTime, server_default=func.now())

    recording = relationship("Recording", back_populates="media_files")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    external_event_id = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    start_time = Column(sqlalchemy.DateTime(timezone=True), nullable=False)
    end_time = Column(sqlalchemy.DateTime(timezone=True), nullable=True)
    meeting_url = Column(Text, nullable=True)
    platform = Column(Text, nullable=True)
    status = Column(Text, nullable=False, server_default='pending', default='pending')
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True)
    sync_token = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting")

    __table_args__ = (
        UniqueConstraint('user_id', 'external_event_id', name='uq_calendar_event_user_ext_id'),
        Index('ix_calendar_events_start_time', 'start_time'),
        Index('ix_calendar_events_status', 'status'),
    )
