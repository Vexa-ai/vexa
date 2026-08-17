"""Provisioning and membership — the operations that create a workspace and decide who is in it.

Workspace identity is an address and a name: the bot has a mailbox, each workspace has its own
address, and users invite that address to a meeting like any attendee, so the invited address IS
the group resolution — there is nothing to infer. The name names the bot.

Group setup is a set of member emails and nothing more. A personal workspace is the one-member
case of the same thing, not a second kind of object, which is why it is created by the same
function with a different policy.
"""

from __future__ import annotations

from .access import Action, AccessDecision, decide
from .errors import AccessDenied, InvalidWorkspace, NotFound
from .layers import Policy, Role
from .store import ContextStackStore, MemberRef, WorkspaceRef


async def create_workspace(
    store: ContextStackStore,
    *,
    workspace_id: str,
    name: str,
    address: str,
    policy: Policy,
    owner_subject: str,
    owner_email: str | None = None,
    member_emails: tuple[str, ...] = (),
) -> WorkspaceRef:
    """Create a workspace and seat its owner. ``member_emails`` is the whole of group setup.

    Members are seated by email because that is the only identifier the setup flow has. The
    subject and the email are the same string until a directory exists to tell them apart; the
    row keeps both columns so that stops being true without a migration.
    """
    if await store.get_workspace(workspace_id) is not None:
        raise InvalidWorkspace(f"workspace {workspace_id!r} already exists")
    if await store.get_workspace_by_address(address) is not None:
        raise InvalidWorkspace(f"address {address!r} is already bound to a workspace")

    workspace = await store.insert_workspace(
        id=workspace_id,
        name=name,
        address=address,
        policy=policy,
        owner_subject=owner_subject,
    )
    await store.insert_membership(
        workspace_id=workspace_id,
        subject=owner_subject,
        role=Role.OWNER,
        added_by=owner_subject,
        email=owner_email,
    )
    for email in member_emails:
        if email == owner_email or email == owner_subject:
            continue
        await add_member(
            store, workspace_id=workspace_id, subject=email, email=email, actor=owner_subject
        )
    await store.commit()
    return workspace


async def ensure_personal(
    store: ContextStackStore, *, subject: str, address: str, name: str | None = None
) -> WorkspaceRef:
    """The personal workspace every user has. Idempotent — provisioning runs more than once."""
    existing = await store.owned_workspace(subject, Policy.PERSONAL)
    if existing is not None:
        return existing
    return await create_workspace(
        store,
        workspace_id=f"personal-{subject}",
        name=name or f"{subject} (personal)",
        address=address,
        policy=Policy.PERSONAL,
        owner_subject=subject,
        owner_email=subject if "@" in subject else None,
    )


async def ensure_user_system(
    store: ContextStackStore, *, subject: str, address: str
) -> WorkspaceRef:
    """The per-user hidden layer: sessions and chat history. Never sharable, never a credential."""
    existing = await store.owned_workspace(subject, Policy.USER_SYSTEM)
    if existing is not None:
        return existing
    return await create_workspace(
        store,
        workspace_id=f"user-system-{subject}",
        name=f"{subject} (system)",
        address=address,
        policy=Policy.USER_SYSTEM,
        owner_subject=subject,
    )


async def add_member(
    store: ContextStackStore,
    *,
    workspace_id: str,
    subject: str,
    actor: str,
    email: str | None = None,
    role: Role = Role.MEMBER,
) -> MemberRef:
    """Seat a member. Owner-only, and only on a layer that is sharable at all."""
    workspace = await _require(store, workspace_id)
    _require_allowed(
        await _decide(store, workspace, actor=actor, action=Action.SHARE),
        f"{actor} may not add members to {workspace_id}",
    )
    if await store.role_of(workspace_id=workspace_id, subject=subject) is not None:
        return MemberRef(subject=subject, role=role, email=email)
    member = await store.insert_membership(
        workspace_id=workspace_id, subject=subject, role=role, added_by=actor, email=email
    )
    await store.commit()
    return member


async def remove_member(
    store: ContextStackStore, *, workspace_id: str, subject: str, actor: str
) -> bool:
    """Unseat a member. Owner-only. The owner's own row is not removable — a workspace without an
    owner has nobody to triage it, and the queue would silently stop being answerable."""
    workspace = await _require(store, workspace_id)
    _require_allowed(
        await _decide(store, workspace, actor=actor, action=Action.SHARE),
        f"{actor} may not remove members from {workspace_id}",
    )
    if subject == workspace.owner_subject:
        raise InvalidWorkspace("the owner's membership cannot be removed")
    removed = await store.delete_membership(workspace_id=workspace_id, subject=subject)
    await store.commit()
    return removed


async def _decide(
    store: ContextStackStore, workspace: WorkspaceRef, *, actor: str, action: Action
) -> AccessDecision:
    role = await store.role_of(workspace_id=workspace.id, subject=actor)
    return decide(
        subject=actor,
        workspace_id=workspace.id,
        policy=workspace.policy,
        role=role,
        action=action,
    )


async def _require(store: ContextStackStore, workspace_id: str) -> WorkspaceRef:
    workspace = await store.get_workspace(workspace_id)
    if workspace is None:
        raise NotFound(f"no workspace {workspace_id!r}")
    return workspace


def _require_allowed(decision: AccessDecision, message: str) -> None:
    if not decision.allow:
        raise AccessDenied(message, decision=decision)
