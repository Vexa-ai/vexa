"""Owner triage: accept or reject a proposed context delta. The human path.

This module is deliberately separate from ``router.py``. The router is the machine path — a
meeting produces deltas and they land — and its ceiling on the group layer is a ``pending`` row.
**No machine ever writes acknowledgement**, and that is the shape of the code rather than a note
about how to use it.

Four guards, at four different levels, and each would hold if the others were removed:

1. **The import direction.** This module depends on the router's types; the router therefore
   cannot import this one without a cycle Python refuses to load. A call chain from a meeting to
   an acceptance is not something a later edit can add here by accident.
2. ``actor`` is a required keyword with no default and no service principal. Every accept and
   every reject names a person.
3. The actor must hold the **owner** role — triage is one of the four things an owner does
   (config, membership, triage, secrets). A member may propose and may not decide.
4. ``ck_proposal_decision_is_attributed`` on ``context_proposals`` refuses any decided row that
   does not carry ``decided_by`` and ``decided_at``. A caller that bypassed this module entirely
   still cannot leave an unattributed acceptance in the table.

There is no accept-all, no auto-accept, and no policy knob that turns one on. The batching
question the roadmap raises — whether factual deltas can be auto-accepted while behavioural ones
queue — is a live design question, not a switch left in the code.
"""

from __future__ import annotations

from .access import Action, decide
from .errors import AccessDenied, NotFound, ProposalAlreadyDecided
from .router import Destination, Landed, Routing
from .store import ContextStackStore, ProposalRef, WorkspaceRef


async def pending_proposals(
    store: ContextStackStore, *, workspace_id: str, actor: str
) -> tuple[ProposalRef, ...]:
    """The owner's queue. Reading it is itself owner-gated — a queue is a view of what a group's
    members are trying to write, which is not a member's to read."""
    workspace = await _workspace(store, workspace_id)
    await _authorise(store, workspace, actor)
    return await store.proposals(workspace_id=workspace_id, state="pending")


async def accept_proposal(
    store: ContextStackStore, *, proposal_id: int, actor: str, note: str | None = None
) -> Landed:
    """The owner accepts: the proposed body becomes the group context's next revision.

    The revision keeps the *proposer* as its author and records ``from_proposal_id``, so the
    history says who wrote the knowledge and who let it in — two different people, both named.
    """
    proposal, workspace = await _pending(store, proposal_id)
    await _authorise(store, workspace, actor)

    revision = await store.append_revision(
        workspace_id=workspace.id,
        path=proposal.path,
        body=proposal.body,
        author_subject=proposal.proposer_subject,
        source_kind="triage",
        source_ref=proposal.source_ref,
        from_proposal_id=proposal.id,
    )
    decided = await store.record_decision(
        proposal_id=proposal.id, state="accepted", decided_by=actor, note=note
    )
    await store.commit()
    return Landed(
        routing=Routing(Destination.DIRECT, workspace.policy, workspace.id, "accepted-by-owner"),
        revision=revision,
        proposal=decided,
    )


async def reject_proposal(
    store: ContextStackStore, *, proposal_id: int, actor: str, note: str | None = None
) -> ProposalRef:
    """The owner rejects: nothing enters context, and the record keeps why and by whom."""
    proposal, workspace = await _pending(store, proposal_id)
    await _authorise(store, workspace, actor)
    decided = await store.record_decision(
        proposal_id=proposal.id, state="rejected", decided_by=actor, note=note
    )
    await store.commit()
    return decided


async def _pending(
    store: ContextStackStore, proposal_id: int
) -> tuple[ProposalRef, WorkspaceRef]:
    proposal = await store.get_proposal(proposal_id)
    if proposal is None:
        raise NotFound(f"no proposal {proposal_id}")
    if proposal.state != "pending":
        # A decided proposal is final. Re-deciding would overwrite one human's answer with
        # another's under the same id, and the record would show only the second.
        raise ProposalAlreadyDecided(
            f"proposal {proposal_id} was already {proposal.state} by {proposal.decided_by}"
        )
    return proposal, await _workspace(store, proposal.workspace_id)


async def _workspace(store: ContextStackStore, workspace_id: str) -> WorkspaceRef:
    workspace = await store.get_workspace(workspace_id)
    if workspace is None:
        raise NotFound(f"no workspace {workspace_id!r}")
    return workspace


async def _authorise(store: ContextStackStore, workspace: WorkspaceRef, actor: str) -> None:
    role = await store.role_of(workspace_id=workspace.id, subject=actor)
    verdict = decide(
        subject=actor,
        workspace_id=workspace.id,
        policy=workspace.policy,
        role=role,
        action=Action.TRIAGE,
    )
    if not verdict.allow:
        raise AccessDenied(f"{actor} may not triage {workspace.id}", decision=verdict)


__all__ = ["accept_proposal", "reject_proposal", "pending_proposals"]
