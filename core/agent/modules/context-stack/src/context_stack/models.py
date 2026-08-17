"""The relational schema: workspaces, memberships, context revisions, proposals, pointers, secrets.

Six tables. Three of their shapes are decisions rather than transcription, and each is argued at
its table below: context is **append-only revisions**, stack slots are **unconstrained pointers**,
and a decided proposal is **required by a CHECK constraint to name a human**.

Style follows ``admin_api.schema.models`` — SQLAlchemy 2.0 with ``declarative_base()`` and
``Column(...)``, which is also the form ``scripts/schema_digest.py`` parses when a schema is
sealed. Columns stay on portable types (no ``JSONB``, no ``ARRAY``): the tests run this DDL on
SQLite and a service will run it on Postgres, and a schema that only exists in one dialect cannot
be tested where it is cheap to test.

These tables are **not sealed**. ``schema.seal.json`` freezes the schema that deployed services
create; no service mounts this module yet, so sealing now would freeze a claim that nothing is
making. When a service adopts it, add this file to ``MODEL_FILES`` in
``scripts/schema_digest.py`` and re-seal with ``pnpm seal:schema`` on a ``lane:schema`` review.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

_POLICIES = "'global', 'group', 'personal', 'user-system'"
_ROLES = "'owner', 'member'"
_STATES = "'pending', 'accepted', 'rejected'"


class Workspace(Base):
    """A workspace: an address, a name, a policy, an owner.

    The **address** is how meetings arrive — the bot has a mailbox and each workspace has its own
    address, invited like any attendee, so the invited address IS the group resolution. The
    **name** names the bot. The **policy** is the layer (see ``layers.py``).

    A personal workspace is a row here like any other, with ``policy='personal'``. It is the
    one-member case, not a second kind of thing.
    """

    __tablename__ = "workspaces"

    id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    address = Column(String(320), unique=True, index=True, nullable=False)
    policy = Column(String(16), nullable=False, index=True)
    owner_subject = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), default=func.now())

    __table_args__ = (CheckConstraint(f"policy IN ({_POLICIES})", name="ck_workspace_policy"),)


class Membership(Base):
    """Who is in a workspace, and as what.

    Group setup is a set of member emails and nothing more, so ``email`` is carried on the row
    rather than looked up from a directory this module does not have — the same choice
    ``policy/members.json`` already makes in the git workspace store.
    """

    __tablename__ = "workspace_memberships"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    subject = Column(String(255), nullable=False, index=True)
    email = Column(String(320), nullable=True)
    role = Column(String(16), nullable=False)
    added_by = Column(String(255), nullable=False)
    added_at = Column(DateTime, server_default=func.now(), default=func.now())

    __table_args__ = (
        UniqueConstraint("workspace_id", "subject", name="uq_membership_workspace_subject"),
        CheckConstraint(f"role IN ({_ROLES})", name="ck_membership_role"),
    )


class ContextRevision(Base):
    """One append-only revision of one context document. Current state = the highest revision.

    **Why append-only rather than mutable rows with a version column.** Triage is the reason. An
    owner accepting a proposal has to be able to see what the group's context said before and what
    it says after, and a rejected proposal has to leave no trace in context while leaving a full
    trace in the record. Both are free when a write is an append and impossible to reconstruct
    once an UPDATE has overwritten the previous body. It also matches what the knowledge store
    already is — the workspaces this module describes are git repos, where a write has always been
    an append — so a later projection of these rows onto that store preserves semantics instead of
    inventing them. The cost is growth, which is the open compaction problem; compaction is itself
    intended to be a readable artifact, and it can only read a history that was kept.

    Deletion is a tombstone revision (``deleted=True``), never a DELETE, for the same reason.
    """

    __tablename__ = "context_revisions"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    path = Column(String(512), nullable=False)
    revision = Column(Integer, nullable=False)
    body = Column(Text, nullable=False)
    deleted = Column(Boolean, nullable=False, default=False)
    author_subject = Column(String(255), nullable=False)
    source_kind = Column(String(32), nullable=False)
    source_ref = Column(String(255), nullable=True)
    # Set when this revision exists because an owner accepted a proposal. The reverse pointer
    # (proposal → revision) is deliberately absent: two FKs between the same pair of tables is a
    # cycle, and one direction answers both questions.
    from_proposal_id = Column(Integer, ForeignKey("context_proposals.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), default=func.now())

    __table_args__ = (
        UniqueConstraint("workspace_id", "path", "revision", name="uq_revision_ws_path_rev"),
        Index("ix_revision_ws_path", "workspace_id", "path"),
    )


class Proposal(Base):
    """A proposed context delta awaiting a human owner's answer.

    The write router puts every group-policy delta here. Nothing moves it out except an owner
    calling accept or reject.

    ``ck_proposal_decision_is_attributed`` is where "no machine ever writes acknowledgement" stops
    being a convention. A row is either pending with no decider, or decided and naming who decided
    and when — the database rejects any other state, so no code path, present or future, can mark
    a proposal accepted without putting a name on it.
    """

    __tablename__ = "context_proposals"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    path = Column(String(512), nullable=False)
    body = Column(Text, nullable=False)
    proposer_subject = Column(String(255), nullable=False, index=True)
    source_kind = Column(String(32), nullable=False)
    source_ref = Column(String(255), nullable=True)
    state = Column(String(16), nullable=False, default="pending", index=True)
    decided_by = Column(String(255), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), default=func.now())

    __table_args__ = (
        CheckConstraint(f"state IN ({_STATES})", name="ck_proposal_state"),
        CheckConstraint(
            "(state = 'pending' AND decided_by IS NULL AND decided_at IS NULL)"
            " OR (state <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_proposal_decision_is_attributed",
        ),
    )


class StackPointer(Base):
    """One slot of one user's composed stack, pointing at a workspace.

    **``workspace_id`` carries no foreign key, on purpose.** The stack slots are pointers, not
    foreign-key constraints: a user can be re-pointed at a different personal workspace, or
    compose several groups, and the terminal keeps free composition while the product ships a
    pinned default. A constraint here would turn re-pointing into a schema problem and would make
    a pointer at a workspace this deployment does not hold — a not-yet-provisioned one, a
    workspace on another store — an insert error instead of what it actually is: a resolvable
    condition. The resolver reports such a pointer as a ``dangling-pointer`` denial and continues.

    Rows are optional. A user with no rows resolves through the pinned product composition, which
    is the participant case and the common one.
    """

    __tablename__ = "stack_pointers"

    id = Column(Integer, primary_key=True)
    subject = Column(String(255), nullable=False, index=True)
    slot = Column(String(16), nullable=False)
    workspace_id = Column(String(64), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), default=func.now())

    __table_args__ = (
        UniqueConstraint("subject", "slot", "workspace_id", name="uq_pointer_subject_slot_ws"),
        CheckConstraint(f"slot IN ({_POLICIES})", name="ck_pointer_slot"),
    )


class WorkspaceSecret(Base):
    """A workspace-scoped secret. Set by the owner; no endpoint ever returns ``material``.

    In the pilot exactly one secret is user-supplied: the LLM key/endpoint (BYOT). ``last4`` and
    the surrounding metadata are what a read returns — the whole read surface is defined by
    :class:`context_stack.secrets.SecretMetadata`, which has no material field to populate.

    ``material`` is plaintext at rest, matching the level the saved GitHub token already sits at
    (access-controlled, not encrypted). Envelope encryption is deliberately not built here: it
    earns its keep when integration tokens arrive, and building it now would be machinery around
    a single key.
    """

    __tablename__ = "workspace_secrets"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(String(64), ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    material = Column(Text, nullable=False)
    last4 = Column(String(4), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    set_by = Column(String(255), nullable=False)
    set_at = Column(DateTime, server_default=func.now(), default=func.now())
    rotated_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_secret_workspace_name"),)
