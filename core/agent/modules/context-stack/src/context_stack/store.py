"""The repository over the six tables. Persistence only — it decides nothing.

Every policy question (who may read, where a write lands, who may accept) is answered in
``access.py``, ``router.py`` and ``secrets.py``. Keeping the store dumb is what makes those
answers testable in one place instead of being re-derived beside every query.

The store never commits. An operation that writes twice — accepting a proposal appends a revision
*and* marks the proposal decided — must be one transaction, and only the caller knows where the
transaction ends. Operations in this package commit at their own boundary; a service that composes
them into a larger unit of work keeps its own.

ORM rows do not leave the package. Every method returns a frozen value object, so a caller cannot
lazy-load its way to a column the surface deliberately withholds — the mechanism that makes "no
endpoint returns secret material" a property of the types rather than of every call site. The
secret accessors at the bottom are the single exception and stay inside the package: ``secrets.py``
turns a row into metadata, ``material.py`` is the one reader of the material itself, and nothing
else imports them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .layers import Policy, Role


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class WorkspaceRef:
    """A workspace's identity and policy. Never its context, never its secrets."""

    id: str
    name: str
    address: str
    policy: Policy
    owner_subject: str


@dataclass(frozen=True)
class MemberRef:
    subject: str
    role: Role
    email: str | None


@dataclass(frozen=True)
class RevisionRef:
    id: int
    workspace_id: str
    path: str
    revision: int
    body: str
    deleted: bool
    author_subject: str
    source_kind: str
    source_ref: str | None
    from_proposal_id: int | None


@dataclass(frozen=True)
class ProposalRef:
    id: int
    workspace_id: str
    path: str
    body: str
    proposer_subject: str
    source_kind: str
    source_ref: str | None
    state: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


@dataclass(frozen=True)
class PointerRef:
    subject: str
    slot: Policy
    workspace_id: str
    position: int


def _workspace(row: models.Workspace) -> WorkspaceRef:
    return WorkspaceRef(
        id=row.id,
        name=row.name,
        address=row.address,
        policy=Policy(row.policy),
        owner_subject=row.owner_subject,
    )


def _revision(row: models.ContextRevision) -> RevisionRef:
    return RevisionRef(
        id=row.id,
        workspace_id=row.workspace_id,
        path=row.path,
        revision=row.revision,
        body=row.body,
        deleted=bool(row.deleted),
        author_subject=row.author_subject,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        from_proposal_id=row.from_proposal_id,
    )


