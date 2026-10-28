"""room_membership.py — WHO IS IN A ROOM (PRD 0.13.4 D36, slice S4).

ONE AUTHORITY, TWO SHAPES:

* an OPEN room's members are DERIVED from its workspace's membership. Being a member of the
  workspace is being a member of the room; leaving the workspace is leaving its open rooms, with no
  row anywhere to forget. The room's maker stays its owner while they are in the workspace.
* a CLOSED room's members are EXPLICIT rows ``room:<session id>`` in the identity registry's
  ``memberships`` table (S1-A's store, ``workspace_membership.room_store_*``; the file backend keeps
  ``<root>/.rooms/<id>.json`` without identity). Nothing derives them, but a person's row counts
  only while they are a member of the room's workspace: out of the workspace is out of every room in
  it, closed ones included. ``follow_workspace`` drops the rows of people the workspace no longer
  has, so the members panel and the ring list never name them; the check in ``resolve_role`` is the
  authority and holds in the moment before that runs. Coming back to the workspace brings back its
  open rooms; a closed room takes them back when one of its members adds them again.

A room of two (FD4) is an OPEN room in its hidden two-member pair workspace, so its two people are
derived like any open room's, and a third person is refused at the workspace (``pair_rooms``).

The session store's participant rows stay as the PROJECTION: per-person read marks, the rail's
listing, and "who has opened this room" for ringing. They are never the authority for a room:
``install`` puts ``resolve_role`` in front of ``SessionStore.role_of`` for every session that has a
room row, so every existing ``can_read`` / ``can_write`` check asks this module.

FAIL CLOSED (FD3): a roster or a room row set that cannot be read answers no membership — never the
projection's guess. Migration: a closed room with no registry rows yet adopts its participant rows
once (while ``VEXA_POLICY_LEGACY_READ`` is on, as S1-A's policy store does), then the rows decide.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from control_plane import workspace_membership as m

log = logging.getLogger("agent_api.room_membership")

#: A workspace role → the room role it gives in that workspace's open rooms. Every member TAKES PART
#: (posts, is rung); what the room's agent may write for them is the workspace role's business — a
#: reader's turn runs read-only on its own worker (``room_principal``), never as a writer.
ROOM_ROLE_OF_WS = {"owner": "member", "contributor": "member", "viewer": "member"}
#: TUNING LINE (D36, R12) — ringing a DERIVED member on a room call: only members who opened the room
#: in the last N days, so an org-wide open workspace does not ring everyone. A closed room's members
#: are always rung. Override per deployment with ``VEXA_ROOM_RING_DERIVED_DAYS``.
RING_DERIVED_DAYS_DEFAULT = 30
#: The room row kinds a store may hold; "direct" is the pre-0.13.4 spelling of a two-person room.
EXPLICIT = ("closed", "direct")


def ring_derived_days() -> int:
    try:
        return max(0, int(os.environ.get("VEXA_ROOM_RING_DERIVED_DAYS", RING_DERIVED_DAYS_DEFAULT)))
    except ValueError:
        return RING_DERIVED_DAYS_DEFAULT


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def is_open(room: Optional[dict]) -> bool:
    return bool(room) and room.get("visibility") == "open" and bool(room.get("workspace"))


# ── closed rooms: the registry's rows ───────────────────────────────────────────────────────────

def _legacy_read() -> bool:
    return m._legacy_read()                                    # S1-A's one switch for adoption


def explicit_rows(store, root: Path, session_id: str) -> list[dict]:
    """A CLOSED room's member rows from the registry. A room with none yet adopts, once (legacy read
    on), the rows the interim file store kept for it, else its participant rows. Raises
    ``MembershipError`` 503 when the store cannot answer."""
    rows = m.room_store_get(root, session_id)
    if rows is not None:
        return [r for r in rows if isinstance(r, dict) and r.get("subject")]
    if not _legacy_read():
        return []
    adopted = m.legacy_room_rows(root, session_id)
    if adopted is None:
        adopted = [{"subject": p["principal"], "role": p["role"], "kind": p.get("kind") or "human",
                    "added_by": "migration", "added_at": _now_iso()} for p in store.participants(session_id)]
    if adopted:
        m.room_store_put(root, session_id, adopted)
        log.info("room membership: adopted %d participant rows for %s", len(adopted), session_id)
    return adopted


def seed(store, root: Path, session_id: str, *, keep: Optional[Callable[[str], bool]] = None) -> list[dict]:
    """Write a closed room's rows from its participant projection (a room just made closed, or an open
    room being closed: ``keep`` filters who stays). Returns the rows written."""
    rows = [{"subject": p["principal"], "role": p["role"], "kind": p.get("kind") or "human",
             "added_by": store.get(session_id)["owner"] if store.get(session_id) else "", "added_at": _now_iso()}
            for p in store.participants(session_id) if keep is None or keep(p["principal"]) or p["role"] == "owner"]
    m.room_store_put(root, session_id, rows)
    return rows


def forget(root: Path, session_id: str) -> None:
    """An open room keeps no rows (it is derived): drop any the registry holds."""
    try:
        m.room_store_drop(root, session_id)
    except m.MembershipError as exc:
        log.warning("room rows not dropped for %s: %s", session_id, exc)


# ── the authority ───────────────────────────────────────────────────────────────────────────────

def resolve_role(store, session_id: str, principal: str, row_role: Optional[str], *, root: Path) -> Optional[str]:
    """The room role of ``principal`` in ``session_id`` (``owner`` · ``member`` · ``viewer``) or None.
    Raises when the authority cannot be read; ``SessionStore.role_of`` turns that into None."""
    room = store.room(session_id)
    if room is None:
        return row_role
    if is_open(room):
        ws_role = m.is_member(root, room["workspace"], str(principal))
        if ws_role is None:
            return None                                        # out of the workspace: out of its rooms
        if row_role == "owner":
            return "owner"
        return ROOM_ROLE_OF_WS.get(m.normalize_role(ws_role), "viewer")
    for r in explicit_rows(store, root, session_id):
        if str(r.get("subject")) == str(principal):
            if not _in_workspace(root, room, r):
                return None                                    # out of the workspace: out of its rooms
            return str(r.get("role") or "member")
    return None


def _in_workspace(root: Path, room: dict, row: dict, roster: Optional[set] = None) -> bool:
    """Whether a closed room's row still counts: its person is a member of the room's workspace. An
    agent's row and a room with no workspace are the rows' own. Raises when the roster cannot be
    read (FD3: the caller answers no membership)."""
    ws = str(room.get("workspace") or "")
    if not ws or (row.get("kind") or "human") != "human":
        return True
    who = str(row.get("subject") or "")
    if roster is not None:
        return who in roster
    return m.is_member(root, ws, who) is not None


def _roster(root: Path, room: dict) -> Optional[set]:
    ws = str(room.get("workspace") or "")
    if not ws:
        return None
    return {str(x.get("subject")) for x in m.read_members(root, ws) if isinstance(x, dict) and x.get("subject")}


def members(store, root: Path, session_id: str) -> list[dict]:
    """Everyone in the room, from the authority: ``{principal, role, kind, derived, added, opened}``.
    ``opened`` is when this person last read the room (or joined it), from the projection; None when
    they never opened it. Raises ``MembershipError`` when the authority cannot be read."""
    room = store.room(session_id)
    rows = {p["principal"]: p for p in store.participants(session_id)}
    out: list[dict] = []

    def _opened(who: str):
        p = rows.get(who)
        if p is None:
            return None
        return float(p.get("read_at") or p.get("added") or 0) or None

    if is_open(room):
        for mem in m.read_members(root, room["workspace"]):
            who = str(mem.get("subject") or "")
            if not who:
                continue
            p = rows.get(who)
            role = "owner" if (p and p["role"] == "owner") else ROOM_ROLE_OF_WS.get(
                m.normalize_role(mem.get("role")), "viewer")
            out.append({"principal": who, "role": role, "kind": "human", "derived": True,
                        "added": (p or {}).get("added"), "opened": _opened(who)})
        for who, p in rows.items():                            # agents addressed by name stay listed
            if p.get("kind") == "agent":
                out.append({"principal": who, "role": p["role"], "kind": "agent", "derived": False,
                            "added": p.get("added"), "opened": None})
        return out
    roster = _roster(root, room) if room else None
    for r in explicit_rows(store, root, session_id):
        if room and not _in_workspace(root, room, r, roster):
            continue
        who = str(r["subject"])
        out.append({"principal": who, "role": str(r.get("role") or "member"), "kind": r.get("kind") or "human",
                    "derived": False, "added": (rows.get(who) or {}).get("added"), "opened": _opened(who),
                    **({"added_by": r["added_by"]} if r.get("added_by") else {})})
    return out


def humans(store, root: Path, session_id: str) -> list[str]:
    return [x["principal"] for x in members(store, root, session_id) if x["kind"] == "human"]


def add(store, root: Path, session_id: str, principal: str, *, actor: str, role: str = "member") -> bool:
    """Make ``principal`` a member. An open room records only that they were brought in (the
    projection row — they were already a member by the workspace); a closed room writes its registry
    row first, then the projection. True when they were not a member before."""
    if role not in ("member", "viewer"):
        raise ValueError("a room member is a member or a viewer")
    room = store.room(session_id)
    if room is None:
        raise ValueError("not a room")
    before = store.role_of(session_id, principal)
    if not is_open(room):
        rows = explicit_rows(store, root, session_id)
        if not any(str(r.get("subject")) == str(principal) for r in rows):
            rows = rows + [{"subject": str(principal), "role": role, "kind": "human",
                            "added_by": str(actor), "added_at": _now_iso()}]
            m.room_store_put(root, session_id, rows)
    store.join(session_id, str(principal))
    return before is None


def remove(store, root: Path, session_id: str, principal: str) -> bool:
    """Take ``principal`` out of a CLOSED room (registry first, then the projection). An open room's
    member leaves by leaving the workspace; here only their projection row goes."""
    room = store.room(session_id)
    if room is None:
        return False
    removed = False
    if not is_open(room):
        rows = explicit_rows(store, root, session_id)
        keep = [r for r in rows if str(r.get("subject")) != str(principal)]
        if len(keep) != len(rows):
            m.room_store_put(root, session_id, keep)
            removed = True
    with store._tx():
        n = store._x("DELETE FROM agent_session_participant WHERE session_id = ? AND principal = ?",
                     (session_id, str(principal)))
    return removed or bool(getattr(n, "rowcount", 0))


def set_owner(store, root: Path, session_id: str, old: str, new: str) -> None:
    """Mirror a hand-on into a closed room's rows (the projection already moved)."""
    room = store.room(session_id)
    if room is None or is_open(room):
        return
    rows = explicit_rows(store, root, session_id)
    for r in rows:
        if str(r.get("subject")) == str(old):
            r["role"] = "member"
        elif str(r.get("subject")) == str(new):
            r["role"] = "owner"
    m.room_store_put(root, session_id, rows)


