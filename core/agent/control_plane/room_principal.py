"""room_principal.py — a room's turns run as its WORKSPACE's agent, `ws:<id>` (decision C1, 2026-10-16).

Founder-approved model: a room belongs to a workspace W and its agent is W's agent, not whoever spoke.
Its context is W (read-write: the room's write scope, fixed), the OPEN workspaces W is connected to
(read-only), and the owning org's CRM to orient, filtered for the room's audience. Never a member's
personal reach: no desk, no `_system`, no person's mail, calendar or connections.

HOW IT RUNS (agent-api's half of C1)
* ONE WORKER PER ROOM: the unit is `agent-ws-<wid>-room-<session>` — not one per speaker.
* THE MOUNT TABLE IS THE GRANT: `_global` read-only, W read-write, connected open workspaces
  read-only, nothing else (`mounts`). The cwd is the session's scratch folder, as for every shared turn.
* THE TOKEN: the delegation ceiling is exactly that set (`ceiling`: W, `ro:<connected>`), always
  `shared` — so every service refuses the person-wide reads (mail, calendar, connections, meetings)
  — and it carries `audience` (`org` or `cross_org`) so crm-api projects a cross-org room's reads to
  the records' public fields (K1).
* WHO PAYS: `RoomPrincipal.payer` (founder I20) — W's OWNER (Trillion-Frame: a room's turns debit
  the workspace owner's pool), except in a room of two, where whoever started the turn pays. The
  dispatcher admits the turn against the payer's spend cap and meters its usage to the payer. The
  token's subject is W's owner, the account identity validates. The speaker is recorded as
  `on_behalf_of` for attribution and for the write-or-propose rule.

THE GRANT IS RE-READ EVERY TURN, AND A CHANGED GRANT IS A NEW WORKER (D27, H2, 2026-10-17).
`resolve` runs on every turn: the speaker's role, W's roster, its connected open workspaces and the
AUDIENCE — the room's people AND the people on its call — are read afresh. A worker's mounts and
token are fixed when it is spawned, so the unit id carries a digest of that grant (`grant_hash`):
when any of it changes the next turn addresses a different unit, which is spawned with the new
mounts and the new token, and the old one idles out. A guest joining, a call participant from
outside, a member removed or a connection closed never reaches a warm worker minted before it.

CALL PARTICIPANTS COUNT IN THE AUDIENCE (D27). Anyone on the room's live call can hear the answer and
speak into the turn, so each must be placed as a member of W (by account, address or roster name);
one who cannot be placed — or a call whose participants cannot be read — makes it `cross_org`.

THE PRINCIPAL KIND (D2, 0.13.4). identity now keeps a numeric principal of kind `workspace` per
workspace (admin-api `principals`; delegation.v1 `principal_kind`/`principal_workspace`/`on_behalf_of`;
identity.v1 `ValidateDelegation.principal_kind` and `bills_to`), and the gateway signs it into
`x-user-principal-kind`. With `VEXA_ROOM_WS_PRINCIPAL` on, `dispatch.build_unit_env` asks identity for the
workspace's principal (`workspace_principal.resolve`) and mints the room's token for it, so the turn
has no person's account to reach for (identity unreachable: no token). With it off (the 0.13.4 default) the token's subject is still
the owner, narrowed by the ceiling, `shared` and D3's default-deny route table (`route_policy.py`
`SHARED_GATE`), which refuses a shared token every route not declared `shared: allow`.

WORKSPACE LINKS are workspace policy, kept where the roster is: the platform's policy store outside
every tree (`workspace_membership.read_policy` / `write_policy`, PRD 0.13.4 D5) — `visibility`
(`{"visibility": "open" | "closed"}`, absent = closed) and `connections`
(`[{"workspace", "added_by", "added"}]`). The tree's `policy/visibility.json` and
`policy/connections.json` are a mirror nobody reads for access: a contributor who rewrites them opens
nothing. Only an OPEN workspace can be connected, and a connection that stops being open stops being
read — checked at every dispatch. A store that cannot be read answers closed and unconnected (FD3).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from control_plane import workspace_membership as membership
from control_plane.workspace_attach import SHARED_ROLE

VISIBILITY_FILE = membership.VISIBILITY_FILE        # the tree mirror's path (never read for access)
CONNECTIONS_FILE = membership.CONNECTIONS_FILE
PRINCIPAL_PREFIX = "ws:"
MAX_CONNECTED = 20
AUDIENCE_ORG, AUDIENCE_CROSS_ORG = "org", "cross_org"


def principal_id(workspace: str) -> str:
    return f"{PRINCIPAL_PREFIX}{workspace}"


def _token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-")[:120]


#: A READER'S TURN RUNS ON ITS OWN SIBLING (review 2026-10-16): a worker's mounts and grant are
#: fixed when it is spawned, so the room's writable worker never serves a turn whose speaker may
#: only read W. The reader's sibling mounts W read-only and its ceiling is `ro:W`.
READ_UNIT_SUFFIX = "-read"

#: Workspace roles that write W (and so may run the room's agent with W writable).
WRITER_ROLES = ("owner", "contributor")


def room_unit_id(workspace: str, session: str) -> str:
    """The one warm unit a room's turns run on: the workspace's agent, in this room."""
    return f"agent-ws-{_token(workspace)}-room-{_token(session)}"


def is_room_unit(unit_id: str) -> bool:
    return str(unit_id or "").startswith("agent-ws-") and "-room-" in str(unit_id or "")


# ── workspace links (policy/) ──────────────────────────────────────────────────────────────────

def _policy(root, workspace: str, name: str, default):
    """One policy value from the store, or ``default`` — for a workspace that is not one, and (fail
    closed, FD3) for a store that cannot be read: closed, no connections."""
    try:
        membership._ws_dir(Path(root), workspace)
        return membership.read_policy(Path(root), workspace, name, default)
    except membership.MembershipError:
        return default


def visibility(root, workspace: str) -> str:
    """``open`` or ``closed`` (absent = closed). A workspace that is not one has no visibility."""
    v = _policy(root, workspace, "visibility", {})
    return "open" if isinstance(v, dict) and v.get("visibility") == "open" else "closed"


def set_visibility(root, workspace: str, value: str, *, commit_fn=None) -> str:
    if value not in ("open", "closed"):
        raise ValueError("visibility is open or closed")
    membership._ws_dir(Path(root), workspace)
    with membership._ws_lock(workspace):
        membership.write_policy(Path(root), workspace, "visibility",
                                {"visibility": value, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                commit_fn=commit_fn, message=f"policy: {workspace} is {value}")
    return value


def connections(root, workspace: str) -> list[dict]:
    rows = _policy(root, workspace, "connections", [])
    return [r for r in rows if isinstance(r, dict) and r.get("workspace")] if isinstance(rows, list) else []


def org_of(root, workspace: str) -> str:
    """The organisation a workspace belongs to, from its own ``.vexa/workspace.json`` (the default
    org when it names none — one org per install today)."""
    from control_plane import orgs as _orgs  # noqa: PLC0415
    try:
        ws = membership._ws_dir(Path(root), workspace)
        from workspaces.shared.workspace_id import read_workspace_json  # noqa: PLC0415
        data = read_workspace_json(Path(ws)) or {}
        return str(data.get("org") or _orgs.DEFAULT_ORG_ID)
    except (OSError, ValueError, membership.MembershipError):
        return _orgs.DEFAULT_ORG_ID


def connect(root, workspace: str, other: str, *, actor: str, commit_fn=None) -> list[dict]:
    """Connect ``other`` (an OPEN workspace of the same organisation) to ``workspace``: the room agent
    reads it, read-only. Open means open to its own organisation; another org's workspace is never
    connected, whatever its visibility."""
    if other == workspace:
        raise ValueError("a workspace is not connected to itself")
    if visibility(root, other) != "open" or org_of(root, other) != org_of(root, workspace):
        raise PermissionError("only an open workspace of your organisation can be connected")
    membership._ws_dir(Path(root), workspace)
    with membership._ws_lock(workspace):
        rows = connections(root, workspace)
        if not any(r["workspace"] == other for r in rows):
            if len(rows) >= MAX_CONNECTED:
                raise ValueError(f"at most {MAX_CONNECTED} connected workspaces")
            rows.append({"workspace": other, "added_by": actor,
                         "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            membership.write_policy(Path(root), workspace, "connections", rows, commit_fn=commit_fn,
                                    message=f"policy: {workspace} connected to {other}")
    return rows


def disconnect(root, workspace: str, other: str, *, commit_fn=None) -> list[dict]:
    membership._ws_dir(Path(root), workspace)
    with membership._ws_lock(workspace):
        rows = [r for r in connections(root, workspace) if r["workspace"] != other]
        membership.write_policy(Path(root), workspace, "connections", rows, commit_fn=commit_fn,
                                message=f"policy: {workspace} disconnected from {other}")
    return rows


def connected_open(root, workspace: str) -> list[str]:
    """The connected workspaces that are STILL open — what the room agent reads, re-checked each time."""
    mine = org_of(root, workspace)
    return [r["workspace"] for r in connections(root, workspace)
            if visibility(root, r["workspace"]) == "open" and org_of(root, r["workspace"]) == mine]


def owner_of(root, workspace: str) -> str:
    try:
        rows = membership.read_members(Path(root), workspace)
    except membership.MembershipError:
        return ""
    return next((str(m.get("subject")) for m in rows if m.get("role") == "owner" and m.get("subject")), "")


# ── the principal ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RoomPrincipal:
    workspace: str
    owner: str
    session: str
    on_behalf_of: str = ""
    connected: list = field(default_factory=list)
    audience: str = AUDIENCE_ORG
    level: str = ""          # the room's own model (a catalog id), "" = the deployment's default
    writable: bool = True    # the speaker writes W (owner/contributor); a reader's turn reads only
    roster: tuple = ()       # W's members as "subject:role", sorted — part of the grant (D27)
    participants: tuple = () # who is on the room's live call, as read this turn (D27)
    pair: bool = False       # a ROOM OF TWO (S4 pair workspace, two owners) — billing only, never a grant

    @property
    def payer(self) -> str:
        """Who this turn bills (founder I20): the workspace owner — except in a room of two, whose
        two owners are equal, where the person who started the turn (or the call) pays."""
        return str(self.on_behalf_of) if (self.pair and self.on_behalf_of) else str(self.owner)

    @property
    def grant_hash(self) -> str:
        """A digest of everything this turn's worker is granted at spawn — its mounts, its token's
        ceiling and audience, the roster behind them. Any change is a different unit (D27)."""
        grant = {"workspace": self.workspace, "owner": self.owner, "writable": bool(self.writable),
                 "connected": sorted(self.connected), "audience": self.audience,
                 "roster": sorted(self.roster)}
        return hashlib.sha256(json.dumps(grant, sort_keys=True).encode()).hexdigest()[:10]

    @property
    def id(self) -> str:
        return principal_id(self.workspace)

    @property
    def unit(self) -> str:
        return (room_unit_id(self.workspace, self.session) + f"-g{self.grant_hash}"
                + ("" if self.writable else READ_UNIT_SUFFIX))

    def ceiling(self) -> list:
        own = self.workspace if self.writable else f"ro:{self.workspace}"
        return [own, *(f"ro:{c}" for c in self.connected)]

    def as_dict(self) -> dict:
        return {"principal": self.id, "workspace": self.workspace, "owner": self.owner,
                "session": self.session, "on_behalf_of": self.on_behalf_of,
                "connected": list(self.connected), "audience": self.audience, "level": self.level,
                "writable": self.writable, "grant": self.grant_hash}


def _placeable(rows: list) -> set:
    """Every spelling a W member answers to: account id, address, roster name (case-folded)."""
    out = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        for k in ("subject", "email", "name", "display_name"):
            v = " ".join(str(r.get(k) or "").split()).casefold()
            if v:
                out.add(v)
    return out


def audience_of(root, workspace: str, members: list, *, cross_org_flag: bool = False,
                is_member: Optional[Callable[[str, str], bool]] = None,
                participants: Optional[list] = None, roster: Optional[list] = None) -> str:
    """``cross_org`` when the room's readers are not all the workspace's own people — anyone in it who
    is not a member of W (a guest, a reader from an open workspace), anyone on its live call who
    cannot be placed as a member (D27) — or the room is marked so; ``org`` otherwise. Decided from
    the members and the call, never from what the agent says. ``participants=None`` means no call;
    an UNKNOWN call is passed as ``["?"]`` by `resolve`, which nobody can place."""
    if cross_org_flag:
        return AUDIENCE_CROSS_ORG
    if participants:
        if roster is None:
            try:
                roster = membership.read_members(Path(root), workspace)
            except Exception:  # noqa: BLE001 — a roster nobody can read places nobody
                roster = []
        known = _placeable(roster)
        for who in participants:
            if " ".join(str(who or "").split()).casefold() not in known:
                return AUDIENCE_CROSS_ORG
    check = is_member or (lambda ws, who: membership.is_member(Path(root), ws, who) is not None)
    for who in members or []:
        try:
            if not check(workspace, who):
                return AUDIENCE_CROSS_ORG
        except Exception:  # noqa: BLE001 — a member nobody can place is treated as outside
            return AUDIENCE_CROSS_ORG
    return AUDIENCE_ORG


def call_participants_of(row: Optional[dict]) -> Optional[list]:
    """Who is on a call, from its meetings row (``data.participants``, names or ``{name}``), the
    bot's own name left out. ``None`` when the row cannot be read — an unknown call."""
    if not isinstance(row, dict):
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    raw = data.get("participants")
    if not isinstance(raw, list):
        return None
    bot = " ".join(str(data.get("bot_name") or "").split()).casefold()
    out = []
    for x in raw:
        name = str((x.get("name") or x.get("email") or "") if isinstance(x, dict) else x or "").strip()
        if name and " ".join(name.split()).casefold() != bot:
            out.append(name)
    return out


