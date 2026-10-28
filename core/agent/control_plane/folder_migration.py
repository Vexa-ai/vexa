"""folder_migration.py — chat folders become workspaces, or Hidden (decision J1, R1, 2026-10-16).

The approved rail groups chats by WORKSPACE (a chat's write target, a room's workspace) and has no
folders of its own. A person's folders are migrated once, behind the `rooms` flag:

* a folder whose chats ALL write to one workspace W is MAPPED to W — its rows leave the folder and
  show under W in the rail (they already write there);
* every other folder's rows go to HIDDEN — never deleted, and listed in a one-time notice;
* the folders themselves are KEPT (empty, with their names), so the old grouping can be restored by
  hand and serves as a saved filter until it is retired later. Pinned rows stay pinned.

NEVER DELETES. `plan` is a pure read and is the dry-run report; `apply` performs exactly the plan and
is idempotent — a second run finds nothing to move. A row whose target cannot be read counts as "no
workspace" (Hidden), never as a guess.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from control_plane import chat_folders as cf

logger = logging.getLogger("agent_api.folder_migration")

PERSONAL = ("", "personal", "desk")


def _filings(store, owner: str) -> dict:
    with store._lock:
        rows = store._x("SELECT row_key, folder FROM agent_session_filing WHERE owner = ?",
                        (owner,)).fetchall()
    return {r[0]: r[1] for r in rows}


def plan(store, owner: str, *, target_of: Callable[[str], str]) -> dict:
    """The dry-run report: per user folder, its rows and where they go — ``{"folders": [{id, name,
    rows, workspace | None, action: map|hide|empty}], "moves": {row: to}, "hidden": [rows]}``."""
    listing = cf.listing(store, owner)
    filings = _filings(store, owner)
    out = {"owner": owner, "folders": [], "moves": {}, "hidden": []}
    for f in listing["folders"]:
        if f["builtin"]:
            continue
        rows = [r for r, folder in filings.items() if folder == f["id"]]
        targets = set()
        for row in rows:
            if not row.startswith("c:"):
                targets.add(None)              # a meeting row has no write target of its own
                continue
            try:
                t = str(target_of(row[2:]) or "").strip()
            except Exception:  # noqa: BLE001 — an unreadable target is "no workspace"
                t = ""
            targets.add(None if t in PERSONAL else t)
        workspace = next(iter(targets)) if len(targets) == 1 else None
        if not rows:
            action = "empty"
        elif workspace:
            action = "map"
            for row in rows:
                out["moves"][row] = cf.UNGROUPED
        else:
            action = "hide"
            for row in rows:
                out["moves"][row] = cf.HIDDEN
                out["hidden"].append(row)
        out["folders"].append({"id": f["id"], "name": f["name"], "rows": rows, "workspace": workspace,
                               "action": action})
    return out


def apply(store, owner: str, *, target_of: Callable[[str], str],
          notify: Optional[Callable[[str], None]] = None) -> dict:
    """Perform the plan: mapped rows to Ungrouped (they show under their workspace), the rest to
    Hidden. Folders are kept. Returns the plan it applied; a second call moves nothing."""
    p = plan(store, owner, target_of=target_of)
    by_dest: dict = {}
    for row, to in p["moves"].items():
        by_dest.setdefault(to, []).append(row)
    for to, rows in by_dest.items():
        cf.file_rows(store, owner, rows, to)
    if notify is not None and p["moves"]:
        mapped = [f for f in p["folders"] if f["action"] == "map"]
        hidden = [f for f in p["folders"] if f["action"] == "hide"]
        try:
            notify(f"Folders now follow workspaces: {len(mapped)} mapped, {len(hidden)} to Hidden")
        except Exception as exc:  # noqa: BLE001 — the move happened; the notice is the extra
            logger.warning("folder migration notice not sent (%s)", type(exc).__name__)
    return p
