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

# Commit authors that are platform/seed PLUMBING, not a member's agent — classified ``system`` so the
# activity feed never mistakes a policy or seed commit for a member push. The per-mount turn-commit stamps
# a member's principal instead (name=<subject>, email=<subject>@vexa.local — see worker/engine.py, D4).
_SYSTEM_AUTHOR_EMAILS = {"platform@vexa.ai", "agent@vexa"}
_SYSTEM_AUTHOR_NAMES = {"vexa-platform", "vexa-agent"}


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

    def _guard_under_root(self, base: Path) -> Path:
        """A workspace dir must live under the store root (traversal guard). Used by the path-based
        readers so a mount PATH (from the active set — own private slots under .attached, or a shared
        workspace at <root>/<id>) can be read directly, not only a ``<root>/<subject>`` dir."""
        base = base.resolve()
        root = self._root.resolve()
        if base != root and root not in base.parents:
            raise ValueError("outside root")
        return base

    def tree(self, subject: str, hidden: bool = False) -> list[str]:
        """Sorted relative paths of the subject's files (the subject's own ``<root>/<subject>`` dir)."""
        return self.tree_at(self._ws(subject), hidden=hidden)

    def tree_at(self, base: Path, hidden: bool = False) -> list[str]:
        """Sorted relative paths of the files under ``base`` (any workspace dir under the store root).

        Always excludes ``.git`` internals. By default also excludes ``.claude`` and any other
        dotfile/dotdir; pass ``hidden=True`` to include those. ``.git`` stays hidden either way.
        """
        ws = self._guard_under_root(base)
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
        """The text at ``path`` within the subject's own workspace, or None if absent. Traversal-guarded."""
        return self.read_at(self._ws(subject), path)

    def read_at(self, base: Path, path: str) -> Optional[str]:
        """The text at ``path`` within the ``base`` workspace dir, or None if absent. Traversal-guarded."""
        ws = self._guard_under_root(base)
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

    def _continuity_roots(self, subject: str, extra_roots: "list[str | Path] | None" = None) -> list[Path]:
        """Every workspace dir a thread's continuity (pointer + transcript) may live in, in preference
        order: the PRIVATE SYSTEM tier (``<root>/.system/<subject>`` — where the worker anchors chats
        now), the subject's own workspace (the legacy location), then any caller-supplied mount dirs —
        the turn's cwd FOLLOWS the active set under the flat model, so chats recorded before the
        _system anchoring landed sit under whichever workspace was mounted first (e.g. a shared one).
        Non-existent and out-of-root candidates are silently dropped."""
        candidates: list[Path] = [self._root / ".system" / subject, self._ws(subject)]
        for e in extra_roots or []:
            candidates.append(Path(e))
        out: list[Path] = []
        seen: set[str] = set()
        for c in candidates:
            try:
                c = self._guard_under_root(c)
            except ValueError:
                continue
            k = str(c)
            if k in seen or not c.exists():
                continue
            seen.add(k)
            out.append(c)
        return out

    def history(self, subject: str, session: str, extra_roots: "list[str | Path] | None" = None) -> list[dict]:
        """The session's prior conversation as ordered, terminal-renderable turns.

        Resolves the thread's claude sessionId from its continuity pointer, finds the transcript JSONL
        under ``<ws>/.claude/projects/<cwd-slug>/<sessionId>.jsonl``, and parses it into ``Turn``-shaped
        dicts: user turns ``{role:"user", text}``; agent turns ``{role:"agent", text, ops, commit?}``.
        Pointer and transcript are searched across every continuity root (``_continuity_roots``) — they
        normally co-locate, but a thread that MOVED anchors (cwd-rooted → _system-rooted) may have them
        apart. Tolerant by design — a missing pointer/file or unparseable lines yield ``[]`` (never
        raises), so the surface degrades to "no history yet" rather than erroring."""
        if "/" in session or "\\" in session or session in ("", ".", ".."):
            return []
        roots = self._continuity_roots(subject, extra_roots)
        sid: Optional[str] = None
        for ws in roots:
            sid = self._session_id(ws, session)
            if sid:
                break
        if not sid:
            return []
        # The cwd-slug dir is claude's encoding of the workspace path; there is normally one, but match by
        # the sessionId filename to be safe. ``rglob`` also catches subagent transcripts — we want the top.
        path: Optional[Path] = None
        for ws in roots:
            projects = ws / ".claude" / "projects"
            if not projects.exists():
                continue
            for cand in projects.glob(f"*/{sid}.jsonl"):
                path = cand
                break
            if path is not None:
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
        removed = False
        targets: list[Path] = []
        # every continuity root the pointer may live in (_system, home — extra mount dirs are not
        # needed here: dropping the indexed thread only has to cover the anchored locations)
        for ws in self._continuity_roots(subject):
            targets.append(ws / ".claude" / "sessions" / f"{session}.session")
            if session == "main":
                targets.append(ws / ".claude" / ".session")
        for f in targets:
            if f.exists() and f.is_file():
                f.unlink()
                removed = True
        return removed

    def git_state(self, subject: str) -> dict:
        """Real source-control state of the subject's OWN (primary) workspace — thin wrapper over
        ``git_state_at`` with the caller as viewer (so their own commits classify as ``you``)."""
        return self.git_state_at(self._ws(subject), viewer=subject)

    def git_state_at(self, base: Path, viewer: Optional[str] = None) -> dict:
        """Author-attributed source-control state (branch · working changes · recent commits) of the
        workspace at ``base`` — which may be the caller's own repo OR a SHARED workspace they're a member
        of (resolved+authorized by the API's ``_read_target``). Empty shape if not yet a repo.

        Each commit carries ``author`` (the committing principal's display id, stamped by the per-mount
        turn-commit — D4) and ``kind`` ∈ {``you``, ``member``, ``system``} so the terminal can surface
        OTHER members' agent pushes to a shared workspace distinctly from the viewer's own writes and from
        platform/seed plumbing. ``viewer`` (the caller's subject id) is what makes ``you`` resolvable — the
        turn-commit stamps author email ``<subject>@vexa.local`` (see ``worker/engine.py`` principal)."""
        import subprocess

        from shared.gitenv import scrubbed_git_env

        base = self._guard_under_root(base)
        if not (base / ".git").exists():
            return {"branch": "", "changes": [], "commits": []}

        def git(*args: str) -> str:
            # scrubbed env: a hook-exported GIT_DIR would report the HOOK's repo, not this workspace
            return subprocess.run(
                ["git", "-C", str(base), *args], capture_output=True, text=True, env=scrubbed_git_env()
            ).stdout.strip()

        changes = []
        for line in git("status", "--porcelain").splitlines():
            if len(line) > 3:
                path = line[3:].strip()
                if path.split("/", 1)[0].lstrip(".") in ("git", "claude"):
                    continue  # hide the agent's internal .git/.claude session plumbing
                flag = line[:2].strip()[:1] or "M"
                changes.append({"path": path, "kind": "A" if flag in ("A", "?") else flag})
        viewer_email = f"{viewer}@vexa.local" if viewer else None
        commits = []
        # %an·%ae carry the D4 attribution: a member's agent commit is authored as its principal
        # (name=<subject>, email=<subject>@vexa.local); platform/seed commits are the plumbing authors.
        # --name-only appends each commit's changed files (so the terminal can make them clickable);
        # \x1e prefixes each commit record so we can split records and separate meta from the file list.
        # %ct = committer unix timestamp — a sortable key so a cross-workspace activity feed can merge
        # commits from several mounts by recency (the %cr relative string can't be sorted).
        raw = git("log", "-8", "--name-only", "--pretty=format:%x1e%h\x1f%s\x1f%cr\x1f%an\x1f%ae\x1f%ct")
        for rec in raw.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            lines = rec.split("\n")
            parts = lines[0].split("\x1f")
            if len(parts) != 6:
                continue
            sha, msg, when, an, ae, ct = parts
            if ae in _SYSTEM_AUTHOR_EMAILS or an in _SYSTEM_AUTHOR_NAMES:
                kind = "system"          # policy/seed plumbing — never a member's agent push
            elif viewer_email and ae == viewer_email:
                kind = "you"             # the caller's own agent write
            else:
                kind = "member"          # ANOTHER member's agent pushed this
            files = [
                f.strip() for f in lines[1:]
                if f.strip() and f.split("/", 1)[0].lstrip(".") not in ("git", "claude")
            ][:20]                       # cap: a root/seed commit can touch hundreds
            commits.append({"sha": sha, "msg": msg, "when": when, "author": an, "kind": kind,
                            "files": files, "ts": int(ct) if ct.isdigit() else 0})
        return {"branch": git("rev-parse", "--abbrev-ref", "HEAD") or "main", "changes": changes, "commits": commits}

    def git_diff_at(self, base: Path, sha: str, path: Optional[str] = None) -> dict:
        """Unified diff of ONE commit (optionally scoped to a single file) in the workspace at ``base`` —
        so the terminal can HIGHLIGHT exactly what changed. Capped so a huge commit can't flood the UI."""
        import re
        import subprocess

        from shared.gitenv import scrubbed_git_env

        base = self._guard_under_root(base)
        if not (base / ".git").exists() or not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha or ""):
            return {"sha": sha, "path": path, "diff": "", "truncated": False}  # bad sha never hits git
        args = ["git", "-C", str(base), "show", "--no-color", "--format=", sha]
        if path:
            args += ["--", path]
        out = subprocess.run(args, capture_output=True, text=True, env=scrubbed_git_env()).stdout
        lines = out.splitlines()
        return {"sha": sha, "path": path, "diff": "\n".join(lines[:600]), "truncated": len(lines) > 600}
