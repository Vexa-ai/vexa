"""session_fork.py — what a person added to a chat sees of it (ADR-0048 decision 4).

A conversation one person had alone ran with everything that person can read: their desk,
``_system``, their mail. Whether the next person sees it is the OWNER's decision, made explicitly
when the chat stops being theirs alone (``routers/sessions.py``, ``history``):

- ``all`` — the person joins this chat, history included (the owner was told first, in one line,
  what in it is sensitive);
- ``from:<turn>`` — a NEW shared chat carrying the visible text from that turn on;
- ``none`` — a NEW shared chat, empty.

A new chat carries only words a person typed and the agent's visible reply, as plain messages —
never a tool call, a tool result or a composed prompt — and the original stays the owner's alone.
Anyone but the owner adding a person starts a new empty chat: the history is not theirs to give.
Whatever was chosen, the shared-turn rule (``session_access``) holds from the moment a second
person is in: a turn uses only what every participant can read, and no workspace is granted.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Callable, Iterable, Optional

from control_plane import session_sync
from control_plane.session_store import SessionStore

#: How many turns an excerpt may carry.
EXCERPT_MAX_TURNS = 50
EXCERPT_MARK = "(excerpt)"


class ExcerptError(ValueError):
    """The excerpt names a turn that does not exist or has no visible text."""


def needs_fork(store: SessionStore, row: dict, *, kind: str) -> bool:
    """Adding a person to a session only its owner is in: the conversation so far is private."""
    if kind != "human":
        return False
    return len(store.humans(row["id"])) <= 1


def visible_text(turn: dict) -> Optional[tuple[str, str]]:
    """``(role, text)`` a person saw in one history turn, or None. A user turn is the words the
    person typed (``user_text``) — never the stored prompt, which carries composed context. An agent
    turn is its prose; its operations are dropped."""
    role = turn.get("role")
    if role == "user":
        said = turn.get("user_text")
        if isinstance(said, str) and said.strip():
            return "user", said.strip()
        return None
    if role in ("agent", "assistant"):
        text = turn.get("text")
        if isinstance(text, str) and text.strip():
            return "agent", text.strip()
    return None


def excerpt(turns: list[dict], picks: Iterable[int]) -> list[tuple[str, str]]:
    """The chosen turns' visible text, in conversation order."""
    chosen = sorted(set(int(i) for i in picks))
    if len(chosen) > EXCERPT_MAX_TURNS:
        raise ExcerptError(f"an excerpt carries at most {EXCERPT_MAX_TURNS} turns")
    out = []
    for i in chosen:
        if i < 0 or i >= len(turns):
            raise ExcerptError(f"turn {i} is not in this conversation")
        got = visible_text(turns[i])
        if got is None:
            raise ExcerptError(f"turn {i} has no text to share")
        out.append(got)
    return out


def turns_from(turns: list[dict], start: int) -> list[int]:
    """The indices of every turn from ``start`` on that carries visible text."""
    if start < 0 or start >= len(turns):
        raise ExcerptError(f"turn {start} is not in this conversation")
    return [i for i in range(start, len(turns)) if visible_text(turns[i]) is not None]


def private_history(store: SessionStore, row: dict,
                    fallback: "Callable[[], list[dict]] | None" = None) -> list[dict]:
    """The owner's private conversation as history turns: the store's head, else ``fallback`` (the
    workspace layout a thread the store holds no version of yet still lives in)."""
    turns = session_sync.history(store, row)
    if turns is None and fallback is not None:
        turns = fallback()
    return list(turns or [])


