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
    Column, String, Text, Integer, BigInteger, DateTime, Float, Boolean,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


# --------------------------------------------------------------------------- #
# identity tables (parent: libs/admin-models/admin_models/models.py)
# --------------------------------------------------------------------------- #
#: The org every install has (MIGRATION-0008). One org per install for now: every existing and new
#: account belongs to it, and its policy source is the install's `_global`.
DEFAULT_ORG_ID = "default"
#: An individual account's org of one is ``p-<user id>`` (kind ``personal``).
PERSONAL_ORG_PREFIX = "p-"


class Org(Base):
    """The org as a first-class object (MIGRATION-0008, architecture decision A1).

    Identity is its one writer. One row per install for now (`DEFAULT_ORG_ID`, created by the
    convergence); the columns are what later per-org policy, CRM and billing hang on, so a second org
    is a data change rather than a refactor:

    - ``policy_source`` names the policy tier agent-api resolves for this org: ``_global`` (the
      install's company layer) for the default org; later ``_orgs/<id>``.
    - ``crm_org_id`` ties the org to its CRM organisation (crm-api's ``CRM_ORG_ID``, its ``org_id``
      key). NULL means "not bound yet"; the convergence stamps it from ``CRM_ORG_ID`` when admin-api
      is given it.
    - ``billing_account`` is where the org's billing account will hang. A field only: nothing reads
      it yet."""
    __tablename__ = "orgs"

    id = Column(String(64), primary_key=True)
    name = Column(String(200), nullable=False)
    policy_source = Column(String(255), nullable=False, server_default=text("'_global'"),
                           default="_global")
    #: ``org`` (a company) or ``personal`` (an individual account's org of one, ``p-<user id>``: the
    #: boundary of their personal CRM). Every org a migration creates is ``org``.
    kind = Column(String(16), nullable=False, server_default=text("'org'"), default="org")
    crm_org_id = Column(String(255), nullable=True)
    billing_account = Column(String(255), nullable=True)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})
    created_at = Column(DateTime, server_default=func.now(), default=func.now())


class OrgMember(Base):
    """An EXPLICIT membership of an account in an org, with its role (MIGRATION-0008).

    An account's home org is ``users.org_id``, and it is a member there without a row here: the
    role is then derived (``admin`` for the instance admin, else ``member``; `app/orgs.py`). So the
    migration writes no row per user. A row here is a role override in the home org, or (later) a
    membership in a second org."""
    __tablename__ = "org_members"

    org_id = Column(String(64), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
                     index=True)
    role = Column(String(32), nullable=False, server_default=text("'member'"), default="member")
    created_at = Column(DateTime, server_default=func.now(), default=func.now())


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
    # MIGRATION-0008 — the account's home org. A constant server default, so on Postgres >= 11 the
    # additive ADD COLUMN is a catalog change (no table rewrite, no per-row UPDATE): every existing
    # row reads the default org. Deliberately no FK and no index yet: both would scan or lock
    # `users` in-band; they are out-of-band steps for when a second org exists (the MIGRATION doc).
    org_id = Column(String(64), nullable=False, server_default=text("'default'"),
                    default=DEFAULT_ORG_ID)

    api_tokens = relationship("APIToken", back_populates="user")

    __table_args__ = (
        # MIGRATION-0007 — every email lookup in this service folds case
        # (`func.lower(User.email) == …`, `create_user` + `GET /admin/users/email/{email}`), and the
        # plain `email` index above cannot serve that predicate: both lookups are sequential scans
        # on `users`, on the sign-in path.
        #
        # NON-UNIQUE, DELIBERATELY, AND THIS IS THE HONEST PART. The invariant we want is
        # one-address-one-account, which is a UNIQUE index on `lower(email)`. It cannot ship as one
        # here: the instances that need it are exactly the instances that already hold case-variant
        # duplicate rows (that is the defect), a UNIQUE build against those rows raises
        # UniqueViolation, and `_sync_indexes` FAILS CLOSED on a unique-index failure by design
        # (#1186) — so shipping it unique would turn "this instance has a few ghost accounts" into
        # "admin-api will not start". Trading a data defect for an outage is not a fix.
        #
        # What closes the hole instead, today: `create_user` stores new addresses folded, so a new
        # collision is caught by the `email` UNIQUE index that already exists, and both lookups
        # ORDER BY id so an instance holding duplicates resolves the same row every time. The
        # UNIQUE upgrade is an operator step AFTER reconciling the duplicates — the SQL to find
        # them, and the CONCURRENTLY build, are in schema/MIGRATION-0007-users-email-lower.md.
        Index("ix_users_email_lower", text("lower(email)")),
        # The public profile's opaque handle (app/public_profile.py): directory reads and crm-api's live
        # reads find a profile by it. NON-UNIQUE: a handle is random (16+ bytes), and a unique build
        # that ever met a collision would fail the boot (`_sync_indexes`, #1186). Being non-unique, it
        # is built CONCURRENTLY after the convergence commits, never under its lock on `users`.
        Index("ix_users_public_profile_handle", text("((data -> 'public_profile') ->> 'handle')")),
    )


