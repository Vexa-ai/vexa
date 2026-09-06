"""workspace_reader.py — read a subject's workspace (the git knowledge graph) for the Workspace surface.

Read-only view over the per-subject workspace dir the chat runner maintains. Hides `.git`/`.claude`
internals and guards against path traversal (a read path can never escape the subject's workspace).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from workspaces.shared import workspace_paths as wpaths


def _tool_op(name: str, args: Optional[dict] = None) -> dict:
    """Classify a claude tool name into one of the terminal's op labels (read/search/edit/git/web/tool).
    Mirrors the frontend ``toolOp`` so the loaded history reads the same as a live turn — including the
    touched ``file`` (+``wrote``) that powers the transcript's actionable file chips."""
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
    op = {"label": label}
    fp = (args or {}).get("file_path")
    if isinstance(fp, str) and fp:
        op["file"] = fp
        op["wrote"] = label == "edit"
    return op


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

# TEMPLATES ARE NOT RECORDS. `kg/templates/` holds the SHAPE of an entity — a skeleton with
# `<Full Name>` where a name goes — and every prose file in the workspace says it is never
# knowledge. Nothing enforced that: the shapes carry conformant `type/id/title` frontmatter, and
# `tree_at` is the single enumerator behind the Files tree, the MCP `workspace_tree`, the client's
# link resolver and its find-file index, so an agent asked "what meetings do I have" could read one
# and answer with it. The founder's rule is that the SYSTEM must know a template is a template.
#
# Two tests, because both are needed: the PATH covers the shipped files before anyone edits their
# frontmatter, and the `template: true` FLAG covers a shape copied anywhere else. `hidden=True`
# still shows them — a human browsing deliberately is not the failure mode.
_RESERVED_PREFIXES = ("kg/templates/",)
_TEMPLATE_FM = re.compile(r"^(?:template|example):\s*true\b", re.M)

# Hiding a shape from every ENUMERATOR is only half of it — `read` is a second door, and it was
# open: `GET /api/workspace/file` and the MCP `workspace_read` take a path the agent supplies, so a
# shape it saw quoted anywhere (a prose file, an earlier reply, a guess) still came back as plain
# markdown with conformant `type/id/title` frontmatter and read exactly like a record.
#
# The answer is NOT a refusal: creating an entity legitimately means looking at its shape first.
# It is that the bytes must announce what they are, before the frontmatter, in the same read.
_TEMPLATE_BANNER = (
    "TEMPLATE — THIS IS THE SHAPE OF AN ENTITY, NOT A RECORD.\n"
    "Nothing below is a real person, company or meeting: the angle-bracket fields are blanks.\n"
    "Never cite it, never name it, never list or count it as prior context, and never copy a\n"
    "placeholder value into an answer. Read it only to learn the shape you are about to fill.\n"
    "\n"
)


# ── WHAT THE PERSON SAID, AND WHAT THE MACHINE SAID (F47/F51) ────────────────────────────────────
#
# A transcript line is the prompt the harness was GIVEN, not the sentence somebody typed: the worker
# prepends voice/kg-links/mounts/entity-index/global-context preambles and the control plane folds
# its grounding in front of that. Until now the terminal reconstructed the human half by STRIPPING
# all of it — a sentinel cut when one was present, else regexes matched against the preambles'
# wording — and on 2026-09-02 a changed preamble set made every stored turn in the founder's chat
# render as a grey USER bubble full of machinery with his own sentence at the bottom.
#
# So the worker now writes the human half down as its own field beside the continuity pointer
# (``worker/engine.py`` ``record_user_text`` → ``.claude/sessions/<session>.turns.jsonl``, one JSON
# object per turn: the sha256 of the exact composed prompt, and the person's words). This reader
# looks the stored prompt up by that digest and serves ``user_text`` alongside ``text``. It reads no
# English and knows nothing about preambles; a turn with no record simply carries no ``user_text``,
# and the terminal's strip stays as the fallback for everything written before the field existed.
_TURNS_SIDECAR = "{session}.turns.jsonl"

