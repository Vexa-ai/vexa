"""The HTTP surface: an ``APIRouter`` a service mounts. It is not an app and it wires no database.

The routes exist so the two invariants this module is built around can be checked mechanically
rather than argued:

* **No route returns secret material.** The secret routes' response model is
  :class:`SecretMetadataOut`, which has no material field, and this module does not import
  ``context_stack.material`` — the only place material is readable. Both are asserted by
  enumerating the routes in ``tests/test_api_surface.py``, which also sets a known key and calls
  every readable route to confirm the value comes back from none of them.
* **No route accepts a proposal automatically.** There is one accept route, it is a POST, and it
  takes its actor from the gateway-verified caller identity. There is no accept-all, no
  auto-accept flag, and no route that decides a proposal as a side effect of anything else.

The actor is read from ``X-User-Email`` — the header the gateway already stamps, and the same
identifier group setup uses, since a group is a set of member emails.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from . import secrets as secrets_surface
from . import triage, workspaces
from .errors import (
    AccessDenied,
    ContextStackError,
    InvalidWorkspace,
    NotFound,
    ProposalAlreadyDecided,
)
from .layers import Policy, Role
from .resolver import Mode, resolve_stack
from .router import ContextDelta, land_delta
from .store import ContextStackStore


# ── wire shapes ───────────────────────────────────────────────────────────────────────────────


class WorkspaceIn(BaseModel):
    workspace_id: str
    name: str
    address: str
    policy: Policy
    member_emails: list[str] = []


class WorkspaceOut(BaseModel):
    id: str
    name: str
    address: str
    policy: Policy
    owner_subject: str


class MemberIn(BaseModel):
    subject: str
    email: str | None = None


class MemberOut(BaseModel):
    subject: str
    role: Role
    email: str | None


class RemovedOut(BaseModel):
    removed: bool


class SlotOut(BaseModel):
    layer: Policy
    workspace_id: str
    name: str
    address: str
    write: str
    hidden: bool
    sharable: bool


class DenialOut(BaseModel):
    layer: Policy
    workspace_id: str | None
    reason: str


class StackOut(BaseModel):
    subject: str
    mode: Mode
    slots: list[SlotOut]
    denied: list[DenialOut]


class DeltaIn(BaseModel):
    workspace_id: str
    path: str
    body: str
    source_kind: str = "meeting"
    source_ref: str | None = None


class LandedOut(BaseModel):
    destination: str
    layer: Policy
    workspace_id: str
    reason: str
    revision_id: int | None = None
    proposal_id: int | None = None


class ProposalOut(BaseModel):
    id: int
    workspace_id: str
    path: str
    body: str
    proposer_subject: str
    state: str
    decided_by: str | None
    decision_note: str | None


class DecisionIn(BaseModel):
    note: str | None = None


class DocumentOut(BaseModel):
    workspace_id: str
    path: str
    revision: int
    body: str
    deleted: bool
    author_subject: str


class SecretIn(BaseModel):
    material: str


class SecretMetadataOut(BaseModel):
    """The whole read surface of a workspace secret. Adding a field here is the only way to leak
    one, which is the point: it would be a visible edit to a declared shape."""

    workspace_id: str
    name: str
    last4: str
    version: int
    set_by: str
    set_at: str
    rotated_at: str | None


# ── error translation ─────────────────────────────────────────────────────────────────────────

_STATUS = {
    AccessDenied: 403,
    NotFound: 404,
    InvalidWorkspace: 409,
    ProposalAlreadyDecided: 409,
}


@contextmanager
def _translating() -> Iterator[None]:
    """Turn a refusal into an HTTP status that keeps the machine-readable code."""
    try:
        yield
    except ContextStackError as exc:
        status = next((s for t, s in _STATUS.items() if isinstance(exc, t)), 400)
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


def _actor(x_user_email: str = Header(..., alias="X-User-Email")) -> str:
    """The caller, as the gateway verified them. Never taken from a body."""
    return x_user_email


def create_router(get_store: Callable[..., ContextStackStore]) -> APIRouter:
    """Build the router against a store dependency the mounting service provides."""
    router = APIRouter(tags=["context-stack"])

    @router.post("/workspaces", response_model=WorkspaceOut)
    async def create_workspace(
        body: WorkspaceIn,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> WorkspaceOut:
        with _translating():
            workspace = await workspaces.create_workspace(
                store,
                workspace_id=body.workspace_id,
                name=body.name,
                address=body.address,
                policy=body.policy,
                owner_subject=actor,
                owner_email=actor,
                member_emails=tuple(body.member_emails),
            )
        return WorkspaceOut(**vars(workspace))

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
    async def read_workspace(
        workspace_id: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> WorkspaceOut:
        with _translating():
            workspace = await _readable(store, workspace_id, actor)
        return WorkspaceOut(**vars(workspace))

    @router.post("/workspaces/{workspace_id}/members", response_model=MemberOut)
    async def add_member(
        workspace_id: str,
        body: MemberIn,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> MemberOut:
        with _translating():
            member = await workspaces.add_member(
                store,
                workspace_id=workspace_id,
                subject=body.subject,
                email=body.email or body.subject,
                actor=actor,
            )
        return MemberOut(subject=member.subject, role=member.role, email=member.email)

    @router.delete("/workspaces/{workspace_id}/members/{subject}", response_model=RemovedOut)
    async def remove_member(
        workspace_id: str,
        subject: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> RemovedOut:
        with _translating():
            removed = await workspaces.remove_member(
                store, workspace_id=workspace_id, subject=subject, actor=actor
            )
        return RemovedOut(removed=removed)

    @router.get("/stack", response_model=StackOut)
    async def read_stack(
        mode: Mode = Mode.PINNED,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> StackOut:
        """The caller's own stack. There is no subject parameter: a stack is composed for the
        person asking, so no caller can ask for someone else's composition."""
        with _translating():
            resolved = await resolve_stack(store, subject=actor, mode=mode)
        return StackOut(
            subject=resolved.subject,
            mode=resolved.mode,
            slots=[SlotOut(**s.to_contract()) for s in resolved.slots],
            denied=[DenialOut(**d.to_contract()) for d in resolved.denied],
        )

    @router.post("/context/deltas", response_model=LandedOut)
    async def land(
        body: DeltaIn,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> LandedOut:
        with _translating():
            landed = await land_delta(
                store,
                ContextDelta(
                    workspace_id=body.workspace_id,
                    path=body.path,
                    body=body.body,
                    author_subject=actor,
                    source_kind=body.source_kind,
                    source_ref=body.source_ref,
                ),
            )
        return LandedOut(
            **landed.routing.to_contract(),
            revision_id=landed.revision.id if landed.revision else None,
            proposal_id=landed.proposal.id if landed.proposal else None,
        )

    @router.get("/workspaces/{workspace_id}/context", response_model=DocumentOut)
    async def read_document(
        workspace_id: str,
        path: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> DocumentOut:
        with _translating():
            await _readable(store, workspace_id, actor)
            revision = await store.current_revision(workspace_id=workspace_id, path=path)
            if revision is None:
                raise NotFound(f"no document {path!r} in {workspace_id!r}")
        return DocumentOut(
            workspace_id=revision.workspace_id,
            path=revision.path,
            revision=revision.revision,
            body=revision.body,
            deleted=revision.deleted,
            author_subject=revision.author_subject,
        )

    @router.get("/workspaces/{workspace_id}/proposals", response_model=list[ProposalOut])
    async def read_proposals(
        workspace_id: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> list[ProposalOut]:
        with _translating():
            pending = await triage.pending_proposals(
                store, workspace_id=workspace_id, actor=actor
            )
        return [_proposal_out(p) for p in pending]

    @router.post("/proposals/{proposal_id}/accept", response_model=LandedOut)
    async def accept(
        proposal_id: int,
        body: DecisionIn | None = None,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> LandedOut:
        """The owner accepts. One proposal, one human, one call — there is no batch form."""
        with _translating():
            landed = await triage.accept_proposal(
                store,
                proposal_id=proposal_id,
                actor=actor,
                note=body.note if body else None,
            )
        return LandedOut(
            **landed.routing.to_contract(),
            revision_id=landed.revision.id if landed.revision else None,
            proposal_id=landed.proposal.id if landed.proposal else None,
        )

    @router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
    async def reject(
        proposal_id: int,
        body: DecisionIn | None = None,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> ProposalOut:
        with _translating():
            decided = await triage.reject_proposal(
                store,
                proposal_id=proposal_id,
                actor=actor,
                note=body.note if body else None,
            )
        return _proposal_out(decided)

    @router.put("/workspaces/{workspace_id}/secrets/{name}", response_model=SecretMetadataOut)
    async def set_secret(
        workspace_id: str,
        name: str,
        body: SecretIn,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> SecretMetadataOut:
        """Set or rotate. The response is metadata — the request is the only place a value ever
        appears on this surface."""
        with _translating():
            metadata = await secrets_surface.set_secret(
                store, workspace_id=workspace_id, name=name, material=body.material, actor=actor
            )
        return SecretMetadataOut(**metadata.to_contract())

    @router.get("/workspaces/{workspace_id}/secrets", response_model=list[SecretMetadataOut])
    async def list_secrets(
        workspace_id: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> list[SecretMetadataOut]:
        with _translating():
            rows = await secrets_surface.list_metadata(
                store, workspace_id=workspace_id, actor=actor
            )
        return [SecretMetadataOut(**m.to_contract()) for m in rows]

    @router.get("/workspaces/{workspace_id}/secrets/{name}", response_model=SecretMetadataOut)
    async def read_secret_metadata(
        workspace_id: str,
        name: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> SecretMetadataOut:
        with _translating():
            metadata = await secrets_surface.get_metadata(
                store, workspace_id=workspace_id, name=name, actor=actor
            )
        return SecretMetadataOut(**metadata.to_contract())

    @router.delete("/workspaces/{workspace_id}/secrets/{name}", response_model=RemovedOut)
    async def delete_secret(
        workspace_id: str,
        name: str,
        store: ContextStackStore = Depends(get_store),
        actor: str = Depends(_actor),
    ) -> RemovedOut:
        with _translating():
            removed = await secrets_surface.delete_secret(
                store, workspace_id=workspace_id, name=name, actor=actor
            )
        return RemovedOut(removed=removed)

    return router


def _proposal_out(proposal) -> ProposalOut:  # noqa: ANN001 - store.ProposalRef
    return ProposalOut(
        id=proposal.id,
        workspace_id=proposal.workspace_id,
        path=proposal.path,
        body=proposal.body,
        proposer_subject=proposal.proposer_subject,
        state=proposal.state,
        decided_by=proposal.decided_by,
        decision_note=proposal.decision_note,
    )


async def _readable(store: ContextStackStore, workspace_id: str, actor: str):
    """Load a workspace the actor is allowed to read, or refuse. The group-read boundary."""
    from .access import Action, decide

    workspace = await store.get_workspace(workspace_id)
    if workspace is None:
        raise NotFound(f"no workspace {workspace_id!r}")
    role = await store.role_of(workspace_id=workspace_id, subject=actor)
    verdict = decide(
        subject=actor,
        workspace_id=workspace_id,
        policy=workspace.policy,
        role=role,
        action=Action.READ,
    )
    if not verdict.allow:
        raise AccessDenied(f"{actor} may not read {workspace_id}", decision=verdict)
    return workspace
