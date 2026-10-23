"""shared_chat_migration.py — SHARED CHATS BECOME ROOMS (PRD 0.13.4 D10, §10 Q5; slice S4).

A chat has exactly one person (7hh); two-person conversations are rooms. ADR-0048's shared chats (a
chat with participants) are retired, and the ones that exist migrate ONCE (founder decision 11, the
§10 Q5 recommendation accepted):

* TWO people → their ROOM OF TWO: the chat becomes the pair's room, in their hidden pair workspace
  (FD4). If the pair already has a room, the chat becomes a second, closed room there, named by its
  title, so nothing is merged and nothing is lost.
* THREE OR MORE → a CLOSED room in the workspace the chat writes to (its owner's chat target), with
  exactly the chat's people as explicit members — nobody gains a reader they did not have.
* A chat that writes to no workspace (the owner's desk) with three or more people is NOT moved: its
  owner is told, and decides (it keeps working as it is until they do).

Each person in a migrated chat gets ONE notice saying what it became. History, attachments and the
chat's id are unchanged: the session row stays, it only gains a room row (and registry rows).
``plan`` reports without writing; ``apply`` writes. Idempotent: a session with a room row is skipped.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from control_plane import pair_rooms, room_membership as rm

log = logging.getLogger("agent_api.shared_chat_migration")


def shared_chats(store) -> list[dict]:
    """Every session with more than one person and no room row: ``{id, owner, title, name, people}``."""
    with store._lock:
        rows = store._x("SELECT s.id, s.owner, s.title, s.name FROM agent_session s "
                        "WHERE NOT EXISTS (SELECT 1 FROM agent_session_room r WHERE r.session_id = s.id) "
                        "AND (SELECT COUNT(*) FROM agent_session_participant p "
                        "     WHERE p.session_id = s.id AND p.kind = 'human') > 1").fetchall()
    out = []
    for r in rows:
        people = [p["principal"] for p in store.participants(r[0]) if p["kind"] == "human"]
        out.append({"id": r[0], "owner": r[1], "title": r[2] or "", "name": r[3], "people": people})
    return out


def _decide(chat: dict, target_of: Callable[[str, str], str]) -> dict:
    people = chat["people"]
    if len(people) == 2:
        other = next(p for p in people if p != chat["owner"]) if chat["owner"] in people else people[1]
        return {**chat, "becomes": "room_of_two", "with": other}
    target = ""
    try:
        target = str(target_of(chat["owner"], chat["name"]) or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable target: the owner decides
        target = ""
    if not target or target in (chat["owner"], "desk", "personal"):
        return {**chat, "becomes": "needs_owner",
                "said": "This shared chat writes to no workspace; choose one, then it becomes a room there."}
    return {**chat, "becomes": "closed_room", "workspace": target}


def plan(store, target_of: Callable[[str, str], str]) -> dict:
    items = [_decide(c, target_of) for c in shared_chats(store)]
    return {"chats": items, "counts": {k: sum(1 for i in items if i["becomes"] == k)
                                       for k in ("room_of_two", "closed_room", "needs_owner")}}


def apply(store, root: Path, target_of: Callable[[str, str], str], *,
          notify: Optional[Callable[[list, str, str, str], None]] = None) -> dict:
    """Migrate every shared chat (see the header). ``notify(recipients, actor, session_id, text)``."""
    root = Path(root)
    done = []
    for item in plan(store, target_of)["chats"]:
        sid = item["id"]
        try:
            if item["becomes"] == "room_of_two":
                wid = pair_rooms.ensure_pair_workspace(root, item["owner"], item["with"])
                existing = pair_rooms.find_room(store, wid)
                if existing is None:
                    store.put_room(sid, visibility="open", proactivity="quiet", workspace=wid,
                                   created_by=item["owner"])
                    text = f"“{item['title'] or 'Your shared chat'}” is now your room of two. Nothing in it changed."
                else:
                    store.put_room(sid, visibility="closed", proactivity="quiet", workspace=wid,
                                   created_by=item["owner"])
                    rm.seed(store, root, sid)
                    text = (f"“{item['title'] or 'Your shared chat'}” is now a room beside your room of two. "
                            "Nothing in it changed.")
            elif item["becomes"] == "closed_room":
                store.put_room(sid, visibility="closed", proactivity="quiet", workspace=item["workspace"],
                               created_by=item["owner"])
                rm.seed(store, root, sid)
                text = (f"“{item['title'] or 'Your shared chat'}” is now a closed room — the same people, the "
                        "same history.")
            else:
                if notify is not None:
                    notify([item["owner"]], "vexa", sid,
                           f"“{item['title'] or 'A shared chat'}” could not become a room: it writes to no "
                           "workspace. Choose one and it moves.")
                done.append({**item, "migrated": False})
                continue
        except Exception as exc:  # noqa: BLE001 — one chat failing never stops the rest
            log.warning("shared chat %s not migrated (%s)", sid, type(exc).__name__)
            done.append({**item, "migrated": False, "error": type(exc).__name__})
            continue
        if notify is not None:
            notify(item["people"], "vexa", sid, text)
        done.append({**item, "migrated": True})
    return {"chats": done, "migrated": sum(1 for d in done if d.get("migrated"))}


__all__ = ["apply", "plan", "shared_chats"]