# The write-back phase runs in the SAME harness session as the turn it follows, so its prompt and
# its reply are in this transcript. The phase declares itself with this mark — ONE literal now, in
# ``shared/marks.py``; the worker reads the same constant under its historical name WRITEBACK_MARK.
# Everything from a marked prompt until the next thing a person actually said is bookkeeping the
# founder was never meant to read back as his own conversation.
from shared.marks import PHASE_MARK, act_label  # noqa: E402 — re-export under this module's long-standing name

# …and the salvage for a turn dispatched BEFORE any of those marks existed (Vexa-ai/vexa#1605):
# a flow's composed kick names its own kind in its first bracket, which is the only thing a
# record with no mark still carries.
from shared.chat_label import composed_label  # noqa: E402 — see shared/chat_label.py

# The harness's own auto-continue. It is a user line nobody typed — the runner nudging a turn that
# stopped early — and it rendered as a grey USER bubble reading "Continue from where you left off."
# Dropping it WITHOUT flushing the open agent turn also re-joins the answer it interrupted, which is
# what the reader was always meant to show: one reply, not two halves with machinery between them.
_HARNESS_CONTINUE = "Continue from where you left off."


def _user_text_index(roots: "list[Path]", session: str) -> list[dict]:
    """Every ``{key, user_text}`` record the worker wrote for this thread, oldest first.

    Searched across the same roots the pointer and transcript are, because a thread that MOVED
    anchors leaves them apart. Tolerant like everything else here: an unreadable or malformed
    sidecar yields no records, and history degrades to the terminal's fallback strip."""
    out: list[dict] = []
    for ws in roots:
        f = ws / ".claude" / "sessions" / _TURNS_SIDECAR.format(session=session)
        try:
            if not f.is_file():
                continue
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict) and isinstance(rec.get("user_text"), str) and rec.get("key"):
                out.append(rec)
    return out


# THE SEED'S BLANKS ARE NOT FACTS EITHER (F53). Workspaces created before the template-free seed
# still hold its README and dashboard skeletons, whose fields read `(unset)`. Those pages carry no
# frontmatter at all, so the `template: true` rule above cannot reach them — and an agent reported
# "the project's objective is still `(unset)`" to the founder as though it had learned something.
#
# It cannot be fixed by re-seeding: those workspaces exist, they are the users', and nobody is going
# to rewrite them. So the page announces itself IN THE SAME READ, exactly as a template does. Unlike
# a template it stays VISIBLE in the tree — it is a real page of theirs waiting to be filled in, not
# a shape that should never have been enumerable — and the banner says what to do with a blank.
_UNSET_MARKER = "(unset)"
_UNFILLED_BANNER = (
    "UNFILLED — THIS PAGE IS STILL THE SEED'S SKELETON WHERE IT SAYS `(unset)`.\n"
    "Each `(unset)` is a BLANK nobody has filled in yet, never a value. Never report one as an\n"
    "answer ('the objective is (unset)') and never copy one into a record: say the thing is not\n"
    "recorded yet, and offer to fill it in from what this turn knows.\n"
    "\n"
)