class Principal(Base):
    """WHO A TOKEN ACTS AS when it is not a person (MIGRATION-0009, PRD 0.13.4 D2).

    A room's turn runs as its WORKSPACE, not as any member: a numeric principal of kind
    ``workspace``, so every consumer that coerces a subject to ``int`` keeps working while nothing
    of any person is reachable through it. The principal's id IS a ``users.id`` — a dedicated account
    row (email ``ws-<id>@principal.invalid``, ``data.principal_kind = "workspace"``, no API key, no
    sign-in, no bots) — so foreign keys and meters that key on an account need no second id space.

    - ``kind``: ``person`` (an account acting as itself; implicit, no row is written for one) or
      ``workspace``.
    - ``workspace_id``: the workspace a ``workspace`` principal is the agent of; unique among them.
    - ``bills_to``: the account its spend debits (the workspace owner's pool). identity answers it
      beside the principal; it never widens what the principal reaches.
    - ``status``: ``active`` or ``disabled``. identity refuses a disabled principal's tokens.

    There is no persons table: the human is the CRM person record (crm-api, ADR-0047 d3)."""
    __tablename__ = "principals"

    id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind = Column(String(16), nullable=False, server_default=text("'workspace'"), default="workspace")
    workspace_id = Column(String(255), nullable=True)
    bills_to = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    status = Column(String(16), nullable=False, server_default=text("'active'"), default="active")
    created_at = Column(DateTime, server_default=func.now(), default=func.now())

    __table_args__ = (
        Index("ux_principals_workspace", "workspace_id", unique=True),
    )


# --------------------------------------------------------------------------- #
# the workspace registry (MIGRATION-0009, PRD 0.13.4 D5): membership leaves git
# --------------------------------------------------------------------------- #
class Workspace(Base):
    """A workspace as a governed object: who owns it, whether it is open, what it is connected to.

    Identity (admin-api) is the ONE writer; agent-api reaches it over the internal tier
    (``app/workspace_registry.py``). Git keeps a workspace's content only. ``id`` is the key every
    membership and invite names (``workspace:<id>``) — today agent-api's workspace slug, which is
    also stored in ``slug``. ``connections`` is the list of open workspaces this one's room agent reads
    (``[{"workspace", "added_by", "added"}]``), kept on the row rather than in a fifth table."""
    __tablename__ = "workspaces"

    id = Column(String(128), primary_key=True)
    slug = Column(String(128), nullable=False, unique=True)
    org_id = Column(String(64), nullable=False, server_default=text("'default'"), default=DEFAULT_ORG_ID)
    owner = Column(String(128), nullable=True)
    visibility = Column(String(16), nullable=False, server_default=text("'closed'"), default="closed")
    kind = Column(String(16), nullable=False, server_default=text("'normal'"), default="normal")
    connections = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=lambda: [])
    archived_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), default=func.now())


class Membership(Base):
    """A principal's role on a resource: ``workspace:<id>``, or ``room:<id>`` for a CLOSED room (an
    open room's membership is derived from its workspace's). ``principal_id`` is an account id (the
    person is their CRM record; there is no persons table) or, later, a ``ws:<id>`` principal.
    ``data`` holds the roster's display extras (``email``, ``discoverable``)."""
    __tablename__ = "memberships"

    principal_id = Column(String(128), primary_key=True)
    resource = Column(String(160), primary_key=True, index=True)
    role = Column(String(16), nullable=False)
    added_by = Column(String(128), nullable=True)
    added_at = Column(String(40), nullable=True)
    position = Column(Integer, nullable=False, server_default="0", default=0)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})


class Invite(Base):
    """An invite to a resource: only the sha256 of its token is kept. ``role`` never outranks
    ``created_by``'s role (checked by the minting service, and again at redeem)."""
    __tablename__ = "invites"

    id = Column(String(32), primary_key=True)
    resource = Column(String(160), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    role = Column(String(16), nullable=False)
    mode = Column(String(16), nullable=False, server_default=text("'open'"), default="open")
    max_uses = Column(Integer, nullable=False, server_default="1", default=1)
    uses = Column(Integer, nullable=False, server_default="0", default=0)
    expires_at = Column(BigInteger, nullable=False)     # epoch seconds: past 2038, so 64-bit
    created_by = Column(String(128), nullable=True)
    created_at = Column(String(40), nullable=True)
    revoked = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})


