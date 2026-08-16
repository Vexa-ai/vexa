"""What a door session may reach — the v0 scope check, deliberately small and default-deny.

The product spec's context stack is global → group → personal → user-system, and the
load-bearing rule is that a **non-member never reaches group context**. This module is the v0
enforcement point for exactly two questions:

1. *Which meeting may this session read?* — one, the one its token names.
2. *May it read group context?* — only ``member`` scope may; ``guest`` never.

It is a **stub in one honest sense**: nothing here consults a real membership table, because
workspace membership does not exist yet in this branch. The scope travels in the signed token,
so the issuer (the artifact postman) decides it, and the door enforces whatever it was handed.
When membership lands, only :func:`scope_for` moves — the enforcement below stays.
"""
from __future__ import annotations

from dataclasses import dataclass

SCOPE_MEMBER = "member"
SCOPE_GUEST = "guest"
VALID_SCOPES = (SCOPE_MEMBER, SCOPE_GUEST)

DENY_WRONG_MEETING = "out_of_scope_meeting"
DENY_GROUP_CONTEXT = "group_context_denied"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def normalize(scope: str | None) -> str:
    """Anything unrecognized degrades to ``guest`` — default-deny, never default-allow."""
    return scope if scope in VALID_SCOPES else SCOPE_GUEST


def may_read_meeting(session_meeting_id: str, requested_meeting_id: str) -> Decision:
    if str(session_meeting_id) != str(requested_meeting_id):
        return Decision(False, DENY_WRONG_MEETING)
    return Decision(True)


def may_read_group_context(scope: str | None) -> Decision:
    if normalize(scope) != SCOPE_MEMBER:
        return Decision(False, DENY_GROUP_CONTEXT)
    return Decision(True)