def _is_template_doc(p: Path) -> bool:
    """Does this file DECLARE itself a shape? Frontmatter only, and only the head of it."""
    if p.suffix.lower() != ".md":
        return False
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(800)
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    return bool(_TEMPLATE_FM.search(head if end == -1 else head[:end]))


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
                rel = str(p.relative_to(ws))
                if not hidden and (rel.startswith(_RESERVED_PREFIXES) or _is_template_doc(p)):
                    continue
                out.append(rel)
        return out

    def read(self, subject: str, path: str) -> Optional[str]:
        """The text at ``path`` within the subject's own workspace, or None if absent. Traversal-guarded."""
        return self.read_at(self._ws(subject), path)

    def read_at(self, base: Path, path: str) -> Optional[str]:
        """The text at ``path`` within the ``base`` workspace dir, or None if absent. Traversal-guarded.

        A template (by reserved PATH or by ``template: true`` FLAG — the same two tests ``tree_at``
        applies) comes back with ``_TEMPLATE_BANNER`` prepended, so a shape can never be read as a
        record. Deliberately not a refusal: the shape is what you consult to write a real entity."""
        ws = self._guard_under_root(base)
        try:
            f = wpaths.resolve_inside(ws, path)   # absolute · `..` · symlink-out · `.git`/`.vexa`
        except wpaths.PathRefused as exc:
            raise ValueError(str(exc)) from None
        if not (f.exists() and f.is_file()):
            return None
        text = f.read_text()
        rel = f.relative_to(ws).as_posix()
        if rel.startswith(_RESERVED_PREFIXES) or _is_template_doc(f):
            return _TEMPLATE_BANNER + text
        if _UNSET_MARKER in text:
            return _UNFILLED_BANNER + text
        return text

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
            # LAST RESORT — threads recorded BEFORE continuity anchoring sit under whatever workspace
            # was the turn's cwd at the time, which may no longer be mounted (deactivated / membership
            # gone). Two fixed-depth globs over the store root find the pointer; read-only + bounded.
            for pat in (f"*/.claude/sessions/{session}.session",
                        f".attached/*/*/.claude/sessions/{session}.session"):
                for f in self._root.glob(pat):
                    ws = f.parents[2]
                    sid = self._session_id(ws, session)
                    if sid:
                        roots.append(ws)
                        break
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
        # The person's own words for each turn, keyed by the digest of the composed prompt (F47).
        records = _user_text_index(roots, session)
        by_key = {r["key"]: r["user_text"] for r in records}
        unused = list(records)
        # Are we inside a write-back phase exchange (F51)? Set by a marked prompt, cleared by the
        # next thing a person actually said. While it is on, the agent's replies are bookkeeping.
        in_phase = False

        def flush_agent() -> None:
            nonlocal cur_agent
            if cur_agent is None:
                return
            open_turn, cur_agent = cur_agent, None
            if in_phase:
                return  # the phase's reply — nobody was ever shown it, and nobody asked for it
            # An agent turn with neither prose nor a single operation is not a turn: it is the
            # residue of a beat that produced nothing, and it rendered as an empty grey card.
            if not open_turn["text"].strip() and not open_turn["ops"]:
                return
            turns.append(open_turn)

        def human_words(stored: str) -> Optional[str]:
            """What the person typed for a stored prompt, or None if nothing recorded it.

            Exact first: the digest of the bytes the harness was handed. The suffix pass behind it
            is belt — it costs one comparison and it survives a harness that ever decorates the
            prompt on its way into the transcript — and it is still a MACHINE test (is this recorded
            string the tail of that stored one), never a reading of what either says."""
            hit = by_key.get(hashlib.sha256(stored.encode("utf-8")).hexdigest())
            if hit is not None:
                return hit
            for rec in unused:
                ut = rec["user_text"]
                if ut and stored.endswith(ut):
                    unused.remove(rec)
                    return ut
            return None

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
                if not text.strip():
                    continue
                # The harness nudging itself is not speech (F51). No flush: the open agent turn keeps
                # accumulating, so the answer this interrupted comes back as ONE reply.
                if text.strip() == _HARNESS_CONTINUE:
                    continue
                flush_agent()
                if PHASE_MARK in text:
                    in_phase = True   # …and everything the agent says until the next real prompt
                    continue
                in_phase = False
                turn: dict = {"role": "user", "text": text}
                # `user_text` is the person's own words as a FIELD; `text` stays the stored prompt so
                # a record written before the field existed still reaches the terminal's fallback.
                #
                # AN ACT IS ITS LABEL, WHATEVER WAS RECORDED (Vexa-ai/vexa#1588). A marked act's
                # `user_text` was the composed preset until the worker learned better, and those
                # records are in people's own transcripts and are not ours to rewrite. The mark is
                # in the stored prompt, so the label is derivable here on every read — which also
                # covers a worker one release behind a terminal that already sends intents.
                said = act_label(text)
                if said is None:
                    said = human_words(text)
                    # …AND A RECORD WRITTEN BEFORE THE MARK IS STILL NOT SPEECH (#1605). A flow
                    # dispatched the founder's post-meeting turn; the worker recorded the whole
                    # composed kick as his half, because on a turn nobody typed there IS no other
                    # half to record. `composed_label` reads the kind that kick opens with — the one
                    # thing left in the record that says a machine wrote it — and answers "" for
                    # everything else, so a sentence somebody typed stays their sentence.
                    if said is not None:
                        said = composed_label(said) or said
                if said is not None:
                    turn["user_text"] = said
                turns.append(turn)
            elif kind == "assistant":
                if not isinstance(content, list):
                    continue
                if cur_agent is None:
                    cur_agent = {"role": "agent", "text": "", "ops": []}
                cur_agent["text"] += _block_text(content)
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        cur_agent["ops"].append(_tool_op(b.get("name", ""), b.get("input") if isinstance(b.get("input"), dict) else None))
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
        if path is not None:
            # The path is a PATHSPEC handed to `git show` — the same caller-supplied string every
            # other route guards, and `git show <sha> -- ../x` reads out of the workspace.
            try:
                wpaths.resolve_inside(base, path)
            except wpaths.PathRefused as exc:
                raise ValueError(str(exc)) from None
        if not (base / ".git").exists() or not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha or ""):
            return {"sha": sha, "path": path, "diff": "", "truncated": False}  # bad sha never hits git
        args = ["git", "-C", str(base), "show", "--no-color", "--format=", sha]
        if path:
            args += ["--", path]
        out = subprocess.run(args, capture_output=True, text=True, env=scrubbed_git_env()).stdout
        lines = out.splitlines()
        return {"sha": sha, "path": path, "diff": "\n".join(lines[:600]), "truncated": len(lines) > 600}

    def git_reset_to(self, base: Path, sha: str) -> dict:
        """UNDO commits made after ``sha`` in the workspace at ``base``, and nothing else.

        THE ONE THING A HUMAN HAD TO DO BY HAND (Vexa-ai/vexa#1606). `process_meeting`'s decision-22
        detector records the organiser's desk HEAD before the post-meeting turn and refuses the step
        when it moved. Twice on 2026-09-06 the recovery was a person opening a shell, resetting that
        repository to the sha in the error, and re-firing the reaction — so the check was loud,
        correct, and un-actionable by the system that raised it. This is that shell command, with
        the two properties a shell command does not have.

        BACKWARD ONLY, AND ONLY ALONG THIS HISTORY. ``sha`` must be a real commit AND an ancestor of
        the current HEAD; anything else is refused. So this can only ever remove commits that landed
        after the witness was taken — it can never fast-forward a desk onto work it has not done, and
        it cannot be aimed at an unrelated history. A caller who could do either would be able to
        rewrite a person's desk by naming a sha, which is a much larger capability than undoing a
        write this same turn is known to have made.

        HARD, and that is deliberate: the stray commit is the whole problem, and a soft reset would
        leave its contents staged for the next writer to commit under a different message. Returns
        ``{"before", "after", "reset", "detail"}``; ``reset`` False with a ``detail`` is the refusal,
        never an exception — the caller is a flow step whose next move is to say why it could not."""
        import re
        import subprocess

        from shared.gitenv import scrubbed_git_env

        base = self._guard_under_root(base)
        if not (base / ".git").exists():
            return {"before": "", "after": "", "reset": False, "detail": "not a git workspace"}
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha or ""):
            return {"before": "", "after": "", "reset": False,
                    "detail": f"{sha!r} is not a commit id"}

        def git(*args: str):
            return subprocess.run(["git", "-C", str(base), *args], capture_output=True, text=True,
                                  env=scrubbed_git_env())

        before = git("rev-parse", "HEAD").stdout.strip()
        if not before:
            return {"before": "", "after": "", "reset": False, "detail": "the workspace has no HEAD"}
        if before.startswith(sha.lower()):
            return {"before": before, "after": before, "reset": False, "detail": "HEAD is already there"}
        if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            return {"before": before, "after": before, "reset": False,
                    "detail": f"{sha} is not a commit in this workspace"}
        if git("merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
            return {"before": before, "after": before, "reset": False,
                    "detail": f"{sha[:9]} is not an ancestor of HEAD — this only ever undoes "
                              f"commits made after the sha it is given"}
        r = git("reset", "--hard", sha)
        after = git("rev-parse", "HEAD").stdout.strip()
        if r.returncode != 0 or not after.startswith(sha.lower()):
            return {"before": before, "after": after, "reset": False,
                    "detail": (r.stderr or r.stdout or "reset failed").strip()[:300]}
        return {"before": before, "after": after, "reset": True, "detail": ""}
