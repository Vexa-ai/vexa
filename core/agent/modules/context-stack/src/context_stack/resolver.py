"""Composition: given a user, return the ordered stack as pointers.

Two modes, because the schema and the product want different things and the founder ruling is that
both are true at once — **loosely coupled by schema, pinned by product**.

``PINNED`` is the product path. The group slot is *derived* from membership: a user gets exactly
the groups they belong to, in a deterministic order, and there is nothing to compose. An ordinary
participant never sees composition because there is none to see.

``FREE`` is what the terminal already does. A slot with pointer rows uses them, in their order;
a slot without falls back to the pinned derivation. So free composition is a superset, reached by
writing pointers, not by a different code path — and the two modes cannot drift apart because the
fallback is the pinned function itself.

The modes differ in exactly one place: the group slot. The three singleton slots honour a pointer
in both modes, because re-pointing a user at a different personal workspace changes *which*
workspace fills a slot, not the shape of the stack — that is migration, and the pinned product
path needs it to work.

What comes back is **pointers, never content**: each slot names a workspace and the access rules
of its layer. Reading a document is a separate, separately-authorised act.

Every slot is authorised before it is returned. A pointer at a group the user does not belong to
is dropped, not mounted — a non-member never reaches group context, whichever mode resolved the
stack, and the reason is recorded in ``denied`` rather than swallowed, because a stack that is
quietly shorter than the user expects is a bug nobody can see.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .access import Action, decide
from .layers import RULES, SINGLETON_LAYERS, STACK_ORDER, Policy, Write
from .store import ContextStackStore, WorkspaceRef


class Mode(str, Enum):
    PINNED = "pinned"
    """The product's fixed composition: global → the user's groups → personal → user-system."""

    FREE = "free"
    """The terminal's composition: pointer rows where they exist, the pinned derivation elsewhere."""


@dataclass(frozen=True)
class StackSlot:
    """One mounted layer. A pointer plus the layer's access rules — no context."""

    layer: Policy
    workspace_id: str
    name: str
    address: str
    write: Write
    hidden: bool
    sharable: bool

    def to_contract(self) -> dict:
        return {
            "layer": self.layer.value,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "address": self.address,
            "write": self.write.value,
            "hidden": self.hidden,
            "sharable": self.sharable,
        }


@dataclass(frozen=True)
class Denial:
    """A slot that was asked for and not mounted, with a stable reason code."""

    layer: Policy
    workspace_id: str | None
    reason: str

    def to_contract(self) -> dict:
        return {
            "layer": self.layer.value,
            "workspace_id": self.workspace_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResolvedStack:
    subject: str
    mode: Mode
    slots: tuple[StackSlot, ...]
    denied: tuple[Denial, ...]

    def at(self, layer: Policy) -> tuple[StackSlot, ...]:
        return tuple(s for s in self.slots if s.layer is layer)

    @property
    def workspace_ids(self) -> tuple[str, ...]:
        return tuple(s.workspace_id for s in self.slots)

    def to_contract(self) -> dict:
        return {
            "subject": self.subject,
            "mode": self.mode.value,
            "slots": [s.to_contract() for s in self.slots],
            "denied": [d.to_contract() for d in self.denied],
        }


def _slot(workspace: WorkspaceRef) -> StackSlot:
    rules = RULES[workspace.policy]
    return StackSlot(
        layer=workspace.policy,
        workspace_id=workspace.id,
        name=workspace.name,
        address=workspace.address,
        write=rules.write,
        hidden=rules.hidden,
        sharable=rules.sharable,
    )


async def resolve_stack(
    store: ContextStackStore, *, subject: str, mode: Mode = Mode.PINNED
) -> ResolvedStack:
    """The ordered stack for one user. Default is the product's pinned composition.

    A user in no group resolves to global → personal → user-system, and that is the participant
    case: someone who was on a meeting invitation, has never joined a group, and still gets an
    artifact built from the meeting record and their own personal context.
    """
    slots: list[StackSlot] = []
    denied: list[Denial] = []
    by_slot: dict[Policy, list[str]] = {}
    for pointer in await store.pointers(subject):
        by_slot.setdefault(pointer.slot, []).append(pointer.workspace_id)

    for layer in STACK_ORDER:
        # The one difference between the modes: pinned always derives the group slot from
        # membership, so the product's composition is not something a stored row can reshape.
        # The singleton slots honour their pointer in both modes — re-pointing a user at a
        # different personal workspace changes which workspace fills a slot, not the shape of
        # the stack, so it is migration, not composition, and the product needs it to work.
        targets = None if (mode is Mode.PINNED and layer is Policy.GROUP) else by_slot.get(layer)
        if targets:
            resolved, refusals = await _from_pointers(store, subject, layer, targets)
        else:
            resolved, refusals = await _pinned(store, subject, layer)
        slots.extend(resolved)
        denied.extend(refusals)

    return ResolvedStack(
        subject=subject, mode=mode, slots=tuple(slots), denied=tuple(denied)
    )


async def _pinned(
    store: ContextStackStore, subject: str, layer: Policy
) -> tuple[list[StackSlot], list[Denial]]:
    """The derivation. Groups come from membership; the singleton layers from ownership."""
    if layer is Policy.GROUP:
        groups = await store.memberships_of(subject, policy=Policy.GROUP)
        return [_slot(w) for w in groups], []

    if layer is Policy.GLOBAL:
        workspace = await store.global_workspace()
        if workspace is None:
            return [], [Denial(layer=layer, workspace_id=None, reason="global-not-provisioned")]
        return [_slot(workspace)], []

    workspace = await store.owned_workspace(subject, layer)
    if workspace is None:
        # Personal is supposed to exist for every user; user-system likewise. Reporting the gap
        # beats raising, because the rest of the stack is still usable and the caller can see
        # exactly which layer is missing instead of losing the whole resolution to one exception.
        return [], [Denial(layer=layer, workspace_id=None, reason=f"{layer.value}-not-provisioned")]
    return [_slot(workspace)], []


async def _from_pointers(
    store: ContextStackStore, subject: str, layer: Policy, targets: list[str]
) -> tuple[list[StackSlot], list[Denial]]:
    """Free composition for one slot, authorised pointer by pointer."""
    slots: list[StackSlot] = []
    denied: list[Denial] = []
    found = await store.workspaces_by_id(targets)

    for workspace_id in targets:
        workspace = found.get(workspace_id)
        if workspace is None:
            # The pointer has no foreign key by design; a target this deployment does not hold is
            # a condition to report, not a crash.
            denied.append(Denial(layer=layer, workspace_id=workspace_id, reason="dangling-pointer"))
            continue
        if workspace.policy is not layer:
            denied.append(Denial(layer=layer, workspace_id=workspace_id, reason="policy-mismatch"))
            continue
        role = await store.role_of(workspace_id=workspace_id, subject=subject)
        verdict = decide(
            subject=subject,
            workspace_id=workspace_id,
            policy=workspace.policy,
            role=role,
            action=Action.READ,
        )
        if not verdict.allow:
            denied.append(
                Denial(layer=layer, workspace_id=workspace_id, reason=verdict.reason)
            )
            continue
        slots.append(_slot(workspace))
        if layer in SINGLETON_LAYERS:
            break

    return slots, denied
