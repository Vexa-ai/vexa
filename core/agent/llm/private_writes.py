"""private_writes.py — the leak rule on the files a turn writes itself.

agent-api holds a page write from a session that read the person's private sources into a
workspace other people read (``control_plane/private_taint.py``). A turn can also write pages with
its own file tools, straight onto its mounts, and those writes meet the platform only at the
post-turn commit (``ports.run_harness_turn``). This is the same rule at that seam:

* the turn's tool calls are watched; a call to a private source — the person's mail, calendar, a
  connected service through the vexa tools, or any MCP server other than vexa (a personal
  connector) — MARKS this worker's session for ``TTL_SEC`` (what the model read stays in its context
  for the next turns too);
* at the commit, a marked session's changes to a mount whose readers are not only the person
  (``policy/members.json`` names someone else) are NOT committed: they are saved to the person's own
  desk (``.vexa/held-writes/<code>.json``, git-excluded), the mount is put back as it was, and the
  turn says what it withheld, from where, and the code that releases it;
* when the person's message says ``confirm <code>``, the held files are written back at the start of
  that turn and committed with it — once.

What is held is the whole of the turn's change to that workspace: the commit cannot tell a sentence
that came from the mail from one that did not, so it asks.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Iterable, Optional

from llm import workspace_paths as wpaths
from llm.gitexec import run_git

log = logging.getLogger(__name__)

TTL_SEC = 12 * 3600
HELD_DIR = ".vexa/held-writes"
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
CONFIRM_RE = re.compile(r"\bconfirm\s+([a-z0-9]{6})\b", re.IGNORECASE)

#: vexa tools that return the person's private data.
PRIVATE_VEXA_TOOLS = {
    "gmail_search": "mail", "gmail_read": "mail", "gmail_thread": "mail", "gmail_inbox": "mail",
    "calendar_events": "calendar", "secret_service_call": "connected service",
    "onboarding_research": "mail",
}
#: Paths never held: runtime plumbing and the attachment drawer, which never commit anyway, and
#: `policy/`, which the policy guard owns.
_SKIP_PREFIXES = (".claude/", "uploads/", "policy/", ".vexa/")


def private_source(tool: str) -> Optional[str]:
    """The private source a tool call reads, or None. ``mcp__<server>__<name>`` names its server."""
    t = str(tool or "")
    if not t.startswith("mcp__"):
        return None
    parts = t.split("__")
    if len(parts) < 3:
        return None
    server, name = parts[1], "__".join(parts[2:])
    if server != "vexa":
        return f"connected service ({server})"
    return PRIVATE_VEXA_TOOLS.get(name)


def other_readers(mount: Path, owner: str) -> list[str]:
    raw = wpaths.read_text_inside(mount, "policy/members.json")
    try:
        rows = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(m.get("email") or m.get("subject")) for m in (rows if isinstance(rows, list) else [])
            if isinstance(m, dict) and m.get("subject") and str(m.get("subject")) != str(owner)]


def _git(work: Path, *args: str) -> str:
    return run_git(work, *args, check=True).stdout


def _changed(work: Path) -> list[str]:
    """Every path the turn changed in ``work`` (tracked edits and deletions, new files)."""
    out = set()
    for blob in (_git(work, "diff", "--name-only", "-z", "HEAD"),
                 _git(work, "diff", "--cached", "--name-only", "-z", "HEAD"),
                 _git(work, "ls-files", "--others", "--exclude-standard", "-z")):
        out |= {p for p in blob.split("\0") if p}
    return sorted(p for p in out if not p.startswith(_SKIP_PREFIXES))


def _in_head(work: Path, rel: str) -> bool:
    return run_git(work, "cat-file", "-e", f"HEAD:{rel}").returncode == 0


_VEXA = (".vexa",)


class Guard:
    """One worker's view: which private sources its session read, and where held writes wait."""

    def __init__(self, owner: str, held_root: Optional[Path]):
        self.owner = str(owner or "")
        self.held_root = Path(held_root) if held_root else None
        self._marked: dict[str, float] = {}
        self._released: dict[str, set] = {}       # mount path -> paths the person released this turn

    @classmethod
    def from_env(cls, env: dict, mounts: list[dict]) -> "Guard":
        owner = str(env.get("VEXA_OWNER") or "")
        desk = next((m.get("path") for m in mounts
                     if m.get("slug") == owner and m.get("role") == "private" and m.get("path")), None)
        return cls(owner, Path(desk) if desk else None)

    # ── marking ──────────────────────────────────────────────────────────────────────────────────
    def observe(self, ev: dict) -> None:
        if ev.get("type") == "tool-call":
            found = private_source(str(ev.get("tool") or ""))
            if found:
                self._marked[found] = time.time()

    def sources(self) -> list[str]:
        now = time.time()
        return sorted(k for k, at in self._marked.items() if now - at <= TTL_SEC)

    # ── holding ──────────────────────────────────────────────────────────────────────────────────
    def _store_ok(self) -> bool:
        if self.held_root is None:
            return False
        wpaths.ensure_git_exclude(self.held_root, "/.vexa/held-writes/")
        return True

    def hold(self, mount: Path) -> Optional[dict]:
        """Withhold a marked session's change to ``mount`` when others read it. Returns what was
        withheld (``{code, workspace, paths, readers, sources}``) or None."""
        found = self.sources()
        if not found:
            return None
        mount = Path(mount)
        if not wpaths.is_dir_inside(mount, ".git", allow=(".git",)):
            return None
        readers = other_readers(mount, self.owner)
        if not readers:
            return None
        released = self._released.get(str(mount), set())
        paths = [p for p in _changed(mount) if p not in released]
        if not paths:
            return None
        files: dict[str, Optional[str]] = {}
        for rel in paths:
            data = wpaths.read_bytes_inside(mount, rel) if wpaths.is_file_inside(mount, rel) else None
            files[rel] = base64.b64encode(data).decode() if data is not None else None
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        stored = self._store_ok()
        if stored:
            wpaths.write_text_inside(self.held_root, f"{HELD_DIR}/{code}.json", json.dumps({
                "code": code, "mount": str(mount), "workspace": mount.name, "files": files,
                "sources": found, "at": time.time()}), allow=_VEXA)
        for rel in paths:
            run_git(mount, "reset", "-q", "HEAD", "--", rel)
            if _in_head(mount, rel):
                _git(mount, "checkout", "HEAD", "--", rel)
            else:
                wpaths.unlink_inside(mount, rel)
        return {"code": code if stored else "", "workspace": mount.name, "paths": paths,
                "readers": readers, "sources": found}

    def release_confirmed(self, text: str) -> list[dict]:
        """Write back every held change whose code the person's ``text`` confirms. Returns what was
        released (``{code, workspace, paths}``)."""
        self._released = {}
        if self.held_root is None:
            return []
        out = []
        for code in {c.lower() for c in CONFIRM_RE.findall(str(text or ""))}:
            rel_held = f"{HELD_DIR}/{code}.json"
            raw = wpaths.read_text_inside(self.held_root, rel_held, allow=_VEXA)
            try:
                row = json.loads(raw) if raw else None
            except ValueError:
                row = None
            if not isinstance(row, dict):
                continue
            mount = Path(row.get("mount") or "")
            if not mount.is_dir():
                continue
            for rel, b64 in (row.get("files") or {}).items():
                try:
                    if b64 is None:
                        wpaths.unlink_inside(mount, rel)
                    else:
                        wpaths.write_bytes_inside(mount, rel, base64.b64decode(b64))
                except wpaths.PathRefused:
                    continue
                self._released.setdefault(str(mount), set()).add(rel)
            wpaths.unlink_inside(self.held_root, rel_held, allow=_VEXA)
            out.append({"code": code, "workspace": row.get("workspace"), "paths": sorted(row.get("files") or {})})
        return out


def withheld_line(held: dict) -> str:
    """What the person reads at the end of the turn."""
    who = ", ".join(held["readers"][:5])
    pages = ", ".join(held["paths"][:5]) + (" …" if len(held["paths"]) > 5 else "")
    how = (f" Reply `confirm {held['code']}` to write it there." if held.get("code")
           else " Ask me again in a chat with your own desk mounted to write it.")
    return (f"\n\nWithheld from {held['workspace']}: {pages}. This chat read your private "
            f"{' and '.join(held['sources'])}, and {held['workspace']} is also read by {who}, so "
            f"nothing was written there.{how}")


def released_line(rows: Iterable[dict]) -> str:
    rows = list(rows)
    if not rows:
        return ""
    return "".join(f"Released to {r['workspace']} as you confirmed: {', '.join(r['paths'])}.\n" for r in rows)
