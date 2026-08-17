"""Who may do what to a workspace. Default-deny, pure, and the only place the answer is decided.

Every allow is an explicit rule; anything the rules do not name is refused with ``default-deny``.
The decision is a value, not a boolean — a refusal that cannot say why is not auditable, and the
access-rights question a regulated buyer asks is answered by reading these codes, not by reading
control flow.

Follows the shape ``identity_core.access`` already uses in this repo: a frozen decision carrying
subject, resource, action and a stable machine reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .layers import RULES, Policy, Role, Write


class Action(str, Enum):
    """What is being attempted."""

    READ = "read"
    """Read the layer's context."""

    WRITE = "write"
    """Land a context delta. An allow does NOT mean a direct write — see ``Routing``."""

    TRIAGE = "triage"
    """Accept or reject a proposal. Owner only."""

    SECRETS = "secrets"
    """Set, rotate or delete a workspace secret. Owner only."""

    SHARE = "share"
    """Add or remove a member."""


@dataclass(frozen=True)
class AccessDecision:
    """The verdict. ``reason`` is a stable code, never free text."""

    allow: bool
    subject: str
    workspace_id: str
    policy: Policy
    action: Action
    reason: str

    def to_contract(self) -> dict:
        """The ``context-stack.v1`` AccessDecision shape."""
        return {
            "allow": self.allow,
            "subject": self.subject,
            "workspace_id": self.workspace_id,
            "policy": self.policy.value,
            "action": self.action.value,
            "reason": self.reason,
        }


def decide(
    *,
    subject: str,
    workspace_id: str,
    policy: Policy,
    role: Role | None,
    action: Action,
) -> AccessDecision:
    """Decide one attempt. ``role`` is the actor's membership role, or ``None`` for a non-member.

    Pure: it reads no store and performs no I/O, so the whole policy is one readable table and a
    caller cannot accidentally make a different decision by holding a different session.
    """
    rules = RULES[policy]

    def verdict(allow: bool, reason: str) -> AccessDecision:
        return AccessDecision(
            allow=allow,
            subject=subject,
            workspace_id=workspace_id,
            policy=policy,
            action=action,
            reason=reason,
        )

    if action is Action.READ:
        # Global is product-level knowledge, mounted by everyone's stack and hidden from all of
        # them. Every other layer is membership-gated, which is what keeps a non-member out of a
        # group's context.
        if rules.readable_by_everyone:
            return verdict(True, "global-readable")
        if role is None:
            return verdict(False, "not-member")
        return verdict(True, role.value)

    if action is Action.WRITE:
        if rules.write is Write.NONE:
            return verdict(False, "global-is-read-only")
        if rules.write is Write.PLATFORM_ONLY:
            return verdict(False, "user-system-is-platform-only")
        if role is None:
            return verdict(False, "not-member")
        return verdict(True, role.value)

    if action is Action.TRIAGE:
        # Only the group layer has a queue at all, and only its owner answers it.
        if rules.write is not Write.VIA_TRIAGE:
            return verdict(False, "layer-has-no-proposal-queue")
        if role is not Role.OWNER:
            return verdict(False, "not-owner")
        return verdict(True, "owner")

    if action is Action.SECRETS:
        # The user-system layer holds no external credentials ever, and the global layer's
        # credentials are deployment config rather than rows an owner sets. Both refusals are
        # properties of the layer, checked before the role — so no role can reach them.
        if not rules.holds_credentials:
            if policy is Policy.USER_SYSTEM:
                return verdict(False, "user-system-holds-no-credentials")
            return verdict(False, "global-secrets-are-deployment-config")
        if role is not Role.OWNER:
            return verdict(False, "not-owner")
        return verdict(True, "owner")

    if action is Action.SHARE:
        # "The user is not a group": a personal workspace is the one-member case, so it has no
        # second member to add. Making it shared means making it a group workspace.
        if policy is Policy.PERSONAL:
            return verdict(False, "personal-is-not-a-group")
        if not rules.sharable:
            return verdict(False, "layer-not-sharable")
        if role is not Role.OWNER:
            return verdict(False, "not-owner")
        return verdict(True, "owner")

    return verdict(False, "default-deny")
