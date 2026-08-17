"""Workspace secrets — a write-only surface. Set, rotate, delete, and read metadata. Never a value.

In the pilot exactly one secret is user-supplied: the LLM key or endpoint the workspace owner
brings (BYOT). Self-hosted, it is a k8s Secret and never reaches this table at all; hosted, it is
a row here.

**The surface is write-only and that is a property of the return type, not a rule about callers.**
Every function in this module returns :class:`SecretMetadata`, which has no field the material
could be put in. There is nothing to remember not to include, and adding one would be a visible
change to a dataclass rather than an accident in a serializer. The single function that reads
material lives in ``material.py``, is never imported here or by the HTTP surface, and returns a
handle whose repr is redacted.

This is the exact inverse of a defect this codebase has already had: a settings read that returned
key material to its caller. A product that asks an owner for their provider key has to be the
other thing.

**Plaintext at rest**, deliberately. Envelope encryption is not built here: it earns its keep when
integration tokens arrive at group scope with grant rows and expiries, and building the machinery
now would wrap a single key in a key-management system nobody is yet asking for. Until then this
row sits at the level the saved GitHub token already sits at — access-controlled, not encrypted —
which is a property to state plainly rather than imply, and it means a full server compromise
reads it. Use a revocable, minimally-scoped key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .access import Action, decide
from .errors import AccessDenied, InvalidWorkspace, NotFound
from .store import ContextStackStore, WorkspaceRef

MIN_MATERIAL_LEN = 8
"""Below this, ``last4`` would be most of the secret. A short key is a mistyped key anyway."""


@dataclass(frozen=True)
class SecretMetadata:
    """Everything a read of a workspace secret returns. There is no material field, by design."""

    workspace_id: str
    name: str
    last4: str
    version: int
    set_by: str
    set_at: datetime
    rotated_at: datetime | None

    def to_contract(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "last4": self.last4,
            "version": self.version,
            "set_by": self.set_by,
            "set_at": self.set_at.isoformat(),
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
        }


def _metadata(row) -> SecretMetadata:  # noqa: ANN001 - models.WorkspaceSecret, kept out of the surface
    return SecretMetadata(
        workspace_id=row.workspace_id,
        name=row.name,
        last4=row.last4,
        version=row.version,
        set_by=row.set_by,
        set_at=row.set_at,
        rotated_at=row.rotated_at,
    )


async def set_secret(
    store: ContextStackStore, *, workspace_id: str, name: str, material: str, actor: str
) -> SecretMetadata:
    """Set or rotate a workspace secret. Owner only.

    Set and rotate are one operation because they are one operation: writing a new value over the
    old one, bumping the version, stamping who did it. A separate rotate endpoint that did the
    same thing would be a second path to keep correct.
    """
    workspace = await _require(store, workspace_id)
    await _authorise(store, workspace, actor)
    if len(material) < MIN_MATERIAL_LEN:
        raise InvalidWorkspace(f"secret material must be at least {MIN_MATERIAL_LEN} characters")
    row = await store.upsert_secret(
        workspace_id=workspace_id, name=name, material=material, set_by=actor
    )
    await store.commit()
    return _metadata(row)


async def delete_secret(
    store: ContextStackStore, *, workspace_id: str, name: str, actor: str
) -> bool:
    """Remove a workspace secret. Owner only."""
    workspace = await _require(store, workspace_id)
    await _authorise(store, workspace, actor)
    removed = await store.delete_secret_row(workspace_id=workspace_id, name=name)
    await store.commit()
    return removed


async def get_metadata(
    store: ContextStackStore, *, workspace_id: str, name: str, actor: str
) -> SecretMetadata:
    """Last-4, version, who set it and when. Owner only, and never the value."""
    workspace = await _require(store, workspace_id)
    await _authorise(store, workspace, actor)
    row = await store.secret_row(workspace_id=workspace_id, name=name)
    if row is None:
        raise NotFound(f"no secret {name!r} on workspace {workspace_id!r}")
    return _metadata(row)


async def list_metadata(
    store: ContextStackStore, *, workspace_id: str, actor: str
) -> tuple[SecretMetadata, ...]:
    """Every secret on the workspace, as metadata. Owner only."""
    workspace = await _require(store, workspace_id)
    await _authorise(store, workspace, actor)
    return tuple(_metadata(row) for row in await store.secret_rows(workspace_id))


async def _require(store: ContextStackStore, workspace_id: str) -> WorkspaceRef:
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
        action=Action.SECRETS,
    )
    if not verdict.allow:
        raise AccessDenied(f"{actor} may not manage secrets on {workspace.id}", decision=verdict)


__all__ = ["SecretMetadata", "set_secret", "delete_secret", "get_metadata", "list_metadata"]
