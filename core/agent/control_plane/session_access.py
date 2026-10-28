"""session_access.py — who may use what in a session's turn (ADR-0048 decisions 4 and 5).

THE TWO GATES. Session access (participants and roles, ``session_store``) decides who reads and
writes the conversation. Workspace access — the speaking person's own membership, narrowed by the
job's ceiling — decides what the agent may read and write in that turn. Sharing a session grants no
workspace: a participant's turn runs on their own mounts, never on the owner's.

THE LEAK RULE. An answer posted into a session is read by every participant, so a turn in a session
with more than one person may use only what every current participant can read — the intersection.
``shared_scope`` computes it from the speaker's candidate workspaces; the speaker's Personal workspace
and ``_system`` are never in it (nobody else can read them), ``_global`` always is (every member of the
organisation reads it). It is enforced, not asked for:

- ``narrow_mounts`` removes every withheld workspace from the worker's mount table, so it is not on
  the worker's disk;
- the dispatcher sets the delegation ceiling to the intersection and marks the token ``shared``
  (delegation.v1 ``scope.shared``), and every service refuses such a worker a person-wide read —
  agent-api and the credential broker the person's connections (mail, calendar, any other), and
  meeting-api the person's meetings, transcripts, recordings and bots (``SHARED_REFUSAL``,
  ``reason: shared_conversation``).

``leak_preamble`` tells the agent who reads the answer, what is withheld and what to say instead
(that it cannot use it here, and to offer the speaker's private chat). The one exception is an item
the speaker shares explicitly for that turn (``shared_items_block``): a workspace page agent-api
reads with the speaker's own access and puts into the turn's prompt; no token grant comes with it.

WHEN THE AGENT SPEAKS. One person: every message. Two or more: only when mentioned (``@vexa`` or an
agent's name) or asked directly. The policy key ``session_agent_speaks`` (``always`` | ``mentioned``)
is instance-wide: agent-api reads it from ``_global/POLICIES.md`` front matter, by the one reader it
uses for that file (``front_page.front_matter``); a value that is neither is logged and the default
is used. ``speaks_policy`` also takes a channel root, which no caller passes today.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

GLOBAL = "_global"
SYSTEM = "_system"
POLICY_KEY = "session_agent_speaks"
SPEAKS_VALUES = ("always", "mentioned")
DEFAULT_SPEAKS = "mentioned"
AGENT_NAME = "vexa"

logger = logging.getLogger("agent_api.session_access")


# ── decision 5 ────────────────────────────────────────────────────────────────────────────────
def normalise_speaks(value: Optional[str]) -> str:
    """The answer, or the default. An answer that is neither value is SAID (the same shape the flows
    policy reader uses: ``key: 'x' is not one of … — using the default``), never silently swallowed:
    an admin who wrote ``sometimes`` must be able to find out why the agent still waits."""
    v = str(value or "").strip().lower()
    if v in SPEAKS_VALUES:
        return v
    if v:
        logger.warning("policies: %s: %r is not one of %s — using the default %r",
                       POLICY_KEY, v, ", ".join(SPEAKS_VALUES), DEFAULT_SPEAKS)
    return DEFAULT_SPEAKS


def _front_matter(root: Path) -> dict:
    """The front matter of ``root/POLICIES.md``, read without following a link (the file sits in a
    workspace whose tools may write it), parsed by the one reader agent-api uses for that file
    (``front_page.front_matter``: flat, lowercased keys, quotes stripped)."""
    from control_plane import front_page  # noqa: PLC0415
    from workspaces.shared import workspace_paths as wpaths  # noqa: PLC0415

    try:
        text = wpaths.read_text_inside(root, "POLICIES.md", max_bytes=1 << 20)
    except Exception:  # noqa: BLE001 — an unreadable policy file is the default
        return {}
    if text is None:
        return {}
    return front_page.front_matter(text)


def speaks_policy(global_root: "Path | None", channel_root: "Path | None" = None) -> str:
    """The deployment's answer to *when does the agent speak with two or more people*: the channel
    workspace's own ``POLICIES.md`` first, then the organisation's, then the default."""
    for root in (channel_root, global_root):
        if root is None:
            continue
        fm = _front_matter(Path(root))
        if POLICY_KEY in fm:
            return normalise_speaks(fm[POLICY_KEY])
    return DEFAULT_SPEAKS


# ── decision 7 ────────────────────────────────────────────────────────────────────────────────
AUTO_ACCEPT_KEY = "post_meeting_auto_accept"


def auto_accept_policy(global_root: "Path | None", workspace_root: "Path | None" = None) -> bool:
    """Does a change proposed after a meeting land without its owner's decision? The target
    workspace's own ``POLICIES.md`` first, then the organisation's; anything but ``on`` is off."""
    for root in (workspace_root, global_root):
        if root is None:
            continue
        fm = _front_matter(Path(root))
        if AUTO_ACCEPT_KEY in fm:
            return str(fm[AUTO_ACCEPT_KEY]).strip().lower() == "on"
    return False


def mentioned(text: str, agent_names: Iterable[str] = ()) -> bool:
    """``@vexa`` or ``@<agent>`` as a word, or the message opening by addressing the agent."""
    names = [AGENT_NAME, *(str(n).strip().lower() for n in agent_names if str(n).strip())]
    low = (text or "").lower()
    for n in names:
        if re.search(rf"(?<![\w.@])@{re.escape(n)}\b", low):
            return True
        if re.match(rf"\s*(hey\s+|ok\s+)?{re.escape(n)}\s*[,:?!]", low):
            return True
    return False


def agent_speaks(text: str, *, humans: int, policy: str = DEFAULT_SPEAKS,
                 agent_names: Iterable[str] = ()) -> bool:
    if humans <= 1 or normalise_speaks(policy) == "always":
        return True
    return mentioned(text, agent_names)


# ── decision 4: the leak rule ─────────────────────────────────────────────────────────────────
@dataclass
class SharedScope:
    allowed: list = field(default_factory=list)
    withheld: list = field(default_factory=list)


def shared_scope(speaker: str, *, others: Iterable[str], candidates: Iterable[str],
                 is_member: Callable[[str, str], bool]) -> Optional[SharedScope]:
    """The workspaces a turn by ``speaker`` may use when ``others`` (the session's other human
    participants) will read the answer — or None when nobody else will (no narrowing).

    ``candidates`` are the workspaces the speaker's own turn would reach. ``is_member(ws, who)``
    answers from the authoritative member list. The speaker's desk and ``_system`` are private by
    construction; ``_global`` is the organisation's and every participant reads it."""
    others = [o for o in others if o and o != speaker]
    if not others:
        return None
    scope = SharedScope()
    seen: set = set()
    for ws in candidates:
        ws = str(ws)
        if not ws or ws in seen:
            continue
        seen.add(ws)
        if ws == GLOBAL:
            scope.allowed.append(ws)
        elif ws in (SYSTEM, speaker):
            scope.withheld.append(ws)
        elif all(_safe(is_member, ws, o) for o in others):
            scope.allowed.append(ws)
        else:
            scope.withheld.append(ws)
    return scope


