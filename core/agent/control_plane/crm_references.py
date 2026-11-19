"""crm_references.py — a workspace's links to CRM records, handed to the CRM as one fact per commit.

ADR-0047 decision 5. A page refers to a record — ``[Globex](/crm?record=<id>)`` in its text, or
``entity: <id>`` in its front matter — and the record shows *Mentioned in*. The CRM cannot read git;
this service holds the checkouts, so it is the one that reads them, and it TELLS (a publish edge,
``control_plane/publish.py``), it does not ask.

ONE FACT PER COMMIT, AND THE FACT IS THE WHOLE SET. For a workspace whose head moved since the last
publish, this reads every reference in the tree AT THAT COMMIT (``git grep`` over the commit, never
the work tree a turn may be halfway through writing) plus the roster in ``policy/members.json``, and
publishes ``workspace.references.changed`` with all of it. The CRM replaces that workspace's set.
So a fact applied twice changes nothing, a lost fact is repaired by the next commit, an older commit
never overwrites a newer one (the CRM compares ``committed_at``), and the CRM's references are
rebuildable from git at any time: ``python -m control_plane.crm_references rebuild`` publishes every
workspace's set again.

OFF unless the deployment runs the CRM: ``VEXA_CRM_REFERENCES_POLL_S`` (default 0) is the poll
interval. NEVER RAISES out of the loop — a reference that did not land is a backlink missing for one
poll, and a service that will not boot over it is a broken one.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from workspaces.shared import workspace_paths as wpaths

from control_plane import publish as publish_mod
from shared.gitexec import run_git

logger = logging.getLogger("agent_api.crm_references")

EVENT = publish_mod.EVENT_WORKSPACE_REFERENCES_CHANGED
POLL_ENV = "VEXA_CRM_REFERENCES_POLL_S"
MEMBERS_FILE = "policy/members.json"
#: A record id: the CRM's ids are UUIDs, and nothing else is a reference.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
LINK = re.compile(r"\]\(/crm\?(?:[^)\s]*&)?record=(" + _UUID + r")")
FRONT = re.compile(r"^entity:\s*['\"]?(" + _UUID + r")['\"]?\s*$")
#: git grep's own pattern: a line that can hold either shape. The two regexes above decide.
GREP = r"/crm\?|^entity:"
MAX_REFERENCES = 20000
#: A desk is the store directory named for its subject — an account id.
DESK = re.compile(r"[0-9]{1,18}")
TIMEOUT_S = 20


def _git(ws: Path, *args: str) -> str:
    out = run_git(ws, *args, timeout=TIMEOUT_S)
    if out.returncode not in (0, 1):            # git grep answers 1 for "no match"
        raise RuntimeError(f"git {args[0]} failed")
    return out.stdout


def head(ws: Path) -> Optional[tuple[str, str]]:
    """``(commit, committed_at)`` of the workspace's HEAD, or None for a tree with no commit."""
    try:
        line = _git(ws, "log", "-1", "--format=%H %cI").strip()
    except Exception:  # noqa: BLE001
        return None
    if not line:
        return None
    commit, _, at = line.partition(" ")
    return commit, at


def references_at(ws: Path, commit: str) -> list[dict]:
    """Every (page, record) the tree at ``commit`` holds, from its committed Markdown only."""
    raw = _git(ws, "grep", "-I", "-n", "-E", GREP, commit, "--", "*.md", "*.mdx")
    found: set[tuple[str, str]] = set()
    for line in raw.splitlines():
        # `<commit>:<path>:<lineno>:<text>`
        try:
            _, path, _, text = line.split(":", 3)
        except ValueError:
            continue
        if path.startswith(".") or "/." in path:
            continue                              # the platform's own trees are not pages
        for match in LINK.finditer(text):
            found.add((path, match.group(1).lower()))
        front = FRONT.match(text.strip())
        if front:
            found.add((path, front.group(1).lower()))
        if len(found) >= MAX_REFERENCES:
            break
    return [{"page": p, "record": r} for p, r in sorted(found)]


