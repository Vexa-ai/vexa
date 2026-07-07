"""workspace_purpose.py — a per-workspace PURPOSE statement that travels with the workspace.

Each workspace can carry a one-line statement of what it is FOR / what the agent should write there.
It lives IN the workspace as a plain ``PURPOSE`` file at the repo root, so it is committed to the
workspace's own git history and TRAVELS when the workspace is shared (a member who mounts a shared
``customer-deal`` workspace inherits its purpose too). The dispatcher reads it into each mount's dict
and ``engine.mounts_preamble`` declares it to the model — so an agent with a COMPOSITION mounted
(Personal + a deal workspace + a sales-dept workspace) knows what belongs where without guessing.

Storage is deliberately trivial (a text file, not frontmatter) so it is human-editable, diff-friendly,
and cheap to read on every dispatch. The statement is a short line; we trim + collapse whitespace and
cap the length so it stays a *preamble line*, never a document.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from shared.gitenv import scrubbed_git_env

log = logging.getLogger(__name__)

PURPOSE_FILE = "PURPOSE"
MAX_PURPOSE_LEN = 500  # a purpose is a one-liner, not a doc — keep the preamble tight


def _normalize(text: str) -> str:
    """Collapse to a single trimmed line, capped — a purpose is a statement, not a paragraph."""
    line = " ".join((text or "").split())
    return line[:MAX_PURPOSE_LEN]


def read_purpose(ws: str | Path) -> str:
    """The workspace's purpose statement (``""`` when unset / unreadable). Read-only + quiet: this is on
    the dispatch hot path, so a missing file / bad bytes is just an empty purpose, never an error."""
    p = Path(ws) / PURPOSE_FILE
    try:
        return _normalize(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


def write_purpose(ws: str | Path, text: str) -> str:
    """Set (or clear, with an empty ``text``) the workspace's purpose and commit it to the workspace's own
    git history so it persists and travels when shared. Returns the normalized purpose actually stored.
    Commits ONLY the ``PURPOSE`` file (never the agent's working tree). A commit failure is non-fatal —
    the file is written regardless (a later turn-commit will pick it up)."""
    wsp = Path(ws)
    purpose = _normalize(text)
    path = wsp / PURPOSE_FILE
    if purpose:
        path.write_text(purpose + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()  # clearing the purpose removes the file
    _commit_purpose(wsp, purpose)
    return purpose


def _commit_purpose(ws: Path, purpose: str) -> None:
    """Commit just the PURPOSE file (add/remove) with the platform identity. Best-effort: a non-git
    workspace or an empty diff is a quiet no-op — the on-disk file is authoritative regardless."""
    if not (ws / ".git").exists():
        return
    env = scrubbed_git_env()
    try:
        subprocess.run(["git", "-C", str(ws), "add", "--", PURPOSE_FILE],
                       check=True, capture_output=True, text=True, env=env)
        msg = f"workspace: set purpose" if purpose else "workspace: clear purpose"
        proc = subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", msg, "--", PURPOSE_FILE],
                              capture_output=True, text=True, env=env)
        if proc.returncode != 0 and "nothing to commit" not in (proc.stdout + proc.stderr):
            log.warning("purpose commit in %s failed: %s", ws, proc.stderr.strip())
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("purpose commit in %s errored: %s", ws, exc)
