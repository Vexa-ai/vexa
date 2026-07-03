"""workspace_reader.py — read a subject's workspace (the git knowledge graph) for the Workspace surface.

Read-only view over the per-subject workspace dir the chat runner maintains. Hides `.git`/`.claude`
internals and guards against path traversal (a read path can never escape the subject's workspace).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _tool_op(name: str) -> dict:
    """Classify a claude tool name into one of the terminal's op labels (read/search/edit/git/web/tool).
    Mirrors the frontend ``toolOp`` so the loaded history reads the same as a live turn."""
    t = (name or "").lower()
    if any(k in t for k in ("read", "cat", "open")) and "edit" not in t:
        label = "read"
    elif any(k in t for k in ("search", "grep", "find", "glob")):
        label = "search"
    elif any(k in t for k in ("edit", "write", "append")):
        label = "edit"
    elif any(k in t for k in ("git", "commit")):
        label = "git"
    elif any(k in t for k in ("web", "fetch", "http")):
        label = "web"
    else:
        label = "tool"
    return {"label": label}


def _block_text(content) -> str:
    """Concatenate the ``text`` of an assistant message's content (string, or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""

# `.git` is pure plumbing — huge/noisy, never useful in the Files tree — so it's hidden
# unconditionally. Everything else dot-prefixed (`.claude` + any dotfile/dotdir) is hidden by
# default but surfaced when the caller opts in via ``hidden=True``.
_ALWAYS_HIDDEN = {".git"}


class WorkspaceReader:
    def __init__(self, workspaces_dir: str) -> None:
        self._root = Path(workspaces_dir)

    @property
    def root(self) -> Path:
        return self._root

    def _ws(self, subject: str) -> Path:
        ws = (self._root / subject).resolve()
        root = self._root.resolve()
        if ws != root and root not in ws.parents:  # traversal guard (subject must stay under root)
            raise ValueError("invalid subject")
        return ws

    def workspace_dir(self, subject: str) -> Path:
        return self._ws(subject)

    def tree(self, subject: str, hidden: bool = False) -> list[str]:
        """Sorted relative paths of the subject's files.

        Always excludes ``.git`` internals. By default also excludes ``.claude`` and any other
        dotfile/dotdir; pass ``hidden=True`` to include those (e.g. so the Files panel can show
        ``.claude/`` sessions + config). ``.git`` stays hidden either way.
        """
        ws = self._ws(subject)
        if not ws.exists():
            return []
        out: list[str] = []
        for p in sorted(ws.rglob("*")):
            parts = p.relative_to(ws).parts
            if any(part in _ALWAYS_HIDDEN for part in parts):
                continue
            if not hidden and any(part.startswith(".") for part in parts):
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

    def _session_id(self, ws: Path, session: str) -> Optional[str]:
        """The claude sessionId for a thread, read from its continuity pointer
        (``.claude/sessions/<session>.session``; the legacy ``main`` falls back to ``.claude/.session``)."""
        candidates = [ws / ".claude" / "sessions" / f"{session}.session"]
        if session == "main":
            candidates.append(ws / ".claude" / ".session")
        for f in candidates:
            try:
                if f.exists() and f.is_file():
                    sid = f.read_text().strip()
                    if sid:
                        return sid
            except OSError:
                continue
        return None

    def history(self, subject: str, session: str) -> list[dict]:
        """The session's prior conversation as ordered, terminal-renderable turns.

        Resolves the thread's claude sessionId from its continuity pointer, finds the transcript JSONL
        under ``<ws>/.claude/projects/<cwd-slug>/<sessionId>.jsonl``, and parses it into ``Turn``-shaped
        dicts: user turns ``{role:"user", text}``; agent turns ``{role:"agent", text, ops, commit?}``.
        Tolerant by design — a missing pointer/file or unparseable lines yield ``[]`` (never raises),
        so the surface degrades to "no history yet" rather than erroring."""
        if "/" in session or "\\" in session or session in ("", ".", ".."):
            return []
        ws = self._ws(subject)
        sid = self._session_id(ws, session)
        if not sid:
            return []
        projects = ws / ".claude" / "projects"
        if not projects.exists():
            return []
        # The cwd-slug dir is claude's encoding of the workspace path; there is normally one, but match by
        # the sessionId filename to be safe. ``rglob`` also catches subagent transcripts — we want the top.
        path: Optional[Path] = None
        for cand in projects.glob(f"*/{sid}.jsonl"):
            path = cand
            break
        if path is None:
            return []
        try:
            raw = path.read_text()
        except OSError:
            return []

        turns: list[dict] = []
        cur_agent: Optional[dict] = None  # the open agent turn we accumulate text/ops onto

        def flush_agent() -> None:
            nonlocal cur_agent
            if cur_agent is not None:
                turns.append(cur_agent)
                cur_agent = None

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            msg = obj.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None

            if kind == "user":
                # A real user prompt is a plain string or a content list with text blocks. A list that is
                # ONLY tool_results belongs to the preceding agent turn (a tool round-trip) — skip it.
                is_tool_result = (
                    isinstance(content, list)
                    and content
                    and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                )
                if is_tool_result:
                    continue
                text = _block_text(content)
                if text.strip():
                    flush_agent()
                    turns.append({"role": "user", "text": text})
            elif kind == "assistant":
                if not isinstance(content, list):
                    continue
                if cur_agent is None:
                    cur_agent = {"role": "agent", "text": "", "ops": []}
                cur_agent["text"] += _block_text(content)
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        cur_agent["ops"].append(_tool_op(b.get("name", "")))
            # all other line kinds (queue-operation/last-prompt/custom-title/mode/attachment/system …) are meta — skip
        flush_agent()
        return turns

    def drop_session(self, subject: str, session: str) -> bool:
        """Delete a chat thread's continuity file (``.claude/sessions/<session>.session``) so a future
        turn on the same name starts a fresh conversation. The ``"main"`` thread also clears the legacy
        single-thread file (``.claude/.session``). Returns whether anything was removed. Traversal-safe:
        ``session`` is a bare name (no path separators)."""
        if "/" in session or "\\" in session or session in ("", ".", ".."):
            raise ValueError("invalid session")
        ws = self._ws(subject)
        removed = False
        targets = [ws / ".claude" / "sessions" / f"{session}.session"]
        if session == "main":
            targets.append(ws / ".claude" / ".session")
        for f in targets:
            if f.exists() and f.is_file():
                f.unlink()
                removed = True
        return removed

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
                path = line[3:].strip()
                if path.split("/", 1)[0].lstrip(".") in ("git", "claude"):
                    continue  # hide the agent's internal .git/.claude session plumbing
                flag = line[:2].strip()[:1] or "M"
                changes.append({"path": path, "kind": "A" if flag in ("A", "?") else flag})
        commits = []
        for line in git("log", "-8", "--pretty=%h\x1f%s\x1f%cr").splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                commits.append({"sha": parts[0], "msg": parts[1], "when": parts[2]})
        return {"branch": git("rev-parse", "--abbrev-ref", "HEAD") or "main", "changes": changes, "commits": commits}
