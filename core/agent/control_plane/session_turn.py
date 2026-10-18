"""session_turn.py — what one chat message does in a session (ADR-0048 decisions 4 and 5), decided
in one place so the chat route only applies it.

``plan_turn`` answers, for a message ``subject`` posts to ``session``:

- may they post at all (the session gate: a participant with a writing role);
- does the agent answer it (decision 5) — if not, the route keeps it as a message every participant
  reads and the agent hears on its next turn;
- what the turn may use (the leak rule): ``scope`` narrows the worker's mounts and the tools'
  ceiling, and ``preamble`` tells the agent who reads the answer, what is withheld, what was said
  since it last answered, and what the speaker chose to share;
- ``gen`` — a suffix for the warm unit's mount generation, so a change of participants (and so of
  the intersection) is served by a worker spawned with the narrowed mounts, never a warm one still
  holding the old set.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from control_plane import session_access, session_sync
from shared import attachments as attachments_mod
from control_plane.session_store import SessionDenied, SessionStore


@dataclass
class TurnPlan:
    row: dict
    speak: bool
    scope: Optional[session_access.SharedScope]
    preamble: str
    gen: str
    humans: list
    #: files posted in messages since the agent last answered — the turn carries them too
    attachments: list = None


def _unheard_block(notes: list[dict], names: dict, files: "list | None" = None) -> str:
    """What was said since the agent last answered. A note's attachments leave its text (so no
    mark is read twice) and are appended to ``files``, which the turn's own mark then carries."""
    if not notes:
        return ""
    lines = ["## Said in this conversation since you last answered", ""]
    for n in notes:
        text = n["text"]
        items = attachments_mod.read(text) or []
        if items:
            text = attachments_mod.strip(text)
            names_ = ", ".join(i["name"] for i in items)
            text = (text + " " if text else "") + f"[attached: {names_} — see the files attached below]"
            if files is not None:
                files.extend(items)
        lines.append(f"- {names.get(n['author'], n['author'])}: {text}")
    return "\n".join(lines) + "\n\n"


def plan_turn(store: SessionStore, *, subject: str, session: str, text: str,
              candidates: Iterable[str], is_member: Callable[[str, str], bool],
              global_root: "Path | None" = None, channel_root: "Path | None" = None,
              names: "dict | None" = None, shared_items: "list[dict] | None" = None,
              speaker_name: str = "", addressed: bool = False,
              is_room: "Callable[[str], bool] | None" = None) -> TurnPlan:
    """``addressed`` — the turn is the agent's by construction (a routine's run, posted by flows
    over the internal tier), so it runs whoever else is in the chat; the leak rule still applies."""
    # A STORE THAT CANNOT ANSWER (S6). Who reads the answer is known only to the store, so a turn
    # whose readers the store cannot name is refused — never run with the speaker's whole reach. The
    # one fall-back is a private thread the store could not even look up: nobody else reads it, and
    # nobody else can address it (others reach a session by its store id).
    try:
        row = session_sync.resolve(store, subject, session)
    except SessionDenied:
        raise
    except Exception as exc:
        if session.startswith(session_sync.STORE_ID_PREFIX):
            raise session_sync.SessionUnavailable(session) from exc
        raise
    if row is None:
        raise SessionDenied("not_a_participant")
    try:
        if not store.can_write(row["id"], subject):
            raise SessionDenied("session_read_only")
        parts = store.participants(row["id"])
    except SessionDenied:
        raise
    except Exception as exc:
        raise session_sync.SessionUnavailable(session) from exc
    humans = [p["principal"] for p in parts if p["kind"] == "human"]
    agents = [p["principal"] for p in parts if p["kind"] == "agent"]
    others = [h for h in humans if h != subject]
    policy = session_access.speaks_policy(global_root, channel_root) if others else "always"
    # A ROOM (rooms feature): a person's message is for the room's sensor, never a turn by itself —
    # whatever the deployment's `session_agent_speaks` says — unless it addresses the agent.
    if is_room is not None and is_room(row["id"]):
        policy = "mentioned"
    speak = addressed or session_access.agent_speaks(text, humans=len(humans), policy=policy,
                                                     agent_names=agents)
    if not speak:
        return TurnPlan(row=row, speak=False, scope=None, preamble="", gen="", humans=humans)
    names = names or {}
    scope = session_access.shared_scope(subject, others=others, candidates=candidates,
                                        is_member=is_member)
    preamble = ""
    gen = ""
    if scope is not None:
        preamble += session_access.leak_preamble(scope, names=names, people=len(humans))
        if row.get("flow"):
            preamble += session_access.ROUTINE_SHARED_NOTE
        preamble += session_access.shared_items_block(shared_items or [],
                                                      speaker=speaker_name or names.get(subject, "The speaker"))
        try:
            unheard = store.take_unheard(row["id"])
        except Exception as exc:
            raise session_sync.SessionUnavailable(session) from exc
        files: list = []
        preamble += _unheard_block(unheard, names, files)
        digest = hashlib.sha1(json.dumps([sorted(scope.allowed), sorted(humans)]).encode()).hexdigest()
        gen = "s" + digest[:8]
        return TurnPlan(row=row, speak=True, scope=scope, preamble=preamble, gen=gen, humans=humans,
                        attachments=files)
    return TurnPlan(row=row, speak=True, scope=scope, preamble=preamble, gen=gen, humans=humans,
                    attachments=[])
