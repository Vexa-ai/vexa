"""The four layers, as one table.

The context stack is the product's central primitive: every product-mode agent turn composes
four layers, and each layer's access rules are FIXED properties of the layer — not per-workspace
configuration. That table appears exactly once, here, so a reader can check it against the
product spec line by line and so no call site can invent a fifth combination.

| Layer       | Access                | Content                                            |
|-------------|-----------------------|----------------------------------------------------|
| global      | read-only, hidden     | product-level knowledge/behaviour, ours             |
| group       | read/write via triage | shared; writes land as proposals the owner triages  |
| personal    | read/write            | always exists for every user — the user is not a group |
| user-system | read, hidden, never sharable | sessions, chat history; holds no external credentials |

**Policy IS the layer.** A workspace carries one explicit ``policy`` field, and that single value
answers both questions the stack asks of it: *where does it sit in the composition* (its layer) and
*how does a write to it land* (direct / via triage / refused). They are deliberately not two
columns. Two columns can disagree, and the only disagreement that matters — a workspace sitting at
the group layer while accepting direct writes — is precisely the bypass of owner triage the product
forbids. One field cannot express it.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class Policy(str, Enum):
    """A workspace's policy field — also its layer in the stack. See the module docstring."""

    GLOBAL = "global"
    GROUP = "group"
    PERSONAL = "personal"
    USER_SYSTEM = "user-system"


class Write(str, Enum):
    """How a context delta addressed to a layer lands."""

    NONE = "none"
    """Nothing writes here through this module (global is ours, shipped with the product)."""

    DIRECT = "direct"
    """Straight into the layer's context as a new revision."""

    VIA_TRIAGE = "via-triage"
    """Into the workspace's proposal queue. Never into context until a human owner accepts."""

    PLATFORM_ONLY = "platform-only"
    """Written by the platform (sessions, chat history), never by a context delta."""


class Role(str, Enum):
    """The two roles. Owner: config, membership, triage, secrets. Member: chat, knowledge."""

    OWNER = "owner"
    MEMBER = "member"


class LayerRules:
    """The fixed properties of one layer. Constructed only in :data:`RULES`."""

    __slots__ = (
        "order",
        "write",
        "hidden",
        "sharable",
        "readable_by_everyone",
        "holds_credentials",
    )

    def __init__(
        self,
        *,
        order: int,
        write: Write,
        hidden: bool,
        sharable: bool,
        readable_by_everyone: bool,
        holds_credentials: bool,
    ) -> None:
        self.order = order
        self.write = write
        self.hidden = hidden
        self.sharable = sharable
        self.readable_by_everyone = readable_by_everyone
        self.holds_credentials = holds_credentials


RULES: Mapping[Policy, LayerRules] = {
    # Product-level knowledge and behaviour, ours. Every stack mounts it; nobody sees it and
    # nothing in this module writes it — it ships with the product. Its credentials are
    # deployment config (a k8s Secret, a chart value), not rows a workspace owner sets.
    Policy.GLOBAL: LayerRules(
        order=0,
        write=Write.NONE,
        hidden=True,
        sharable=False,
        readable_by_everyone=True,
        holds_credentials=False,
    ),
    # The shared layer, and the ONLY sharable one. A member may propose; only the owner accepts.
    # The owner-supplied LLM key (BYOT) belongs here or on personal — the two layers a user owns.
    Policy.GROUP: LayerRules(
        order=1,
        write=Write.VIA_TRIAGE,
        hidden=False,
        sharable=True,
        readable_by_everyone=False,
        holds_credentials=True,
    ),
    # Always exists for every user. One member — the user is not a group, so it cannot take a
    # second member; a workspace that needs two members is a group workspace.
    Policy.PERSONAL: LayerRules(
        order=2,
        write=Write.DIRECT,
        hidden=False,
        sharable=False,
        readable_by_everyone=False,
        holds_credentials=True,
    ),
    # Sessions and chat history. Hidden, never sharable, and it HOLDS NO EXTERNAL CREDENTIALS
    # EVER — stated in the spec as a property of the layer, so it is one here: the secret surface
    # refuses this layer outright rather than relying on nobody trying.
    Policy.USER_SYSTEM: LayerRules(
        order=3,
        write=Write.PLATFORM_ONLY,
        hidden=True,
        sharable=False,
        readable_by_everyone=False,
        holds_credentials=False,
    ),
}

STACK_ORDER: tuple[Policy, ...] = tuple(sorted(RULES, key=lambda p: RULES[p].order))
"""global → group → personal → user-system. The pinned product composition's slot order."""

SINGLETON_LAYERS: frozenset[Policy] = frozenset(
    {Policy.GLOBAL, Policy.PERSONAL, Policy.USER_SYSTEM}
)
"""Layers that hold exactly one workspace per user. Only the group layer composes several."""