def roster_at(ws: Path, commit: str) -> Optional[list[dict]]:
    try:
        raw = _git(ws, "show", f"{commit}:{MEMBERS_FILE}")
    except Exception:  # noqa: BLE001
        return None
    try:
        rows = json.loads(raw) if raw.strip() else None
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    return [{"subject": str(m["subject"]), "role": str(m["role"])}
            for m in rows if isinstance(m, dict) and m.get("subject") and m.get("role")]


def snapshot(ws: Path, workspace: str) -> Optional[dict]:
    at = head(ws)
    if at is None:
        return None
    commit, committed_at = at
    out = {"workspace": workspace, "commit": commit, "committed_at": committed_at,
           "references": references_at(ws, commit)}
    members = roster_at(ws, commit)
    if members is None and DESK.fullmatch(workspace):
        # A person's desk has no roster file: it is the directory named for its subject
        # (dispatch mounts `<store>/<subject>`), so its owner is that subject.
        members = [{"subject": workspace, "role": "owner"}]
    if members is not None:
        out["members"] = members
    # The person the fact concerns, as every agent carrier names one: the workspace's owner on its
    # roster, else the desk's own name (a desk is named for the subject it belongs to).
    owner = next((m["subject"] for m in members or [] if m["role"] == "owner"), "")
    out["uid"] = owner or workspace
    return out


def workspaces(root: Path) -> list[tuple[str, Path]]:
    """Every top-level git workspace under the store: the shared ones and the desks alike."""
    out = []
    for name in wpaths.list_dirs_inside(root):
        if not wpaths.is_workspace_name(name) or name.startswith("."):
            continue
        if wpaths.is_dir_inside(root, f"{name}/.git", allow=(".git",)):
            out.append((name, Path(root) / name))
    return out


def source_event_id(workspace: str, commit: str) -> str:
    return f"refs-{workspace}-{commit}"


def reconcile(root: Path, seen: dict, *, force: bool = False,
              send: Callable[[str, str, dict], bool] = publish_mod.publish) -> list[str]:
    """Publish a snapshot for each workspace whose head moved. ``seen`` remembers what landed."""
    published = []
    for name, ws in workspaces(Path(root)):
        at = head(ws)
        if at is None or (not force and seen.get(name) == at[0]):
            continue
        try:
            snap = snapshot(ws, name)
        except Exception:  # noqa: BLE001 — one unreadable tree must not stop the others
            logger.info("crm references: %s could not be read this poll", name)
            continue
        if snap is None:
            continue
        if send(EVENT, source_event_id(name, snap["commit"]), snap):
            seen[name] = snap["commit"]
            published.append(name)
    return published


@dataclass(frozen=True)
class Handle:
    thread: threading.Thread
    stop_event: threading.Event

    def stop(self) -> None:
        self.stop_event.set()


def interval() -> float:
    try:
        return max(0.0, float(os.environ.get(POLL_ENV, "0") or 0))
    except ValueError:
        return 0.0


def start(root: "str | Path", *, interval_sec: Optional[float] = None) -> Optional[Handle]:
    every = interval() if interval_sec is None else interval_sec
    if every <= 0:
        return None
    seen: dict = {}

    def once() -> None:
        try:
            done = reconcile(Path(root), seen)
            if done:
                logger.info("crm references: published %d workspace(s)", len(done))
        except Exception:  # noqa: BLE001
            logger.exception("crm references: reconcile failed")

    stop_event = threading.Event()

    def loop() -> None:
        once()
        while not stop_event.wait(every):
            once()

    thread = threading.Thread(target=loop, name="crm-references", daemon=True)
    thread.start()
    return Handle(thread=thread, stop_event=stop_event)


def main(argv: "list[str]") -> int:
    if len(argv) < 2 or argv[1] != "rebuild":
        print("usage: python -m control_plane.crm_references rebuild [<workspaces root>]", file=sys.stderr)
        return 2
    root = Path(argv[2] if len(argv) > 2 else os.environ.get("VEXA_WORKSPACES_DIR", "/workspaces"))
    done = reconcile(root, {}, force=True)
    print(f"[crm-references] published {len(done)} workspace snapshot(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
