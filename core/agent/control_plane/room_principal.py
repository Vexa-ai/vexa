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
* WHO PAYS: the token's subject is W's OWNER — the workspace pool (Trillion-Frame: a room's turns
  debit the workspace owner's pool), and the account identity validates. The speaker is recorded as
  `on_behalf_of` for attribution and for the write-or-propose rule.

What is NOT here yet, and why: identity does not know a `ws:` principal kind (identity.v1 validates
an account id), so the principal rides the dispatch (`VEXA_PRINCIPAL`) and the unit id, and the
account behind the token is the owner, narrowed by the ceiling and `shared`. Teaching identity the
principal kind is a contract-lane change of its own.

WORKSPACE LINKS live in the workspace's own `policy/` (platform-written, like `members.json`):
`policy/visibility.json` (`{"visibility": "open" | "closed"}`, absent = closed) and
`policy/connections.json` (`[{"workspace", "added_by", "added"}]`). Only an OPEN workspace can be
connected, and a connection that stops being open stops being read — checked at every dispatch.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from control_plane import workspace_membership as membership
from workspaces.shared import workspace_paths as wpaths
from control_plane.workspace_attach import SHARED_ROLE

VISIBILITY_FILE = f"{membership.POLICY_DIR}/visibility.json"
CONNECTIONS_FILE = f"{membership.POLICY_DIR}/connections.json"
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

def _read_json(ws: Path, rel: str, default):
    try:
        raw = wpaths.read_text_inside(Path(ws), rel)
        return json.loads(raw) if raw else default
    except (OSError, ValueError):
        return default


def _write_json(ws: Path, rel: str, value) -> None:
    wpaths.write_text_inside(Path(ws), rel, json.dumps(value, indent=2, sort_keys=True) + "\n")


def visibility(root, workspace: str) -> str:
    """``open`` or ``closed`` (absent = closed). A workspace that is not one has no visibility."""
    try:
        ws = membership._ws_dir(Path(root), workspace)
    except membership.MembershipError:
        return "closed"
    v = _read_json(ws, VISIBILITY_FILE, {})
    return "open" if isinstance(v, dict) and v.get("visibility") == "open" else "closed"


def set_visibility(root, workspace: str, value: str, *, commit_fn=None) -> str:
    if value not in ("open", "closed"):
        raise ValueError("visibility is open or closed")
    ws = membership._ws_dir(Path(root), workspace)
    with membership._ws_lock(workspace):
        _write_json(ws, VISIBILITY_FILE, {"visibility": value, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        membership._commit(commit_fn, ws, f"policy: {workspace} is {value}")
    return value


def connections(root, workspace: str) -> list[dict]:
    try:
        ws = membership._ws_dir(Path(root), workspace)
    except membership.MembershipError:
        return []
    rows = _read_json(ws, CONNECTIONS_FILE, [])
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
    ws = membership._ws_dir(Path(root), workspace)
    with membership._ws_lock(workspace):
        rows = connections(root, workspace)
        if not any(r["workspace"] == other for r in rows):
            if len(rows) >= MAX_CONNECTED:
                raise ValueError(f"at most {MAX_CONNECTED} connected workspaces")
            rows.append({"workspace": other, "added_by": actor,
                         "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            _write_json(ws, CONNECTIONS_FILE, rows)
            membership._commit(commit_fn, ws, f"policy: {workspace} connected to {other}")
    return rows


def disconnect(root, workspace: str, other: str, *, commit_fn=None) -> list[dict]:
    ws = membership._ws_dir(Path(root), workspace)
    with membership._ws_lock(workspace):
        rows = [r for r in connections(root, workspace) if r["workspace"] != other]
        _write_json(ws, CONNECTIONS_FILE, rows)
        membership._commit(commit_fn, ws, f"policy: {workspace} disconnected from {other}")
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

    @property
    def id(self) -> str:
        return principal_id(self.workspace)

    @property
    def unit(self) -> str:
        return room_unit_id(self.workspace, self.session) + ("" if self.writable else READ_UNIT_SUFFIX)

    def ceiling(self) -> list:
        own = self.workspace if self.writable else f"ro:{self.workspace}"
        return [own, *(f"ro:{c}" for c in self.connected)]

    def as_dict(self) -> dict:
        return {"principal": self.id, "workspace": self.workspace, "owner": self.owner,
                "session": self.session, "on_behalf_of": self.on_behalf_of,
                "connected": list(self.connected), "audience": self.audience, "level": self.level,
                "writable": self.writable}


def audience_of(root, workspace: str, members: list, *, cross_org_flag: bool = False,
                is_member: Optional[Callable[[str, str], bool]] = None) -> str:
    """``cross_org`` when the room's readers are not all the workspace's own people — anyone in it who
    is not a member of W (a guest, a reader from an open workspace) — or the room is marked so;
    ``org`` otherwise. Decided from the members, never from what the agent says."""
    if cross_org_flag:
        return AUDIENCE_CROSS_ORG
    check = is_member or (lambda ws, who: membership.is_member(Path(root), ws, who) is not None)
    for who in members or []:
        try:
            if not check(workspace, who):
                return AUDIENCE_CROSS_ORG
        except Exception:  # noqa: BLE001 — a member nobody can place is treated as outside
            return AUDIENCE_CROSS_ORG
    return AUDIENCE_ORG


def resolve(store, root, session_id: str, *, speaker: str = "",
            is_member: Optional[Callable[[str, str], bool]] = None) -> Optional[RoomPrincipal]:
    """The room principal for ``session_id``, or None when it is not a room (or its workspace has no
    owner to pay for it)."""
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
    return RoomPrincipal(workspace=ws, owner=owner, session=session_id, on_behalf_of=speaker,
                         writable=role in WRITER_ROLES,
                         connected=connected_open(root, ws),
                         audience=audience_of(root, ws, members,
                                              cross_org_flag=bool(room.get("cross_org")),
                                              is_member=is_member),
                         level=str(room.get("level") or ""))


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