def fork(store: SessionStore, row: dict, owner: str, *, title: str = "",
         carried: "list[tuple[str, str]] | None" = None) -> dict:
    """A new session owned by ``owner``, starting empty or with ``carried`` as messages. Nothing of
    ``row`` is copied — not its transcript, its workspaces or its title — and ``row``'s conversation
    is untouched.

    A ROUTINE'S CHAT (a session bound to a flow, ADR-0046 decision 12) keeps its routine: the new
    shared session takes the flow binding and the thread name the routine's runs are posted to, so
    every later run lands in the shared chat; the private session keeps the runs made so far under a
    new name and is no longer bound. The flow itself stays its owner's."""
    flow = row.get("flow")
    if flow:
        name = row["name"]
        keep = f"{name[:100]}.private-{uuid.uuid4().hex[:8]}"
        store.set_name(row["id"], keep)
        store.update(row["id"], flow=None)
        new = store.create(owner, name=name, title=title or row.get("title") or "", flow=flow)
    else:
        new = store.create(owner, title=title)
    for role, text in carried or []:
        who = "agent" if role == "agent" else "me"
        line = f"{EXCERPT_MARK} {who}: {text}"
        store.add_note(new["id"], owner, line[: store.NOTE_MAX])
    return new


# ── what sharing a routine's chat costs it ───────────────────────────────────────────────────────
#: Person-wide reads a shared turn is refused (``shared_conversation``), by the words a person knows.
PRIVATE_READS = (
    ("Gmail", re.compile(r"^(gmail_|mail_|email_)|_gmail_")),
    ("Calendar", re.compile(r"^calendar_|_calendar_")),
    ("your meetings", re.compile(r"meeting|transcript|recording|^bot_|_bot$")),
    ("your connections", re.compile(r"^connection|^connections_|^service_call$")),
)
_WORDS = (
    ("Gmail", re.compile(r"\b(gmail|inbox|e-?mails?|mailbox)\b", re.I)),
    ("Calendar", re.compile(r"\b(calendar|agenda)\b", re.I)),
    ("your meetings", re.compile(r"\b(meetings?|transcripts?|recordings?)\b", re.I)),
)
_MCP_PREFIX = re.compile(r"^mcp__[^_]+(?:_[^_]+)*?__")


def _tool_reads(name: str) -> Optional[str]:
    n = _MCP_PREFIX.sub("", str(name or "")).lower()
    for label, pat in PRIVATE_READS:
        if pat.search(n):
            return label
    return None


def private_reads(files: dict) -> list[str]:
    """What a conversation reads that only its owner may: from the tools its turns called, and from
    what a routine's run is asked to do (its prompt). In the order of ``PRIVATE_READS``."""
    found: set = set()
    for rel, data in (files or {}).items():
        if not (rel.startswith(".claude/projects/") and rel.endswith(".jsonl")):
            continue
        for line in data.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if rec.get("type") == "user" and isinstance(content, str):
                for label, pat in _WORDS:
                    if pat.search(content):
                        found.add(label)
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        got = _tool_reads(b.get("name", ""))
                        if got:
                            found.add(got)
    order = [label for label, _ in PRIVATE_READS]
    return [x for x in order if x in found]


def share_preview(store: SessionStore, row: dict) -> dict:
    """What adding a person to ``row`` would do, for the confirmation the owner reads first: whether
    the owner chooses what of the history the person sees (``forks``: a chat only they are in, with
    ``history`` naming the choices), whether ``row`` is a routine's chat, and the person-wide reads
    its turns have made (``private_reads``) — which the history would show the person, and which a
    shared turn is refused from then on."""
    forks = len(store.humans(row["id"])) <= 1
    routine = None
    flow = str(row.get("flow") or "")
    if flow:
        routine = {"name": flow.split("/", 1)[1] if "/" in flow else flow}
    reads: list = []
    if (routine is not None or forks) and int(row.get("version") or 0) > 0:
        try:
            reads = private_reads(store.load_bundle(row["id"])["files"])
        except Exception:  # noqa: BLE001 — unreadable: the confirmation still names the rule
            reads = []
    out = {"session_id": row["id"], "forks": forks, "routine": routine, "private_reads": reads,
           "participants": len(store.participants(row["id"]))}
    if forks:
        # the owner's choice for a chat only they are in (routers/sessions.py, `history`): what of it
        # the person sees, and what the history holds that only the owner may read
        out["history"] = {"options": ["all", "from:<turn>", "none"], "default": "all",
                          "turns": len(session_sync.history(store, row) or [])}
    return out
