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
