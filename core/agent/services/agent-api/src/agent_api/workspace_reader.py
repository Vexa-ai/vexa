"""workspace_reader.py — read a subject's workspace (the git knowledge graph) for the Workspace surface.

Read-only view over the per-subject workspace dir the chat runner maintains. Hides `.git`/`.claude`
internals and guards against path traversal (a read path can never escape the subject's workspace).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_HIDDEN = {".git", ".claude"}


class WorkspaceReader:
    def __init__(self, workspaces_dir: str) -> None:
        self._root = Path(workspaces_dir)

    def _ws(self, subject: str) -> Path:
        ws = (self._root / subject).resolve()
        root = self._root.resolve()
        if ws != root and root not in ws.parents:  # traversal guard (subject must stay under root)
            raise ValueError("invalid subject")
        return ws

    def tree(self, subject: str) -> list[str]:
        """Sorted relative paths of the subject's files (excluding .git/.claude internals)."""
        ws = self._ws(subject)
        if not ws.exists():
            return []
        out: list[str] = []
        for p in sorted(ws.rglob("*")):
            if any(part in _HIDDEN for part in p.relative_to(ws).parts):
                continue
            if p.is_file():
                out.append(str(p.relative_to(ws)))
        return out

    def read(self, subject: str, path: str) -> Optional[str]:
        """The text at ``path`` within the subject's workspace, or None if absent. Traversal-guarded."""
        ws = self._ws(subject)
        f = (ws / path).resolve()
        if ws not in f.parents:  # the resolved path must stay inside the workspace
            raise ValueError("invalid path")
        return f.read_text() if f.exists() and f.is_file() else None

    def git_state(self, subject: str) -> dict:
        """Real source-control state of the subject's workspace: branch, working changes, recent commits
        (the governed git knowledge graph the agent commits to). Empty shape if not yet a repo."""
        import subprocess

        ws = self._ws(subject)
        if not (ws / ".git").exists():
            return {"branch": "", "changes": [], "commits": []}

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(ws), *args], capture_output=True, text=True
            ).stdout.strip()

        changes = []
        for line in git("status", "--porcelain").splitlines():
            if len(line) > 3:
                flag = line[:2].strip()[:1] or "M"
                changes.append({"path": line[3:].strip(), "kind": "A" if flag in ("A", "?") else flag})
        commits = []
        for line in git("log", "-8", "--pretty=%h\x1f%s\x1f%cr").splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                commits.append({"sha": parts[0], "msg": parts[1], "when": parts[2]})
        return {"branch": git("rev-parse", "--abbrev-ref", "HEAD") or "main", "changes": changes, "commits": commits}
