"""workspace_id.py — the immutable identity of a workspace, stored INSIDE it.

PRD decision 26.1 (founder, 2026-09-02): *"Hash ID to every workspace? workspaces interconnected
together. If a workspace is not available, it's okay — by design."*

A workspace is renamed, promoted from private to shared, un-shared back, parked under a slug,
cloned from a repo and swapped in again. Every one of those moves changes its *slug* and its
*directory*, and until now a link into it was written against one of those two. So a link broke
whenever the workspace it pointed at was administered — which is the ordinary case, not the edge.

The fix is one line: **the id is a fact about the workspace, so it lives in the workspace.**
``.vexa/workspace.json`` travels with the tree through every move git and ``shutil.move`` can make,
including a clone: a repo somebody attaches that already carries the file KEEPS its id, which is
exactly what makes an attached repo *the same workspace* rather than a new one that looks like it.
The server registry (``control_plane/workspace_ids.py``) is the *derived* half — id → where it is
now — and it is rebuildable from the files by walking the root. The file is authoritative for the
same reason ``policy/members.json`` is: it survives a store loss and it can be read offline.

WHY 10 CHARS OF BASE32. The id is written by hand into prose (``[[ws:k4m9x2q7bd/olga-avramenko]]``)
and read out of a URL, so it has to be short enough to type and unambiguous enough to read back.
Ten characters of the lowercase RFC-4648 alphabet is 50 bits — birthday-safe past any number of
workspaces a deployment will ever hold — with no case to get wrong and no ``0/O`` or ``1/l`` pair,
because the alphabet ``a-z2-7`` contains neither digit.

DELIBERATELY PURE. No redis, no HTTP, no git: the same code serves the control plane, the worker
that has to know the ids of its own mounts, and the offline replay. Committing is the caller's.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Optional

# Where the id lives inside the workspace. Dot-prefixed, so every enumerator the product already
# has (``scan_workspace_subjects``, the Files tree, ``tree_at``) hides it for free — it is
# machinery, never a page.
VEXA_DIR = ".vexa"
WORKSPACE_JSON = f"{VEXA_DIR}/workspace.json"

# The three kinds decision 26 names. A desk is a person's own workspace; a group is a shared one
# with a members roster; `global` is the single organisation tier. `_system` is deliberately NOT a
# kind: it is private per-user machinery (chats, sessions, settings), nothing links into it, and
# giving it an id would invite one.
KINDS = ("desk", "group", "global")

# RFC 4648 base32, lowercased. No `0`, `1`, `8` or `9` — so no digit can be misread as a letter in
# a link somebody types from a screen.
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
ID_LEN = 10


class WorkspaceIdError(ValueError):
    """A workspace identity operation that must not be papered over — a bad kind, or a stored file
    that says something the caller cannot act on."""


def mint_id() -> str:
    """A fresh workspace id: 10 characters of base32 from the system CSPRNG."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(ID_LEN))


def is_workspace_id(value: str) -> bool:
    """Is this string SHAPED like a workspace id? A total function on any string — the parser for
    the ``ws:`` link form calls it on whatever a document happened to contain."""
    v = str(value or "")
    return len(v) == ID_LEN and all(c in _ALPHABET for c in v)


def _json_path(ws_dir) -> Path:
    return Path(ws_dir) / WORKSPACE_JSON


def read_workspace_json(ws_dir) -> Optional[dict]:
    """The workspace's own identity record, or ``None`` when it has none / cannot be read.

    Never raises on a malformed file. A workspace whose identity file is corrupt is a workspace
    with no id — which the migration then mints for it — and that is a strictly better outcome than
    a control plane that will not start because one tree on the volume has bad json in it."""
    try:
        raw = _json_path(ws_dir).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        rec = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(rec, dict) or not is_workspace_id(str(rec.get("id") or "")):
        return None
    return rec


