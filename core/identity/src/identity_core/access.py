"""canAccess(subject, resource, action) — the authz port + a default-deny owner-only adapter (P20).

Derived from the real ownership model: in the parent, every readable resource is keyed by
`user_id` (admin-api `User.id`; `Meeting.user_id`, `Recording.user_id`, and the gateway's
`/internal/validate` injects `X-User-ID` so downstream reads are owner-scoped). Reimplemented here
as an explicit policy seam instead of being implicit in each route.

The three read paths the port must guard, modeled as `ResourceKind`:
- `meeting_transcript` — a meeting's transcript (meeting-api / agent read).
- `recording`          — a stored recording.
- `ws_subscribe`       — the WS subscribe / agent live read.

Default policy (`OwnerOnlyPolicy`): a subject may read only resources it OWNS. Anything else —
including an unknown owner — is **default-deny**. The verdict conforms to identity.v1 AccessDecision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# The guarded read paths (identity.v1 ResourceKind).
RESOURCE_KINDS: frozenset[str] = frozenset({"meeting_transcript", "recording", "ws_subscribe"})
ResourceKind = str
Action = str  # identity.v1 Action: read | write | admin


@dataclass(frozen=True)
class Resource:
    """A thing access is decided over. `owner` is the user id that owns it (None = unowned/unknown)."""

    kind: ResourceKind
    id: str
    owner: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in RESOURCE_KINDS:
            raise ValueError(f"Unknown resource kind {self.kind!r}; valid: {sorted(RESOURCE_KINDS)}")


@dataclass(frozen=True)
class AccessDecision:
    """The verdict — the identity.v1 AccessDecision shape. Default-deny unless `allow` is True."""

    allow: bool
    subject: str
    resource_kind: ResourceKind
    resource_id: str
    action: Action
    reason: str  # owner | not-owner | default-deny | missing-scope | token-expired

    def to_contract(self) -> dict:
        return {
            "allow": self.allow,
            "subject": self.subject,
            "resource_kind": self.resource_kind,
            "resource_id": self.resource_id,
            "action": self.action,
            "reason": self.reason,
        }


@runtime_checkable
class AccessPolicy(Protocol):
    """The authz port. An adapter decides whether `subject` may `action` `resource`."""

    def decide(self, subject: str, resource: Resource, action: Action) -> AccessDecision: ...


class OwnerOnlyPolicy:
    """Default-deny owner-only adapter (P20).

    ALLOW only when the subject is the resource's owner. Every other case — different owner, no
    owner recorded, empty subject — is denied. This is the conservative floor; richer policies
    (sharing, org roles) layer on additively later without loosening this default.
    """

    def decide(self, subject: str, resource: Resource, action: Action) -> AccessDecision:
        def deny(reason: str) -> AccessDecision:
            return AccessDecision(False, subject, resource.kind, resource.id, action, reason)

        if not subject or resource.owner is None:
            return deny("default-deny")
        if subject == resource.owner:
            return AccessDecision(True, subject, resource.kind, resource.id, action, "owner")
        return deny("not-owner")


# Module-level default so callers get the safe policy for free.
_DEFAULT_POLICY: AccessPolicy = OwnerOnlyPolicy()


def can_access(
    subject: str,
    resource: Resource,
    action: Action = "read",
    *,
    policy: AccessPolicy | None = None,
) -> AccessDecision:
    """Decide access. Defaults to the owner-only default-deny policy when none is supplied."""
    return (policy or _DEFAULT_POLICY).decide(subject, resource, action)