def _proposal(row: models.Proposal) -> ProposalRef:
    return ProposalRef(
        id=row.id,
        workspace_id=row.workspace_id,
        path=row.path,
        body=row.body,
        proposer_subject=row.proposer_subject,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        state=row.state,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


class ContextStackStore:
    """Async repository. One instance wraps one :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    # ── workspaces ────────────────────────────────────────────────────────────────────────────

    async def insert_workspace(
        self,
        *,
        id: str,
        name: str,
        address: str,
        policy: Policy,
        owner_subject: str,
    ) -> WorkspaceRef:
        # Stamped Python-side rather than by the server default: creation order is what the
        # pinned derivation orders by, and Postgres and SQLite disagree about how much of a
        # timestamp they keep. A tie in the store would become an arbitrary personal workspace
        # in someone's stack.
        row = models.Workspace(
            id=id,
            name=name,
            address=address,
            policy=policy.value,
            owner_subject=owner_subject,
            created_at=_utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return _workspace(row)

    async def get_workspace(self, workspace_id: str) -> WorkspaceRef | None:
        row = await self.session.get(models.Workspace, workspace_id)
        return _workspace(row) if row is not None else None

    async def get_workspace_by_address(self, address: str) -> WorkspaceRef | None:
        stmt = select(models.Workspace).where(models.Workspace.address == address)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _workspace(row) if row is not None else None

    async def owned_workspace(self, subject: str, policy: Policy) -> WorkspaceRef | None:
        """The one workspace at a singleton layer this subject owns, if it exists."""
        stmt = (
            select(models.Workspace)
            .where(
                models.Workspace.owner_subject == subject,
                models.Workspace.policy == policy.value,
            )
            .order_by(models.Workspace.created_at, models.Workspace.id)
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return _workspace(row) if row is not None else None

    async def global_workspace(self) -> WorkspaceRef | None:
        """The product's global layer. One exists; the oldest wins if a deployment holds several,
        so resolution is deterministic rather than dependent on row order."""
        stmt = (
            select(models.Workspace)
            .where(models.Workspace.policy == Policy.GLOBAL.value)
            .order_by(models.Workspace.created_at, models.Workspace.id)
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return _workspace(row) if row is not None else None

    async def workspaces_by_id(self, ids: Sequence[str]) -> dict[str, WorkspaceRef]:
        if not ids:
            return {}
        stmt = select(models.Workspace).where(models.Workspace.id.in_(list(ids)))
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.id: _workspace(row) for row in rows}

    # ── membership ────────────────────────────────────────────────────────────────────────────

    async def insert_membership(
        self,
        *,
        workspace_id: str,
        subject: str,
        role: Role,
        added_by: str,
        email: str | None = None,
    ) -> MemberRef:
        row = models.Membership(
            workspace_id=workspace_id,
            subject=subject,
            role=role.value,
            added_by=added_by,
            email=email,
            added_at=_utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return MemberRef(subject=subject, role=role, email=email)

    async def delete_membership(self, *, workspace_id: str, subject: str) -> bool:
        stmt = delete(models.Membership).where(
            models.Membership.workspace_id == workspace_id,
            models.Membership.subject == subject,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return bool(result.rowcount)

    async def role_of(self, *, workspace_id: str, subject: str) -> Role | None:
        """The actor's role in the workspace, or ``None`` when they are not a member."""
        stmt = select(models.Membership.role).where(
            models.Membership.workspace_id == workspace_id,
            models.Membership.subject == subject,
        )
        value = (await self.session.execute(stmt)).scalar_one_or_none()
        return Role(value) if value is not None else None

    async def members(self, workspace_id: str) -> tuple[MemberRef, ...]:
        stmt = (
            select(models.Membership)
            .where(models.Membership.workspace_id == workspace_id)
            .order_by(models.Membership.id)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return tuple(
            MemberRef(subject=r.subject, role=Role(r.role), email=r.email) for r in rows
        )

    async def member_count(self, workspace_id: str) -> int:
        stmt = select(func.count()).select_from(models.Membership).where(
            models.Membership.workspace_id == workspace_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def memberships_of(
        self, subject: str, *, policy: Policy | None = None
    ) -> tuple[WorkspaceRef, ...]:
        """Every workspace this subject is a member of, oldest first, name-tiebroken."""
        stmt = (
            select(models.Workspace)
            .join(models.Membership, models.Membership.workspace_id == models.Workspace.id)
            .where(models.Membership.subject == subject)
            .order_by(models.Workspace.created_at, models.Workspace.id)
        )
        if policy is not None:
            stmt = stmt.where(models.Workspace.policy == policy.value)
        rows = (await self.session.execute(stmt)).scalars().all()
        return tuple(_workspace(r) for r in rows)

    # ── stack pointers ────────────────────────────────────────────────────────────────────────

    async def pointers(self, subject: str) -> tuple[PointerRef, ...]:
        stmt = (
            select(models.StackPointer)
            .where(models.StackPointer.subject == subject)
            .order_by(models.StackPointer.position, models.StackPointer.id)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return tuple(
            PointerRef(
                subject=r.subject,
                slot=Policy(r.slot),
                workspace_id=r.workspace_id,
                position=r.position,
            )
            for r in rows
        )

    async def set_pointer(
        self, *, subject: str, slot: Policy, workspace_id: str, position: int = 0
    ) -> PointerRef:
        """Point a slot at a workspace.

        A singleton slot holds one pointer: setting it replaces whatever it held, which is what
        re-pointing a user's personal workspace is. The group slot accumulates.
        """
        from .layers import SINGLETON_LAYERS

        if slot in SINGLETON_LAYERS:
            await self.clear_pointers(subject=subject, slot=slot)
        row = models.StackPointer(
            subject=subject, slot=slot.value, workspace_id=workspace_id, position=position
        )
        self.session.add(row)
        await self.session.flush()
        return PointerRef(
            subject=subject, slot=slot, workspace_id=workspace_id, position=position
        )

    async def clear_pointers(self, *, subject: str, slot: Policy) -> int:
        stmt = delete(models.StackPointer).where(
            models.StackPointer.subject == subject,
            models.StackPointer.slot == slot.value,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)

    # ── context revisions ─────────────────────────────────────────────────────────────────────

    async def append_revision(
        self,
        *,
        workspace_id: str,
        path: str,
        body: str,
        author_subject: str,
        source_kind: str,
        source_ref: str | None = None,
        deleted: bool = False,
        from_proposal_id: int | None = None,
    ) -> RevisionRef:
        stmt = select(func.max(models.ContextRevision.revision)).where(
            models.ContextRevision.workspace_id == workspace_id,
            models.ContextRevision.path == path,
        )
        highest = (await self.session.execute(stmt)).scalar_one_or_none()
        row = models.ContextRevision(
            workspace_id=workspace_id,
            path=path,
            revision=(highest or 0) + 1,
            body=body,
            deleted=deleted,
            author_subject=author_subject,
            source_kind=source_kind,
            source_ref=source_ref,
            from_proposal_id=from_proposal_id,
            created_at=_utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return _revision(row)

    async def current_revision(self, *, workspace_id: str, path: str) -> RevisionRef | None:
        """The highest revision of one document, tombstone included (callers check ``deleted``)."""
        stmt = (
            select(models.ContextRevision)
            .where(
                models.ContextRevision.workspace_id == workspace_id,
                models.ContextRevision.path == path,
            )
            .order_by(models.ContextRevision.revision.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return _revision(row) if row is not None else None

    async def revisions(self, *, workspace_id: str, path: str) -> tuple[RevisionRef, ...]:
        stmt = (
            select(models.ContextRevision)
            .where(
                models.ContextRevision.workspace_id == workspace_id,
                models.ContextRevision.path == path,
            )
            .order_by(models.ContextRevision.revision)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return tuple(_revision(r) for r in rows)

    async def documents(self, workspace_id: str) -> tuple[str, ...]:
        stmt = (
            select(models.ContextRevision.path)
            .where(models.ContextRevision.workspace_id == workspace_id)
            .distinct()
            .order_by(models.ContextRevision.path)
        )
        return tuple((await self.session.execute(stmt)).scalars().all())

    # ── proposals ─────────────────────────────────────────────────────────────────────────────

    async def insert_proposal(
        self,
        *,
        workspace_id: str,
        path: str,
        body: str,
        proposer_subject: str,
        source_kind: str,
        source_ref: str | None = None,
    ) -> ProposalRef:
        row = models.Proposal(
            workspace_id=workspace_id,
            path=path,
            body=body,
            proposer_subject=proposer_subject,
            source_kind=source_kind,
            source_ref=source_ref,
            state="pending",
            created_at=_utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return _proposal(row)

    async def get_proposal(self, proposal_id: int) -> ProposalRef | None:
        row = await self.session.get(models.Proposal, proposal_id)
        return _proposal(row) if row is not None else None

    async def proposals(
        self, *, workspace_id: str, state: str | None = None
    ) -> tuple[ProposalRef, ...]:
        stmt = select(models.Proposal).where(models.Proposal.workspace_id == workspace_id)
        if state is not None:
            stmt = stmt.where(models.Proposal.state == state)
        stmt = stmt.order_by(models.Proposal.id)
        rows = (await self.session.execute(stmt)).scalars().all()
        return tuple(_proposal(r) for r in rows)

    async def record_decision(
        self, *, proposal_id: int, state: str, decided_by: str, note: str | None
    ) -> ProposalRef:
        """Stamp a human's answer onto a proposal.

        ``decided_by`` has no default and is not optional anywhere up the call chain, and the
        table's CHECK constraint refuses a decided row without it.
        """
        row = await self.session.get(models.Proposal, proposal_id)
        if row is None:  # pragma: no cover - callers load the proposal first
            raise LookupError(proposal_id)
        row.state = state
        row.decided_by = decided_by
        row.decided_at = _utcnow()
        row.decision_note = note
        await self.session.flush()
        return _proposal(row)

    # ── secrets (rows in, metadata out — see secrets.py for the surface) ──────────────────────

    async def upsert_secret(
        self, *, workspace_id: str, name: str, material: str, set_by: str
    ) -> models.WorkspaceSecret:
        stmt = select(models.WorkspaceSecret).where(
            models.WorkspaceSecret.workspace_id == workspace_id,
            models.WorkspaceSecret.name == name,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        now = _utcnow()
        if row is None:
            row = models.WorkspaceSecret(
                workspace_id=workspace_id,
                name=name,
                material=material,
                last4=material[-4:],
                version=1,
                set_by=set_by,
                set_at=now,
            )
            self.session.add(row)
        else:
            row.material = material
            row.last4 = material[-4:]
            row.version = row.version + 1
            row.set_by = set_by
            row.rotated_at = now
        await self.session.flush()
        return row

    async def secret_row(self, *, workspace_id: str, name: str) -> models.WorkspaceSecret | None:
        stmt = select(models.WorkspaceSecret).where(
            models.WorkspaceSecret.workspace_id == workspace_id,
            models.WorkspaceSecret.name == name,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def secret_rows(self, workspace_id: str) -> tuple[models.WorkspaceSecret, ...]:
        stmt = (
            select(models.WorkspaceSecret)
            .where(models.WorkspaceSecret.workspace_id == workspace_id)
            .order_by(models.WorkspaceSecret.name)
        )
        return tuple((await self.session.execute(stmt)).scalars().all())

    async def delete_secret_row(self, *, workspace_id: str, name: str) -> bool:
        stmt = delete(models.WorkspaceSecret).where(
            models.WorkspaceSecret.workspace_id == workspace_id,
            models.WorkspaceSecret.name == name,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return bool(result.rowcount)