def follow_workspace(store, root: Path, workspace: str) -> list[tuple[str, str]]:
    """Drop, from every closed room of ``workspace``, the rows of people who are no longer its members
    (registry first, then the projection) — told after the workspace's roster changed. A room whose
    owner went passes to its longest-standing remaining member, as when an owner leaves. Returns the
    ``(room, person)`` pairs dropped. A roster that cannot be read drops nothing: ``resolve_role``
    already answers no membership for anyone it cannot place."""
    root = Path(root)
    if store is None or not workspace:
        return []
    try:
        roster = {str(x.get("subject")) for x in m.read_members(root, workspace)
                  if isinstance(x, dict) and x.get("subject")}
        rooms_ = store.closed_rooms(workspace)
    except Exception as exc:  # noqa: BLE001 — the authority check still holds; only the tidy-up waits
        log.warning("closed rooms of %s not reconciled (%s)", workspace, type(exc).__name__)
        return []
    dropped: list[tuple[str, str]] = []
    for sid in rooms_:
        room = {"workspace": workspace}
        try:
            rows = explicit_rows(store, root, sid)
            keep = [r for r in rows if _in_workspace(root, room, r, roster)]
            gone = [str(r["subject"]) for r in rows if r not in keep]
            if not gone:
                continue
            heir = ""
            if any(str(r.get("role")) == "owner" for r in rows if r not in keep) and \
                    not any(str(r.get("role")) == "owner" for r in keep):
                humans_left = sorted((r for r in keep if (r.get("kind") or "human") == "human"),
                                     key=lambda r: str(r.get("added_at") or ""))
                if humans_left:
                    heir = str(humans_left[0]["subject"])
                    for r in keep:
                        if str(r["subject"]) == heir:
                            r["role"] = "owner"
            m.room_store_put(root, sid, keep)
            with store._tx():
                for who in gone:
                    store._x("DELETE FROM agent_session_participant WHERE session_id = ? AND principal = ?",
                             (sid, who))
                if heir:
                    store._x("UPDATE agent_session_participant SET role = 'owner' "
                             "WHERE session_id = ? AND principal = ?", (sid, heir))
                    store._x("UPDATE agent_session SET owner = ? WHERE id = ?", (heir, sid))
            dropped.extend((sid, who) for who in gone)
            log.info("room %s: %d member(s) left with workspace %s%s", sid, len(gone), workspace,
                     f"; handed on to {heir}" if heir else "")
        except Exception as exc:  # noqa: BLE001 — one room failing never stops the rest
            log.warning("closed room %s not reconciled (%s)", sid, type(exc).__name__)
    return dropped


