"""pair_rooms.py — ROOMS OF TWO (PRD 0.13.4 FD4, UI 7hh, slice S4).

"Message <person>" opens the room of two with that person, creating it once; the same pair always
lands in the same room. A room of two is a ROOM (§16): shared, holding neither person's private data,
run by its workspace's principal. FD4 (founder, 2026-10-17): it is backed by a HIDDEN two-member PAIR
WORKSPACE, so a room of two is nothing new — an open room whose workspace has exactly two members.

The specifics (review SF-11):
* both people are OWNERS of the pair workspace; ``bills_to`` is the person who opened it (the room's
  ``created_by``; the workspace principal's payer, S1-B);
* a THIRD member is refused (``assert_not_pair`` at every grant door that could add one);
* a pair can be opened with anyone who has an account; a person without one gets an invite instead
  (the tag flow, D21);
* the pair workspace is HIDDEN: it is never in the membership index, never in a person's workspace
  list or mounts (``workspace_membership.is_pair`` filters the enumeration), only in their Rooms.

The pair's id is DETERMINISTIC (``pair-`` + a digest of the two account ids, ordered), so the same two
people can never get two rooms by racing each other.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Optional

from control_plane import room_membership as rm
from control_plane import workspace_membership as m
from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger("agent_api.pair_rooms")

PAIR_PREFIX = "pair-"
KIND = "pair"


class _NoIndex:
    """A pair is never put in the membership index: the index feeds mounts and workspace lists."""

    def add(self, *_a, **_k) -> None:
        return None

    def remove(self, *_a, **_k) -> None:
        return None

    def list(self, _subject: str) -> list:
        return []


NO_INDEX = _NoIndex()


def pair_id(a: str, b: str) -> str:
    x, y = sorted((str(a), str(b)))
    return PAIR_PREFIX + hashlib.sha256(f"{x}\x00{y}".encode()).hexdigest()[:20]


def _make_dir(root: Path, wid: str) -> None:
    """The pair workspace's tree: a bare git repo (nothing is seeded — it holds what the two put there)."""
    from shared.seeding import seed_workspace  # noqa: PLC0415
    target = Path(root) / wid
    if target.exists():
        return
    staged = Path(root) / f".staging-pair-{wid}"
    if staged.exists():
        wpaths.remove_tree(staged)
    seed_workspace(staged, None)
    shutil.move(str(staged), str(target))


def ensure_pair_workspace(root: Path, opener: str, other: str, *, opener_email: Optional[str] = None,
                          commit_fn=None) -> str:
    """The pair workspace of ``opener`` and ``other``: made once (both owners, kind ``pair``), reused
    after. Returns its id."""
    if not opener or not other or str(opener) == str(other):
        raise ValueError("a room of two is two different people")
    wid = pair_id(opener, other)
    root = Path(root)
    with m._ws_lock(wid):
        _make_dir(root, wid)
        roster = m.read_policy(root, wid, "members", [])
        have = {str(r.get("subject")) for r in roster if isinstance(r, dict)}
        if not {str(opener), str(other)} <= have:
            m.ensure_owner(root, wid, str(opener), index=NO_INDEX, email=opener_email, commit_fn=commit_fn)
            roster = m.read_policy(root, wid, "members", [])
            if str(other) not in {str(r.get("subject")) for r in roster}:
                roster.append({"subject": str(other), "role": "owner", "added_by": str(opener),
                               "added_at": m._now_iso()})
                m.write_policy(root, wid, "members", roster, commit_fn=commit_fn,
                               message=f"policy: room of two {wid}")
        if not is_pair(root, wid):
            m.store_put(root, wid, "kind", {"kind": KIND, "people": sorted([str(opener), str(other)]),
                                            "bills_to": str(opener)})
    return wid


def is_pair(root: Path, workspace_id: str) -> bool:
    return m.is_pair(root, workspace_id)


def assert_not_pair(root: Path, workspace_id: str) -> None:
    """A third person is never added to a pair workspace (FD4, SF-11)."""
    if m.is_pair(root, workspace_id):
        raise m.MembershipError("a room of two has exactly two people; start a room in a workspace "
                                "to talk with more", status=409)


def find_room(store, workspace_id: str) -> Optional[str]:
    """The pair's one room (the oldest open room in its workspace), or None."""
    rows = store.open_rooms(workspace_id)
    return rows[-1]["id"] if rows else None


def open_room(store, root: Path, opener: str, other: str, *, title: str = "",
              opener_email: Optional[str] = None, commit_fn=None) -> tuple[str, bool]:
    """FIND OR CREATE the room of two. Returns ``(session_id, created)``. Both people get a projection
    row (so a call rings both, and the room is in both rails)."""
    wid = ensure_pair_workspace(root, opener, other, opener_email=opener_email, commit_fn=commit_fn)
    with m._ws_lock(f"{wid}:room"):
        sid = find_room(store, wid)
        if sid is not None:
            store.join(sid, str(opener))
            store.join(sid, str(other))
            return sid, False
        row = store.create(str(opener), title=title or "", workspaces=[wid])
        store.put_room(row["id"], visibility="open", proactivity="quiet", workspace=wid, created_by=str(opener))
        store.join(row["id"], str(other))
        return row["id"], True


def people(root: Path, workspace_id: str) -> list[str]:
    return [str(r.get("subject")) for r in m.read_members(root, workspace_id) if r.get("subject")]


def is_pair_room(store, root: Path, session_id: str) -> bool:
    room = store.room(session_id)
    return bool(room) and bool(room.get("workspace")) and m.is_pair(root, room["workspace"])


def peer_name(store, root: Path, session_id: str, viewer: str, index_list) -> Optional[str]:
    """WHAT A ROOM OF TWO IS CALLED, FOR ONE OF ITS TWO PEOPLE: the other person's name (UI 7ii,
    humans by name) — the name the rail gives the same room (``people_directory.name_of``), else the
    one sentences use (``front_page.display_name``). None for any other session, for a room whose
    other person is not known yet, and when no name can be read."""
    try:
        if not is_pair_room(store, root, session_id):
            return None
        other = next((str(h) for h in store.humans(session_id) if str(h) != str(viewer)), None)
        if not other:
            return None
        from control_plane import front_page, people_directory  # noqa: PLC0415 — module-load order
        return (people_directory.name_of(Path(root), other, index_list)
                or front_page.display_name(Path(root), other) or None)
    except Exception:  # noqa: BLE001 — a room without its person's name keeps its own label
        log.warning("room of two: the other person's name could not be read for %s", session_id)
        return None


__all__ = ["KIND", "NO_INDEX", "PAIR_PREFIX", "assert_not_pair", "ensure_pair_workspace", "find_room",
           "is_pair", "is_pair_room", "open_room", "pair_id", "peer_name", "people"]
