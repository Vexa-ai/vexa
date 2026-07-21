"""The v0.12 backing-stack schema — the SQLAlchemy source-of-truth.

Derived from the parent (re-read, not blind-copied):
  - identity tables    ← `libs/admin-models/admin_models/models.py`   (User, APIToken)
  - meeting tables     ← `services/meeting-api/meeting_api/models.py` (Meeting, Transcription, MeetingSession)

ONE `Base` here (the parent split identity vs meeting bases and bridged the FK via
`ensure_schema(prerequisites=...)`). Co-locating them in one metadata is the same shape —
`create_all` emits tables in FK order, so `users` lands before `api_tokens` and `meetings`
before its children. The cross-domain FK (api_tokens.user_id → users.id, meetings has a
logical user_id) is preserved.

DROPPED vs the parent: the `recordings` + `media_files` tables. See O-STACK-1 migration note
(`schema/MIGRATION-0001-drop-recordings.md`) — they are write-never dead columns; recordings
live in `meetings.data['recordings'][]` JSONB (the `internal_upload_recording` writer). The
parent keeps the ORM classes only as a legacy READ fallback guarded by
`to_regclass('public.recordings') IS NOT NULL`, so omitting the tables is safe.
"""
from sqlalchemy import (
    Column, String, Text, Integer, DateTime, Float,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()


# --------------------------------------------------------------------------- #
# identity tables (parent: libs/admin-models/admin_models/models.py)
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(100))
    image_url = Column(Text)
    created_at = Column(DateTime, server_default=func.now(), default=func.now())
    max_concurrent_bots = Column(Integer, nullable=False, server_default="3", default=3)
    # webhook_url / webhook_secret / webhook_events live here (surfaced by /internal/validate)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})

    api_tokens = relationship("APIToken", back_populates="user")


class PlatformSetting(Base):
    """Deployment-wide runtime config, one JSONB value per key (`models`, `transcription`).
    The DB layer between per-user prefs (users.data) and the process env: services resolve
    user > platform_settings > env. Written only over the internal tier (the terminal's
    admin-gated settings editor fronts it); read over the same edge by agent-api/meeting-api."""
    __tablename__ = "platform_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class APIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(255), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scopes = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    name = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="api_tokens")


# --------------------------------------------------------------------------- #
# meeting tables (parent: services/meeting-api/meeting_api/models.py)
# --------------------------------------------------------------------------- #
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
    sessions = relationship("MeetingSession", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_meeting_user_platform_native_id_created_at",
              "user_id", "platform", "platform_specific_id", "created_at"),
        Index("ix_meeting_data_gin", "data", postgresql_using="gin"),
        # #800 (mirror of meeting-api's sessions/models.py): the collector's list_meetings UNIONs
        # three access branches, one scan path each — owner top-N, transcript-share containment,
        # workspace top-N. The whole-column GIN above cannot serve a containment probe on the
        # `transcript_viewers` key alone.
        # ⚠ PROD ROLLOUT: build CONCURRENTLY out-of-band before deploying (vexa-platform O-book
        # O4); _sync_indexes' in-band CREATE INDEX locks `meetings` under live traffic.
        Index("ix_meeting_user_created_at", "user_id", "created_at"),
        Index("ix_meeting_transcript_viewers_gin",
              text("(data -> 'transcript_viewers') jsonb_path_ops"), postgresql_using="gin"),
        Index("ix_meeting_workspace_created_at", text("(data ->> 'workspace_id')"), "created_at"),
        # ROB1/ROB2 DB-level backstop (mirror of meeting-api's sessions/models.py): at most ONE
        # ACTIVE (non-terminal) meeting per (user, platform, native_meeting_id). Unique PARTIAL
        # index — terminal rows (completed/failed) are NOT covered, so a user can re-meet the same
        # native id once the prior run ends and continue_meeting can reopen a terminal row. The
        # in-txn pg_advisory_xact_lock in create_meeting_guarded serializes same-process spawns;
        # this index backstops the cross-process race → IntegrityError → DuplicateMeeting.
        #
        # ⚠ PROD ROLLOUT: this CREATE UNIQUE INDEX FAILS on a table that already holds duplicate
        # active rows, and _sync_indexes swallows that failure silently. The index must be built
        # out-of-band on prod (dedup + CREATE UNIQUE INDEX CONCURRENTLY) BEFORE this change deploys
        # — see schema/MIGRATION-0002-meeting-active-dedup-index.md.
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
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    session_uid = Column(String, nullable=True, index=True)
    segment_id = Column(String, nullable=True)

    meeting = relationship("Meeting", back_populates="transcriptions")

    __table_args__ = (
        Index("ix_transcription_meeting_start", "meeting_id", "start_time"),
        Index("ix_transcription_meeting_segment", "meeting_id", "segment_id",
              unique=True, postgresql_where=segment_id.isnot(None)),
    )


class MeetingSession(Base):
    __tablename__ = "meeting_sessions"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    session_uid = Column(String, nullable=False, index=True)
    session_start_time = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    meeting = relationship("Meeting", back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("meeting_id", "session_uid", name="_meeting_session_uc"),
    )