def ringable(store, root: Path, session_id: str, *, exclude: str = "", now: Optional[float] = None) -> list[str]:
    """WHO A ROOM CALL RINGS (sharing-tagging §7, D36): every member of a closed room; in an open room
    the derived members who opened it within ``ring_derived_days`` (the tuning line). Never ``exclude``
    (the caller). Fails closed to nobody."""
    try:
        room = store.room(session_id)
        mem = members(store, root, session_id)
    except Exception as exc:  # noqa: BLE001 — a roster that cannot be read rings nobody
        log.warning("ring list unreadable for %s (%s)", session_id, type(exc).__name__)
        return []
    now = float(now if now is not None else time.time())
    horizon = now - ring_derived_days() * 86400
    out = []
    for x in mem:
        if x["kind"] != "human" or x["principal"] == str(exclude):
            continue
        if is_open(room) and (x.get("opened") is None or float(x["opened"]) < horizon):
            continue
        out.append(x["principal"])
    return out


def install(store, root_of: Callable[[], Path]) -> None:
    """Put this module in front of ``store.role_of`` for rooms (agent-api's composition root)."""
    if store is None:
        return

    def _resolver(st, session_id, principal, row_role):
        return resolve_role(st, session_id, principal, row_role, root=Path(root_of()))

    store.set_room_role(_resolver)
    store._room_members = lambda sid: members(store, Path(root_of()), sid)


__all__ = ["add", "explicit_rows", "follow_workspace", "forget", "humans", "install", "is_open", "members", "remove",
           "resolve_role", "ring_derived_days", "ringable", "seed", "set_owner"]