def _live_call_participants(store, session_id: str,
                            call_lookup: Optional[Callable[[str], Optional[dict]]]) -> Optional[list]:
    """The participants of the room's live call; ``None`` when no call is held; ``["?"]`` (nobody
    can place it) when a call is held but who is on it cannot be read — fail closed (D27)."""
    try:
        call = store.active_call(session_id) if hasattr(store, "active_call") else None
    except Exception:  # noqa: BLE001 — a call store that cannot answer: an unknown call
        return ["?"]
    if not call:
        return None
    if call_lookup is None or not call.get("meeting_id"):
        return ["?"]
    try:
        people = call_participants_of(call_lookup(str(call["meeting_id"])))
    except Exception:  # noqa: BLE001
        people = None
    return ["?"] if people is None else people


def resolve(store, root, session_id: str, *, speaker: str = "",
            is_member: Optional[Callable[[str, str], bool]] = None,
            call_lookup: Optional[Callable[[str], Optional[dict]]] = None,
            participants: Optional[list] = None) -> Optional[RoomPrincipal]:
    """The room principal for ``session_id``, or None when it is not a room (or its workspace has no
    owner to pay for it). Read afresh on every turn (D27). ``participants`` names who is on the
    call when the caller already holds them; else the room's live call is read through
    ``call_lookup(meeting_id) -> meetings row``."""
    room = store.room(session_id) if store is not None else None
    if not room or not room.get("workspace"):
        return None
    ws = str(room["workspace"])
    owner = owner_of(root, ws)
    if not owner:
        return None
    try:
        members = store.humans(session_id)
    except Exception:  # noqa: BLE001
        members = []
    try:
        role = membership.is_member(Path(root), ws, str(speaker)) if speaker else None
    except Exception:  # noqa: BLE001 — a role nobody can read writes nothing
        role = None
    try:
        rows = membership.read_members(Path(root), ws)
    except Exception:  # noqa: BLE001
        rows = []
    if participants is None:
        participants = _live_call_participants(store, session_id, call_lookup)
    try:
        pair = bool(membership.is_pair(Path(root), ws))
    except Exception:  # noqa: BLE001 — unknown reads as an ordinary room: the owner pays
        pair = False
    return RoomPrincipal(workspace=ws, owner=owner, session=session_id, on_behalf_of=speaker,
                         writable=role in WRITER_ROLES, pair=pair,
                         connected=connected_open(root, ws),
                         audience=audience_of(root, ws, members,
                                              cross_org_flag=bool(room.get("cross_org")),
                                              is_member=is_member, participants=participants,
                                              roster=rows),
                         level=str(room.get("level") or ""),
                         roster=tuple(sorted(f"{r.get('subject')}:{r.get('role')}" for r in rows
                                             if isinstance(r, dict) and r.get("subject"))),
                         participants=tuple(participants or ()))


def mounts(root, base: list[dict], principal: RoomPrincipal) -> list[dict]:
    """The room's mount table: `_global` (read-only, from the base stack), W read-write, the
    connected open workspaces read-only — and nothing else (no desk, no `_system`)."""
    root = Path(root)
    out = [{**m, "write": False, "primary": False} for m in base if m.get("role") == "global"]
    out.append({"slug": principal.workspace, "path": str(root / principal.workspace), "role": SHARED_ROLE,
                "write": bool(principal.writable), "primary": False})
    for c in principal.connected:
        out.append({"slug": c, "path": str(root / c), "role": SHARED_ROLE, "write": False, "primary": False})
    return out