def write_workspace_json(ws_dir, *, id: str, kind: str, created: str) -> dict:
    """Write ``.vexa/workspace.json``. The caller owns the commit.

    Three fields and no more, on purpose: **id · kind · created**. The NAME is not here. A name
    changes — that is the whole reason the id exists — and a copy of it inside the tree would be a
    second place to change it, i.e. a second source of truth that goes stale silently. The name
    lives in the registry, which is the thing that already has to be updated on a rename."""
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise WorkspaceIdError(f"{kind!r} is not a workspace kind — one of {', '.join(KINDS)}")
    if not is_workspace_id(id):
        raise WorkspaceIdError(f"{id!r} is not a workspace id ({ID_LEN} chars of base32)")
    rec = {"id": id, "kind": kind, "created": str(created)}
    p = _json_path(ws_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rec


def ensure_workspace_json(ws_dir, *, kind: str, created: str) -> tuple[dict, bool]:
    """``(record, minted)`` — read the identity already in the tree, or mint one and write it.

    THIS IS THE WHOLE PRESERVATION RULE, and it is one ``if``. A parked tree that comes back keeps
    its id because the file came back with it. A repo cloned in that already carries the file keeps
    ITS id, because this reads before it mints — that is how an attached repo stays the same
    workspace instead of becoming a new one wearing the same name.

    The stored ``kind`` is NOT overwritten when it disagrees: a group promoted out of a desk slot
    is one workspace whose kind changed, and the caller that knows about the promotion updates it
    explicitly. Silently rewriting it here would let a routine walk of the volume re-label
    workspaces from a guess."""
    existing = read_workspace_json(ws_dir)
    if existing is not None:
        return existing, False
    return write_workspace_json(ws_dir, id=mint_id(), kind=kind, created=created), True


def set_kind(ws_dir, kind: str) -> Optional[dict]:
    """Re-label an existing workspace's kind (a promotion / un-share), keeping id and created."""
    rec = read_workspace_json(ws_dir)
    if rec is None:
        return None
    return write_workspace_json(ws_dir, id=rec["id"], kind=kind, created=rec.get("created", ""))


def workspace_id_of(ws_dir) -> Optional[str]:
    """Just the id, or ``None``. The read every caller that only wants to write a link needs."""
    rec = read_workspace_json(ws_dir)
    return rec["id"] if rec else None


# THE USAGE SIGNAL'S PROJECTION. Redis holds the authoritative touch log per desk id
# (`control_plane/workspace_ids.TouchLog`); this file is the copy the WORKER can read, because the
# README is regenerated at the end of a turn in a container that holds the workspace mounts and no
# redis. It lives here rather than beside the log for the same reason: `shared/` is what both
# images ship, and an import of `control_plane` from `worker/` is a boundary violation the
# isolation gate refuses — correctly, since the worker image does not contain that package at all.
TOUCHES_FILE = f"{VEXA_DIR}/touches.json"


def read_touches(desk_dir) -> list:
    """`[{workspace, path, at}]`, most recently opened first. `[]` for a desk nobody has opened
    anything from — which is most of them, and is not a failure."""
    try:
        rows = json.loads((Path(desk_dir) / TOUCHES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("workspace") and r.get("path")]


def ids_of_mounts(mounts) -> dict:
    """``{slug: id}`` for a dispatch's mount set — the map the agent needs in order to write a
    cross-workspace link at all.

    Mounts with no identity file are simply absent from the map rather than raising: a turn must
    still run over a workspace the migration has not reached, it just cannot be linked TO by id
    yet, and the in-workspace ``[[Title]]`` form still works."""
    out: dict = {}
    for m in mounts or []:
        if not isinstance(m, dict):
            continue
        slug, path = str(m.get("slug") or ""), str(m.get("path") or "")
        if not slug or not path:
            continue
        wid = m.get("id") or workspace_id_of(path)
        if wid:
            out[slug] = wid
    return out
