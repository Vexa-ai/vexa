"""The meeting-api SQLAlchemy models — the per-service mirror of the backing-stack
``meetings`` / ``transcriptions`` / ``meeting_sessions`` tables.

SELF-CONTAINED per-service mirror (the SSOT is ``identity/services/admin-api/.../schema/
models.py``). Co-located here — NOT imported across the lane seam — for the same reason
``obs.py`` is duplicated per service: it keeps the cross-domain import-boundary gates
(``gate:isolation-py`` / ``gate:graph-py``) clean while binding the SAME physical Postgres
schema (identical table names + columns). ``gate:isolation-py`` PRE-ALLOWS a ``meeting_api →
admin_api`` edge for these models, but we DO NOT take it: mirroring keeps the monolith
import-free of the identity domain (no real edge is created), exactly as the folded collector
already did.

SQLAlchemy is imported at MODULE load, so this module is only imported lazily by the production
``bot_spawn`` / ``recordings`` / ``collector.adapters`` paths at runtime — never during the gate
venv's test run (the in-memory fakes never touch it). That is why ``pyproject.toml`` carries no
``greenlet`` pin.

Recordings + notes live in ``meetings.data`` JSONB (there is NO separate recordings table — see
``schema/MIGRATION-0001-drop-recordings.md``). ``MeetingSession`` keys N sessions per meeting by
``session_uid`` (one per bot connection), the linkage ``bot_spawn`` eager-creates on spawn and
``recordings`` looks up on chunk upload.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func, text

Base = declarative_base()


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(100), nullable=False)
    platform_specific_id = Column(String(255), index=True, nullable=True)
    status = Column(String(50), nullable=False, default="requested", index=True)
    bot_container_id = Column(String(255), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    # recordings[] are stored in this JSONB blob — NOT in a `recordings` table.
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    transcriptions = relationship("Transcription", back_populates="meeting")
    sessions = relationship(
        "MeetingSession", back_populates="meeting", cascade="all, delete-orphan"
    )

    @property
    def native_meeting_id(self):
        return self.platform_specific_id

    __table_args__ = (
        Index(
            "ix_meeting_user_platform_native_id_created_at",
            "user_id", "platform", "platform_specific_id", "created_at",
        ),
        Index("ix_meeting_data_gin", "data", postgresql_using="gin"),
        # #800: the collector's list_meetings UNIONs three access branches, one scan path each —
        # owner top-N, transcript-share containment, workspace top-N. The whole-column GIN above
        # cannot serve a containment probe on the `transcript_viewers` key alone, and the
        # single-column created_at index invites the catastrophic backward-walk plan the UNION
        # exists to avoid.
        # ⚠ PROD ROLLOUT: build these CONCURRENTLY out-of-band before deploying (vexa-platform
        # O-book O4); in-band CREATE INDEX locks `meetings` under live traffic.
        Index("ix_meeting_user_created_at", "user_id", "created_at"),
        Index("ix_meeting_transcript_viewers_gin",
              text("(data -> 'transcript_viewers') jsonb_path_ops"), postgresql_using="gin"),
        Index("ix_meeting_workspace_created_at", text("(data ->> 'workspace_id')"), "created_at"),
        # ROB1/ROB2 DB-level backstop: at most ONE ACTIVE (non-terminal) meeting per
        # (user, platform, native_meeting_id). A unique PARTIAL index — terminal rows
        # (completed/failed) are NOT covered, so continue_meeting can reopen a prior terminal row and
        # a user can re-meet the same native id once the previous run ends. Concurrent duplicate
        # spawns that slip past the in-txn advisory-lock dedup (e.g. across meeting-api processes) hit
        # this index → IntegrityError → mapped to DuplicateMeeting in create_meeting_guarded.
        Index(
            "uq_meeting_active_user_platform_native",
            "user_id", "platform", "platform_specific_id",
            unique=True,
            postgresql_where=text("status NOT IN ('completed', 'failed')"),
        ),
    )


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
        Index("ix_transcription_meeting_start", "meeting_id", "start_time"),
        # The segment identity the db-writer upserts on (ON CONFLICT (meeting_id, segment_id)
        # WHERE segment_id IS NOT NULL) — mirrors the AUTHORITATIVE admin-api schema
        # (admin_api.schema.models), which owns the table; kept in sync here so a
        # metadata.create_all from this mirror builds the same shape.
        Index("ix_transcription_meeting_segment", "meeting_id", "segment_id",
              unique=True, postgresql_where=segment_id.isnot(None)),
    )


class MeetingSession(Base):
    """N sessions per meeting, keyed by ``session_uid`` (one per bot connection/reconnect).

    ``bot_spawn`` eager-creates a row on spawn (``session_uid`` == the ``connectionId`` minted into
    the bot's invocation); ``recordings`` looks the row up by ``session_uid`` when the bot uploads
    a chunk, so the upload finds its meeting even before the bot reports ``active``.
    """

    __tablename__ = "meeting_sessions"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    session_uid = Column(String, nullable=False, index=True)
    session_start_time = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    meeting = relationship("Meeting", back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("meeting_id", "session_uid", name="_meeting_session_uc"),
    )
