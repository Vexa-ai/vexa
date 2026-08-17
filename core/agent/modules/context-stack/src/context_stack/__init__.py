"""The context stack: the four layers every product-mode agent turn composes.

| Layer           | Access                       | Content                                            |
|-----------------|------------------------------|-----------------------------------------------------|
| **global**      | read-only, hidden            | product-level knowledge/behaviour, ours              |
| **group**       | read/write via triage        | shared; writes land as proposals the owner triages   |
| **personal**    | read/write                   | always exists for every user — the user is not a group |
| **user-system** | read, hidden, never sharable | sessions, chat history; holds no external credentials |

Read ``layers.py`` first — the table above lives there as code, once.

``material`` is deliberately absent from this front door. The one function that reads secret
material is ``context_stack.material.use_material``, and importing it has to be an explicit act
by the one caller that needs it at LLM-call time. Everything else gets
:class:`context_stack.secrets.SecretMetadata`, which has no material field.
"""

from __future__ import annotations

from .access import AccessDecision, Action, decide
from .errors import (
    AccessDenied,
    ContextStackError,
    InvalidWorkspace,
    NotFound,
    ProposalAlreadyDecided,
)
from .layers import RULES, STACK_ORDER, Policy, Role, Write
from .models import (
    Base,
    ContextRevision,
    Membership,
    Proposal,
    StackPointer,
    Workspace,
    WorkspaceSecret,
)
from .resolver import Denial, Mode, ResolvedStack, StackSlot, resolve_stack
from .router import ContextDelta, Destination, Landed, Routing, land_delta, route
from .secrets import SecretMetadata, delete_secret, get_metadata, list_metadata, set_secret
from .store import ContextStackStore, MemberRef, ProposalRef, RevisionRef, WorkspaceRef
from .triage import accept_proposal, pending_proposals, reject_proposal
from .workspaces import add_member, create_workspace, ensure_personal, ensure_user_system, remove_member

__all__ = [
    # the layer table
    "Policy",
    "Role",
    "Write",
    "RULES",
    "STACK_ORDER",
    # schema
    "Base",
    "Workspace",
    "Membership",
    "ContextRevision",
    "Proposal",
    "StackPointer",
    "WorkspaceSecret",
    # store
    "ContextStackStore",
    "WorkspaceRef",
    "MemberRef",
    "RevisionRef",
    "ProposalRef",
    # provisioning + membership
    "create_workspace",
    "ensure_personal",
    "ensure_user_system",
    "add_member",
    "remove_member",
    # composition
    "resolve_stack",
    "Mode",
    "ResolvedStack",
    "StackSlot",
    "Denial",
    # write routing (machine path)
    "route",
    "land_delta",
    "ContextDelta",
    "Routing",
    "Destination",
    "Landed",
    # triage (human path)
    "accept_proposal",
    "reject_proposal",
    "pending_proposals",
    # access
    "decide",
    "Action",
    "AccessDecision",
    # secrets — metadata only
    "SecretMetadata",
    "set_secret",
    "delete_secret",
    "get_metadata",
    "list_metadata",
    # errors
    "ContextStackError",
    "AccessDenied",
    "NotFound",
    "InvalidWorkspace",
    "ProposalAlreadyDecided",
]
