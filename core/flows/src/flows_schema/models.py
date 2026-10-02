"""The schema SOURCE OF TRUTH — SQLAlchemy declarative models, house-style (meeting-api's
collector/sessions and admin-api define their tables exactly this way).

`schema.sql` is GENERATED from these models (scripts/gen_schema.py) so the stdlib-pure engine and
the sqlite test rig keep working without importing SQLAlchemy; a pytest gate fails if the file
drifts from the models. Hot statements (SKIP LOCKED claim, ON CONFLICT admission) stay textual in
the engine — same split as the collector.

This module is imported ONLY by tooling and the postgres composition path — never by src/flows/*
engine modules (the isolation gate enforces that)."""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Double, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Reaction(Base):
    __tablename__ = "reaction"
    reaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_refs: Mapped[str] = mapped_column(Text, nullable=False)
    flow: Mapped[str] = mapped_column(Text, nullable=False)
    flow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run_at: Mapped[float] = mapped_column(Double, nullable=False)
    blocked_deadline: Mapped[float | None] = mapped_column(Double)
    lease_until: Mapped[float | None] = mapped_column(Double)
    reason: Mapped[str | None] = mapped_column(Text)
    scratch: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('admitted','running','blocked','retrying',"
                        "'failed','cancelled','done')", name="reaction_status"),
        Index("reaction_due", "next_run_at",
              postgresql_where="status IN ('admitted','retrying')",
              sqlite_where="status IN ('admitted','retrying')"),
    )


class EffectReceipt(Base):
    __tablename__ = "effect_receipt"
    effect_key: Mapped[str] = mapped_column(Text, primary_key=True)
    reaction_id: Mapped[str] = mapped_column(Text, ForeignKey("reaction.reaction_id"), nullable=False)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[float] = mapped_column(Double, nullable=False)
    confirmed_at: Mapped[float | None] = mapped_column(Double)
    __table_args__ = (
        CheckConstraint("state IN ('reserved','confirmed','failed')", name="receipt_state"),
        Index("receipt_by_reaction", "reaction_id"),
    )


class Signal(Base):
    __tablename__ = "signal"
    signal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    reaction_id: Mapped[str] = mapped_column(Text, ForeignKey("reaction.reaction_id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    consumed_at: Mapped[float | None] = mapped_column(Double)
    __table_args__ = (CheckConstraint("kind IN ('resume','retry','cancel','wake')", name="signal_kind"),)


class MailThread(Base):
    __tablename__ = "mail_thread"
    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    subject_uid: Mapped[str] = mapped_column(Text, nullable=False)
    session: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)


class MailCursor(Base):
    """One row: where the inbox poller stopped.

    'uid' is the IMAP UID — monotonic, so one integer is a complete position. 'token' is for
    sources whose ids are not ordered at all (mailpit's are random base62): it holds an opaque
    source-native watermark, and the 'mail_seen' table holds the ids it cannot separate."""
    __tablename__ = "mail_cursor"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("id = 1", name="cursor_singleton"),)


class MailSeen(Base):
    """Ids already routed, per inbox source — the never-re-admit half of a non-monotonic cursor.

    A watermark alone cannot make a re-scan idempotent when the source orders by a timestamp that
    several messages can share, so the poller re-scans a small window behind the watermark and
    this table decides what inside it has already been answered. Rows older than the window are
    pruned on every commit; they can never be fetched again."""
    __tablename__ = "mail_seen"
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    ext_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created: Mapped[str] = mapped_column(Text, nullable=False)
    seen_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (Index("mail_seen_window", "source", "created"),)


class MailQuarantine(Base):
    """A mail the intake REFUSED to act on, and everything it knew.

    The row is the whole point (`flows_integrations/mail_policy`). A refusal that leaves no trace
    is indistinguishable from a poller that died, and for an invite it would also throw away the
    meeting facts the mail carried — so `facts` keeps them verbatim, and a known user can have the
    event re-admitted through `POST /events` once the sender is vouched for, without anyone going
    back to the mailbox.

    Keyed by the inbound message id, so the poller's lookback re-scan writes each refusal once.
    `replied_at` is the once-per-address semaphore for the fixed one-line template: a stranger who
    keeps writing gets rows, never a second reply, so two auto-responders cannot loop."""
    __tablename__ = "mail_quarantine"
    ext_id: Mapped[str] = mapped_column(Text, primary_key=True)
    from_addr: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[str | None] = mapped_column(Text)
    at: Mapped[float] = mapped_column(Double, nullable=False)
    replied_at: Mapped[float | None] = mapped_column(Double)
    __table_args__ = (Index("mail_quarantine_sender", "from_addr"),)


class MailTurn(Base):
    """One ADMITTED mail-triggered agent turn — the sliding window both rate limits count over.

    Admitted, not received: a quarantined stranger never enters this table, so a flood cannot
    exhaust the budget of the people who are allowed to use the mailbox. Keyed by the message id
    for the same reason as the quarantine row — a re-scan must not inflate a sender's count and
    lock them out of their own inbox."""
    __tablename__ = "mail_turn"
    ext_id: Mapped[str] = mapped_column(Text, primary_key=True)
    from_addr: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[float] = mapped_column(Double, nullable=False)
    # ONE index, on the window column. Both counts filter on `at` first and the window is an
    # hour, so a per-sender count is a scan of a handful of rows inside it; a second index would
    # buy nothing, and two indexes on one table do not come out of the generator in a stable
    # order — which the drift gate would then fail at random.
    __table_args__ = (Index("mail_turn_window", "at"),)


class MailOutboxSent(Base):
    __tablename__ = "mail_outbox_sent"
    subject_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    session: Mapped[str] = mapped_column(Text, primary_key=True)
    hash: Mapped[str] = mapped_column(Text, primary_key=True)
    sent_at: Mapped[float] = mapped_column(Double, nullable=False)


class FlowVersion(Base):
    __tablename__ = "flow_version"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    on_event: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (CheckConstraint("status IN ('draft','active','retired')", name="flow_status"),)