class PlatformSetting(Base):
    """Deployment-wide runtime config, one JSONB value per key (`models`, `transcription`).
    The DB layer between per-user prefs (users.data) and the process env: services resolve
    user > platform_settings > env. Written only over the internal tier (the terminal's
    admin-gated settings editor fronts it); read over the same edge by agent-api/meeting-api."""
    __tablename__ = "platform_settings"

    key = Column(String(64), primary_key=True)
    value = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=lambda: {})
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PersonPhoto(Base):
    """A person's photo, one row per source (Vexa-ai/vexa#1783, UI 15g): ``upload`` (the person's
    own), ``provider`` (their sign-in provider's picture) or ``gravatar``. The first present in that
    order is shown (`app/person_photos.py`). Identity is the one writer. The BYTES are not here: they
    live in the install's object store under ``person-photos/<user id>/`` (`app/photo_storage.py`),
    re-encoded so no metadata survives; the row keeps the key, the SHA-256 and the type. A
    ``gravatar`` row with no key is a remembered miss. Kept out of ``users.data`` and out of the
    database's bytes on purpose: every token check reads this database."""
    __tablename__ = "person_photos"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source = Column(String(16), primary_key=True)
    object_key = Column(String(255), nullable=False, server_default="")
    content_type = Column(String(32), nullable=False, server_default="")
    sha256 = Column(String(64), nullable=False, server_default="")
    origin = Column(String(64), nullable=False, server_default="")
    provider = Column(String(16), nullable=False, server_default="")
    checked_at = Column(Float, nullable=False, server_default="0")
    updated_at = Column(Float, nullable=False, server_default="0")


class APIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    # MIGRATION-0013: a key is held as its SHA-256 (`key_store.py`). `token` is the plaintext column
    # rows had before; the convergence hashes and clears it, so it is NULL on every converged row.
    token = Column(String(255), unique=True, index=True, nullable=True)
    token_hash = Column(String(64), unique=True, index=True, nullable=True)
    token_hint = Column(String(32), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scopes = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    name = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    # MIGRATION-0010 (D13): the credential class. `key` — every API key, self-serve or admin-minted;
    # `session` — the terminal's signed-in browser, minted only by `POST /internal/sessions`.
    kind = Column(String(16), nullable=False, server_default=text("'key'"))
    # MIGRATION-0016: a TRANSIT key (ADR-0051) names the one registered service it opens
    # (`transit_targets.name`) and what it may do there. NULL on every other key. A transit key
    # reaches no gateway route; its target asks identity (`POST /transit/validate`).
    audience = Column(String(64), nullable=True, index=True)
    actions = Column(ARRAY(Text), nullable=True)

    user = relationship("User", back_populates="api_tokens")


class TransitTarget(Base):
    """A service that accepts transit keys (ADR-0051, MIGRATION-0016). Registered by an operator
    (`PUT /admin/transit-targets/{name}`), never named in code. ``secret`` is the target's own
    credential for asking identity, sealed at rest (`at_rest.py`); it also keys the HMAC on
    identity's answers, so the target knows an answer came from identity.
    ``legacy_prefixes`` reads keys minted before transit keys existed as transit keys on this
    target: a key whose ``vxa_<scope>_`` prefix names one of them (``""`` = a key with no prefix)."""
    __tablename__ = "transit_targets"

    name = Column(String(64), primary_key=True)
    actions = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    legacy_prefixes = Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    description = Column(Text, nullable=False, server_default="")
    secret = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    secret_rotated_at = Column(DateTime, nullable=True)


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
        # A missing one is built CONCURRENTLY after the convergence commits (`_sync_indexes`), so
        # it never locks `meetings` under live traffic.
        Index("ix_meeting_user_created_at", "user_id", "created_at"),
        Index("ix_meeting_transcript_viewers_gin",
              text("(data -> 'transcript_viewers') jsonb_path_ops"), postgresql_using="gin"),
        Index("ix_meeting_workspace_created_at", text("(data ->> 'workspace_id')"), "created_at"),
        # #1222 (mirror of meeting-api's sessions/models.py): the list_view sort is
        # (non-terminal pin, MEETING EVENT time, id), so the owner/workspace union branches need
        # THAT key index-walkable, not created_at. `meeting_event_time()` is the IMMUTABLE wrapper
        # for COALESCE(data.scheduled_at, start_time, created_at), created by _sync_functions
        # (sync.py) BEFORE create_all/_sync_indexes — these expressions must stay verbatim-equal
        # to the ORDER BY in the collector's list_meetings or the planner won't substitute them.
        # ⚠ PROD ROLLOUT: create the function + both indexes CONCURRENTLY out-of-band BEFORE this
        # change deploys — see schema/MIGRATION-0005-meeting-event-order.md.
        Index("ix_meeting_user_event_order", "user_id",
              text("(status IN ('active', 'awaiting_admission', 'joining', 'requested', "
                   "'scheduled', 'stopping'))"),
              text("meeting_event_time(data, start_time, created_at)"),
              "id"),
        Index("ix_meeting_workspace_event_order", text("(data ->> 'workspace_id')"),
              text("(status IN ('active', 'awaiting_admission', 'joining', 'requested', "
                   "'scheduled', 'stopping'))"),
              text("meeting_event_time(data, start_time, created_at)"),
              "id"),
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
    created_at = Column(DateTime, default=datetime.utcnow)
    session_uid = Column(String, nullable=True, index=True)
    segment_id = Column(String, nullable=True)
    # transcript.v1 `Source` — how the row was attributed; `chat` marks an in-meeting chat line
    # (speaker = sender), which GET /bots/{platform}/{native}/chat reads back. NULL on speech rows
    # from producers that never stamped one.
    source = Column(String(32), nullable=True)

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
