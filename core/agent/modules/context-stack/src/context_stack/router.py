"""Where a context delta lands. The machine path — and it has a ceiling.

A meeting produces deltas: what changed in a context because the meeting happened. Each one is
addressed to a workspace, and that workspace's policy field decides the rest:

* **personal policy** → straight into personal context as a new revision.
* **group policy** → the group's proposal queue, PR-style. Never direct. The owner triages.
* **global policy** → refused. Global is ours and read-only.
* **user-system policy** → refused. Sessions and chat history are the platform's to write.

**No machine ever writes acknowledgement.** Accept and reject live in ``triage.py``, and the
dependency runs one way: triage imports this module's types, so this module importing triage is a
cycle Python refuses to load. The wiring that would let a meeting accept its own proposal is not
merely absent, it cannot be added here — the most this path can produce on the group layer is a
``pending`` row. See ``triage.py`` for the other two guards (a required owner ``actor``, and a
table CHECK that refuses any decided proposal not naming who decided and when).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import NotFound
from .layers import RULES, Policy, Write
from .store import ContextStackStore, ProposalRef, RevisionRef


class Destination(str, Enum):
    DIRECT = "direct"
    """Appended to the workspace's context now."""

    PROPOSAL = "proposal"
    """Queued for the owner. Not in context, and will not be until a human says so."""

    REFUSED = "refused"
    """Nothing was written."""


@dataclass(frozen=True)
class ContextDelta:
    """What changed in a context because a meeting (or a chat) happened."""

    workspace_id: str
    path: str
    body: str
    author_subject: str
    source_kind: str = "meeting"
    source_ref: str | None = None


@dataclass(frozen=True)
class Routing:
    """The routing verdict, before anything is written."""

    destination: Destination
    layer: Policy
    workspace_id: str
    reason: str

    def to_contract(self) -> dict:
        return {
            "destination": self.destination.value,
            "layer": self.layer.value,
            "workspace_id": self.workspace_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Landed:
    """Where a delta actually went. On a refusal both ids are absent."""

    routing: Routing
    revision: RevisionRef | None = None
    proposal: ProposalRef | None = None


def route(*, policy: Policy, workspace_id: str, member: bool) -> Routing:
    """The routing table, as a pure function of policy and membership. No I/O, no store.

    Split out from :func:`land_delta` so the table can be read — and tested — without a database.
    The rule is the product decision; the write is only its consequence.
    """
    write = RULES[policy].write

    if write is Write.NONE:
        return Routing(Destination.REFUSED, policy, workspace_id, "global-is-read-only")
    if write is Write.PLATFORM_ONLY:
        return Routing(Destination.REFUSED, policy, workspace_id, "user-system-is-platform-only")
    if not member:
        # A non-member cannot even propose. A queue anyone may fill is a queue the owner stops
        # reading, and a group's context should be unreachable from outside the group in both
        # directions, not just on the read side.
        return Routing(Destination.REFUSED, policy, workspace_id, "not-member")
    if write is Write.VIA_TRIAGE:
        return Routing(Destination.PROPOSAL, policy, workspace_id, "group-policy-routes-to-triage")
    return Routing(Destination.DIRECT, policy, workspace_id, "personal-policy-writes-direct")


async def land_delta(store: ContextStackStore, delta: ContextDelta) -> Landed:
    """Route a delta and write it where the routing says."""
    workspace = await store.get_workspace(delta.workspace_id)
    if workspace is None:
        raise NotFound(f"no workspace {delta.workspace_id!r}")

    role = await store.role_of(workspace_id=workspace.id, subject=delta.author_subject)
    routing = route(policy=workspace.policy, workspace_id=workspace.id, member=role is not None)

    if routing.destination is Destination.REFUSED:
        return Landed(routing=routing)

    if routing.destination is Destination.PROPOSAL:
        proposal = await store.insert_proposal(
            workspace_id=workspace.id,
            path=delta.path,
            body=delta.body,
            proposer_subject=delta.author_subject,
            source_kind=delta.source_kind,
            source_ref=delta.source_ref,
        )
        await store.commit()
        return Landed(routing=routing, proposal=proposal)

    revision = await store.append_revision(
        workspace_id=workspace.id,
        path=delta.path,
        body=delta.body,
        author_subject=delta.author_subject,
        source_kind=delta.source_kind,
        source_ref=delta.source_ref,
    )
    await store.commit()
    return Landed(routing=routing, revision=revision)