def _safe(is_member, ws, who) -> bool:
    try:
        return bool(is_member(ws, who))
    except Exception:  # noqa: BLE001 — an unreadable member list is not a membership
        return False


def narrow_mounts(mounts: list[dict], scope: Optional[SharedScope]) -> list[dict]:
    """The mount table with every withheld workspace removed (``scope`` None: unchanged)."""
    if scope is None:
        return mounts
    allowed = set(scope.allowed)
    return [m for m in mounts if str(m.get("slug") or "") in allowed]


def leak_preamble(scope: Optional[SharedScope], *, names: "dict | None" = None, people: int) -> str:
    """What the agent is told in a shared session: who reads the answer, what it may use, that some
    workspaces are withheld (never which: the preamble is part of the shared transcript), and what
    to say when a question needs one of them."""
    if scope is None:
        return ""
    names = names or {}
    allowed = [names.get(w, w) for w in scope.allowed]
    lines = [
        "## This is a shared conversation",
        "",
        f"{people} people read every answer here. Use only what all of them can read: "
        + (", ".join(allowed) if allowed else "nothing beyond this conversation") + ".",
    ]
    if allowed:
        lines.append("A meeting sent into one of those workspaces is theirs too: read its transcript "
                     "here (get_meeting_transcript) when asked. A meeting kept private is not.")
    if scope.withheld:
        # the line is kept in the shared transcript, so it never names what it withholds
        lines.append("Some workspaces are withheld this turn: only the person speaking can read them.")
    lines += [
        "If answering needs something you cannot use here, say plainly that you can't use it here, "
        "and offer to answer in the speaker's private chat. Never quote, summarise or hint at "
        "private material in this conversation.",
        "",
    ]
    return "\n".join(lines) + "\n"


#: Told to a routine's run in a chat other people are in: it runs on what everyone there can read,
#: and a read the run is refused is reported in the chat rather than worked around or skipped quietly.
ROUTINE_SHARED_NOTE = (
    "This turn is a scheduled routine's run, in a chat other people are in. It may use only what "
    "every one of them can read: the owner's mail, calendar, meetings and other connections are "
    "refused here (`shared_conversation`). If a step of the routine needs one of them, do the rest, "
    "and say in your reply which read was refused and why, in one sentence; the routine's owner can "
    "move it back to a private chat.\n\n")


def shared_with(store, subject: str, session: str, name_of: Callable[[str], str]) -> list[str]:
    """The OTHER people in ``subject``'s chat ``session``, by name (``"someone"`` for one with no
    name we can say) — what a routine paused in a shared chat tells its owner (Vexa-ai/vexa#1783).
    ``[]`` for a private chat, an unknown one, or a store that cannot answer. Never raises."""
    if store is None:
        return []
    try:
        from control_plane import session_sync  # noqa: PLC0415
        row = session_sync.resolve(store, subject, session, create=False)
        if row is None:
            return []
        others = [p["principal"] for p in store.participants(row["id"])
                  if p.get("kind") == "human" and str(p.get("principal")) != str(subject)]
    except Exception:  # noqa: BLE001 — naming the people is never worth a failed answer
        return []
    out = []
    for who in others:
        try:
            name = str(name_of(str(who)) or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        out.append(name or "someone")
    return out


def shared_items_block(items: list[dict], *, speaker: str) -> str:
    """Items the speaker chose to share for this turn — the one exception to the leak rule."""
    if not items:
        return ""
    parts = [f"## {speaker} shared these for this conversation", ""]
    for it in items:
        parts += [f"### {it.get('label') or 'shared item'}", "", str(it.get("text") or ""), ""]
    return "\n".join(parts) + "\n"
