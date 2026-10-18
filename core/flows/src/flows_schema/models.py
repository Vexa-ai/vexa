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


class MailSend(Base):
    """ONE OUTBOUND MAIL, AS THE AUDIT SEES IT (`flows_steps/outbound.py`) — every send attempt of
    every kind, sent or not: the kind, the template version, the recipient's DOMAIN (never the
    address), and what happened (`sent`, `off`, `admins-only`, `capped`, `suppressed`, `failed`).
    Also the count the per-kind daily caps read, and the Message-IDs the inbound sensor matches a
    reply against."""
    __tablename__ = "mail_send"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    at: Mapped[float] = mapped_column(Double, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    template_version: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_domain: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    test: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (Index("mail_send_kind_at", "kind", "at"),)


class MailTestSend(Base):
    """A TEST-SEND OF ONE TEMPLATE VERSION to the admin's own address. A kind is switched on only
    while its live template version has a row here (`mail_kind_set`): what the global account turns
    on is what it has seen arrive."""
    __tablename__ = "mail_test_send"
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    template_version: Mapped[str] = mapped_column(Text, primary_key=True)
    at: Mapped[float] = mapped_column(Double, nullable=False)
    by: Mapped[str] = mapped_column(Text, nullable=False)


class MailOutboxSent(Base):
    __tablename__ = "mail_outbox_sent"
    subject_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    session: Mapped[str] = mapped_column(Text, primary_key=True)
    hash: Mapped[str] = mapped_column(Text, primary_key=True)
    sent_at: Mapped[float] = mapped_column(Double, nullable=False)


class FlowVersion(Base):
    """ONE VERSION OF ONE FLOW (flows.v1 `FlowDefinition`, ADR-0046).

    Keyed (scope, name, version): `scope` is `instance` or `user:<uid>`, so two people and the
    instance may each own a flow of the same name. It defaults to `instance` so every row and every
    INSERT written before scope existed keeps its meaning; an existing table is migrated in place
    by `flows.migrations`. `cron` set makes the flow a SCHEDULE flow (five fields, UTC): the worker
    fires it, and `on_event` then holds the reserved `schedule.due`, which no producer may publish."""
    __tablename__ = "flow_version"
    scope: Mapped[str] = mapped_column(Text, primary_key=True, server_default="instance")
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    on_event: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str | None] = mapped_column(Text)
    steps: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (CheckConstraint("status IN ('draft','active','retired')", name="flow_status"),)


class FrictionOccurrence(Base):
    """ONE ROW PER FRICTION EDGE PER HOUR — the repeat counter behind `POST /friction`'s dedup.

    A reporter stuck in a loop files the same report again and again (a scheduled run refused on
    every tick filed one per tick). The first report of an edge is admitted as a
    `friction.reported` reaction exactly as before; every repeat with the same key inside the same
    UTC hour bumps `occurrences` and `last_seen` here instead of admitting a second reaction.

    `dedup_key` is a sha256 over (uid, tool, normalised reason, UTC hour bucket) — computed in
    `flows_integrations.friction_dedup`. `friction_id` is the first report's id, the row a repeat
    is folded into. ONE WRITER: flows-api's `POST /friction`. Readers: `flows_timeline`'s
    `friction_for_subject`, which folds the count onto the report it belongs to. The reaction's own
    `subject_refs` are the admitted fact and are never mutated to carry a count."""
    __tablename__ = "friction_occurrence"
    dedup_key: Mapped[str] = mapped_column(Text, primary_key=True)
    friction_id: Mapped[str] = mapped_column(Text, nullable=False)
    uid: Mapped[str] = mapped_column(Text, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen: Mapped[float] = mapped_column(Double, nullable=False)
    last_seen: Mapped[float] = mapped_column(Double, nullable=False)
    # 1 once the first report's reaction was admitted. Until then a missing reaction means "still
    # being admitted", not "deleted" — see `friction_dedup.record_occurrence`.
    admitted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ONE index, on the join column the read side uses (see MailTurn on why only one).
    __table_args__ = (Index("friction_occurrence_by_id", "friction_id"),)


class KeyedSchedule(Base):
    """ONE PENDING FIRE PER KEY (ADR-0050 decision 3, flows-sensors phase A): "a bot 1 min before
    every calendar meeting, following reschedules". A `relative` trigger keeps a row per
    (scope, flow, key) — the key is read off the event (`payload.event_id`). Each matching event
    UPSERTS it: a new `fire_at` and `version + 1`, so a reschedule moves it; an event that no longer
    passes the trigger's filter, or a `*.cancelled`, cancels it. The worker fires due rows into
    reactions keyed `at:<scope>/<flow>/<key>:<fire_at>`, so a slot fires at most once by the reaction
    table's constraint; a row moved after firing re-arms. ONE WRITER: flows (`flows.keyed`)."""
    __tablename__ = "keyed_schedule"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    flow: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    fire_at: Mapped[float] = mapped_column(Double, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    # whom the pending fire is for — the flow's owner, or for a company flow the person the event names
    # (uid, else organizer): the per-person cap counts this, so one busy company flow starves nobody
    person: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[str | None] = mapped_column(Text)
    fired_for: Mapped[float | None] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('armed','fired','cancelled','missed')", name="keyed_status"),
        Index("keyed_due", "fire_at"),
        Index("keyed_person", "scope", "person"),
    )


class Sensor(Base):
    """A SENSOR IS ITS OWNER'S OBJECT (ADR-0050 decision 6, founder 2026-10-14). `{kind, connection,
    filter, interval}`, owned by one person (`scope` = `user:<uid>`) or by the company (`instance`,
    the global account's), made from chat (`sensor_create`), TESTED before it runs (`sensor_test`
    returns the events it would emit and what it dropped, with no side effects; `tested_version` must
    equal `version` to enable), and CONNECTED to a chat (`sensor_connect`). It turns a provider's
    changes into typed events with no model call and never acts; the credential broker holds the
    token and makes the call, the sensor keeps only its CURSOR. A no-change tick writes nothing.
    `status`: draft (made, not yet enabled) · active · degraded (N failures in a row; `reason`) ·
    stopped. ONE WRITER: flows (flows-api for the person's edits, the sensor pool for ticks)."""
    __tablename__ = "sensor"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    connection_id: Mapped[str | None] = mapped_column(Text)
    filter: Mapped[str | None] = mapped_column(Text)
    interval_s: Mapped[float] = mapped_column(Double, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tested_version: Mapped[int | None] = mapped_column(Integer)
    tested_at: Mapped[float | None] = mapped_column(Double)
    cursor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="poll")
    next_due_at: Mapped[float] = mapped_column(Double, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str | None] = mapped_column(Text)
    last_tick_at: Mapped[float | None] = mapped_column(Double)
    last_change_at: Mapped[float | None] = mapped_column(Double)
    last_events: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[float | None] = mapped_column(Double)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('draft','active','degraded','stopped')", name="sensor_status"),
        Index("sensor_due", "next_due_at"),
    )


