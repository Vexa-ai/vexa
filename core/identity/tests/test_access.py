"""gate:access deny-tests (riding gate:python for now) — the canAccess port.

Asserts the default-deny owner-only policy on each of the three guarded read paths:
- a NON-owner reading a meeting_transcript / recording / ws_subscribe → DENY
- the OWNER reading the same → ALLOW
Plus the unowned-resource and empty-subject default-deny corners.
"""

import pytest

from identity_core import OwnerOnlyPolicy, Resource, can_access

OWNER = "42"
OTHER = "99"

# The three read paths the port must guard.
READ_PATHS = ["meeting_transcript", "recording", "ws_subscribe"]


@pytest.mark.parametrize("kind", READ_PATHS)
def test_other_user_denied_on_each_read_path(kind):
    """canAccess(otherUser, <path>, read) → DENY on all 3 paths."""
    resource = Resource(kind=kind, id=f"{kind}-1", owner=OWNER)
    decision = can_access(OTHER, resource, "read")
    assert decision.allow is False
    assert decision.reason == "not-owner"
    assert decision.subject == OTHER
    assert decision.resource_kind == kind


@pytest.mark.parametrize("kind", READ_PATHS)
def test_owner_allowed_on_each_read_path(kind):
    """canAccess(owner, <path>, read) → ALLOW on all 3 paths."""
    resource = Resource(kind=kind, id=f"{kind}-1", owner=OWNER)
    decision = can_access(OWNER, resource, "read")
    assert decision.allow is True
    assert decision.reason == "owner"


@pytest.mark.parametrize("kind", READ_PATHS)
def test_unowned_resource_default_denies(kind):
    """A resource with no recorded owner is default-deny even for a real subject."""
    resource = Resource(kind=kind, id=f"{kind}-1", owner=None)
    decision = can_access(OWNER, resource, "read")
    assert decision.allow is False
    assert decision.reason == "default-deny"


def test_empty_subject_default_denies():
    """An empty/anonymous subject is default-deny against an owned resource."""
    resource = Resource(kind="recording", id="rec-1", owner=OWNER)
    decision = can_access("", resource, "read")
    assert decision.allow is False
    assert decision.reason == "default-deny"


def test_default_policy_is_owner_only():
    """can_access with no explicit policy uses the default-deny owner-only adapter."""
    resource = Resource(kind="meeting_transcript", id="m-1", owner=OWNER)
    implicit = can_access(OTHER, resource, "read")
    explicit = can_access(OTHER, resource, "read", policy=OwnerOnlyPolicy())
    assert implicit.allow is explicit.allow is False


def test_unknown_resource_kind_rejected():
    """Resource only accepts the three contract ResourceKinds."""
    with pytest.raises(ValueError):
        Resource(kind="billing", id="x", owner=OWNER)