class Delivery(Base):
    """ONE NUDGE IN FLIGHT, per person and item (PRD 0.13.4 D15, S6) — the keyed schedule of the
    delivery gate. An item (a card waiting on the person, a mention, a missed call, a join request, a
    slip) is ARMED at its `next_check_at`; the worker wakes it at `wake_at`, asks crm whether it is
    still open and unseen, and walks it in-app → (idle) one email → the digest, stopping the moment
    it is seen or acted on (`outcome`). REFS ONLY: a card's title is fetched for the recipient at send
    time; `names` holds a non-card item's display names (who, room, workspace) — never a message.
    A re-arm (the card moved its check) moves `wake_at` and bumps `version`; a closed card cancels.
    ONE WRITER: flows (`flows_delivery.store`)."""
    __tablename__ = "delivery"
    person: Mapped[str] = mapped_column(Text, primary_key=True)
    ref: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    org: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workspace: Mapped[str] = mapped_column(Text, nullable=False, default="")
    room: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner: Mapped[str] = mapped_column(Text, nullable=False, default="")
    names: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    wake_at: Mapped[float] = mapped_column(Double, nullable=False)
    notice_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    armed_at: Mapped[float] = mapped_column(Double, nullable=False)
    notified_at: Mapped[float | None] = mapped_column(Double)
    emailed_at: Mapped[float | None] = mapped_column(Double)
    digested_at: Mapped[float | None] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (
        CheckConstraint("stage IN ('armed','notified','digest','done')", name="delivery_stage"),
        Index("delivery_due", "stage", "wake_at"),
        Index("delivery_person", "person", "stage"),
    )


class DeliverySend(Base):
    """ONE DELIVERY OUTSIDE THE INBOX (PRD 0.13.4 D32 — the attention budget's unit). One email for
    one item, or one digest for many, is one row; the in-app notice never is (acknowledging inside
    the Inbox costs no budget). `day` is the recipient's LOCAL day, so the cap resets at their
    midnight. Written `sending` BEFORE the transport is called, under a deterministic `id`
    (`<person>|<ref>|single` or `<person>|digest|<slot>`), and flipped to `sent` after: a crash in
    between can never send twice, which is the effect-receipt rule applied to mail. ONE WRITER: flows."""
    __tablename__ = "delivery_send"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    person: Mapped[str] = mapped_column(Text, nullable=False)
    day: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    refs: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[float] = mapped_column(Double, nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('sending','sent','failed')", name="delivery_send_status"),
        CheckConstraint("kind IN ('single','digest')", name="delivery_send_kind"),
        Index("delivery_send_day", "person", "day"),
    )
