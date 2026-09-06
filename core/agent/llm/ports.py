"""ports.py — the provider-agnostic port of the llm module (mirrors runtime_kernel/backend.py).

ONE call shape, one port:

- ``HarnessPort`` — a CLI coding agent driven over a mounted workspace: the tool loop, sessions,
  streamed UnitEvents. Chat, routines and every agent turn run here.

There was a second, ``CompletionPort`` — a plain prompt→text HTTP call with no tools and no
workspace — and its only caller was the live meeting copilot's card beats. PRD decision 34 removed
that pipeline, and the port went with it.

It is a ``typing.Protocol`` — duck-typed like the runtime ``Backend`` port, so adapters need no
base class and tests inject trivial fakes. Adapter selection is env-driven in ``registry.py``.

The UnitEvent stream contract every harness adapter must emit (shapes FROZEN — the terminal
reducer + SSE relay consume them):
  ``{"type":"message-delta","text":…}`` · ``{"type":"tool-call",tool,args,callId}`` ·
  ``{"type":"tool-result",callId,ok,summary}`` · ``{"type":"done",reply,sessionId,ok}`` ·
  and (from ``run_harness_turn``) ``{"type":"commit","sha":…}``.

This module imports NOTHING from product code — it must stay liftable into a standalone brick.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Protocol

# Env vars that redirect git's repo/worktree/index/object discovery away from cwd. Git HOOKS
# export GIT_DIR (and friends) into their descendants; a git subprocess inheriting them operates
# on the HOOK's repo with its own cwd as the work tree — a workspace commit then REWRITES the
# hook's branch. Deliberately a module-local twin of ``shared.gitenv`` (this module owns zero
# product imports so it stays liftable, same stance as the local ``_git`` below).
_GIT_REPO_DISCOVERY_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                            "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                            "GIT_ALTERNATE_OBJECT_DIRECTORIES")


def scrubbed_git_env() -> dict[str, str]:
    """``os.environ`` minus every git repo-discovery redirect — cwd-based discovery, always.
    Used for the local ``_git`` (the worker's OWN commits) AND as the base for launching harness CLIs
    (they shell out to git in the workspace and would inherit the same poisoned discovery)."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_REPO_DISCOVERY_VARS}


# Host DATA-PLANE secrets the worker PROCESS legitimately holds — it drives redis (the serve loop, the
# meeting/processed streams) and carries the minted per-dispatch token — but that the UNTRUSTED model
# subprocess must NEVER inherit. A harness CLI exposes a Bash tool to the model; with ``REDIS_URL`` in its
# environment that Bash reaches the SHARED redis and can read/write ANOTHER tenant's ``tc:meeting:*`` /
# ``unit:*:in`` keys — filesystem tenancy is mount-enforced, the data plane is not. The per-dispatch
# identity token is a bearer secret the subprocess has no use for. The model DOES need its MODEL
# credentials (ANTHROPIC_*/CLAUDE_CODE_OAUTH_TOKEN) to talk to the provider, so those are
# deliberately absent here — this is the tight denylist of vars the model has no legitimate reason to hold.
_HARNESS_SUBPROCESS_DENY_VARS = ("REDIS_URL", "VEXA_AGENT_IDENTITY_TOKEN")


def harness_subprocess_env() -> dict[str, str]:
    """The env for launching an UNTRUSTED model-driven harness subprocess: ``scrubbed_git_env()`` further
    stripped of the host data-plane secrets in ``_HARNESS_SUBPROCESS_DENY_VARS``. Use this — never a raw
    ``os.environ`` / ``scrubbed_git_env`` — to spawn a harness CLI: its Bash tool would otherwise inherit
    the worker's own ``REDIS_URL`` and cross the data-plane tenancy boundary the mounts enforce on disk.

    It also pins ``ENABLE_TOOL_SEARCH``, which decides whether the harness hands MCP tools to the
    model DIRECTLY or as DEFERRED ones the model must find and load before it can call them. The
    deferred round trip is where this product loses turns: measured on Haiku, 1 dispatch in 8 never
    completes it and writes a confident note with nothing from the meeting in it, and others end
    with the model explaining that "the tool appears in the deferred MCP tools list, but I don't
    have a direct function invocation" and writing nothing at all.

    The threshold is a share of the CONTEXT, not a count of tools — which is why it bites here and
    not in a small test: a worker carries the mount preamble, a long post-meeting prompt and 53
    tool schemas from the rig, on the smallest model. ``auto:100`` sets that share to 100%, so the
    tools are always present. (Naming them in ``--allowedTools`` does NOT do this — that is a
    permission gate and cannot reach the harness's context management. Measured, and corrected in
    ``worker/engine.py``.)

    Deliberately overridable: a deployment that wants the harness's own judgement sets the variable
    itself and this leaves it alone."""
    env = {k: v for k, v in scrubbed_git_env().items() if k not in _HARNESS_SUBPROCESS_DENY_VARS}
    env.setdefault("ENABLE_TOOL_SEARCH", "auto:100")
    return env


# A raw process runner: given an argv + a cwd, yield the process's stdout lines. Injected into CLI
# harness adapters so their parsers are offline-provable with a fake (no CLI, no network).
HarnessExec = Callable[[list[str], str], Iterable[str]]


class HarnessPort(Protocol):
    """A CLI coding agent driven over a workspace. ``run_turn`` yields the UnitEvent stream
    documented above; the session id is an OPAQUE per-harness token (an alien/stale id must yield
    ``done.ok=False``, which the engine's stale-resume retry heals)."""

    name: str

    def run_turn(self, work: Path, prompt: str, *, allowed_tools: Iterable[str] = (),
                 session: Optional[str] = None, model: Optional[str] = None,
                 mcp_config: Optional[str] = None) -> Iterator[dict]: ...

    def prepare(self, work: Path, chat_root: Optional[Path] = None) -> None:
        """Harness-specific workspace hooks before a turn (continuity/skills wiring). ``chat_root``
        anchors chat continuity (session store + transcripts) when it must live OUTSIDE the turn's
        cwd — the flat model can point the cwd at a SHARED workspace, and chats are private to the
        subject. None ⇒ continuity stays under ``work`` (legacy). May no-op."""
        ...

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        """Size of the stored transcript behind ``session_id`` (resume-cost accounting); 0 if unknown."""
        ...

    def preflight(self) -> Optional[str]:
        """Boot-time credential sanity check — a loud warning string, or None. May no-op."""
        ...

    def midturn_enabled(self) -> bool:
        """Whether this runner accepts user input while ``run_turn`` is still active."""
        ...

    def inject_user_message(self, text: str) -> bool:
        """Append user input to the active turn. False means leave it queued for the next turn."""
        ...


def _git(work: Path, *args: str, env: Optional[dict] = None) -> str:
    """Local git runner (trimmed stdout). Deliberately NOT shared.adapters._git — this module owns
    zero product imports so it stays liftable. Scrubbed env: the turn commit must land on ``work``,
    never on a repo a hook exported via GIT_DIR. ``env`` (optional) layers extra vars (the principal
    ``GIT_AUTHOR_*``) over the scrubbed base."""
    run_env = scrubbed_git_env()
    if env:
        run_env.update(env)
    proc = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True, check=True,
                          env=run_env)
    return proc.stdout.strip()



# The harness continuity store. Kept out of git history at the commit seam as well as by the seed's
# `.gitignore` — see _commit_mount for why it is dropped from the index rather than excluded by a
# pathspec.
_CONTINUITY_DIR = ".claude"

# The platform-write-only subtree of every workspace repo. Agent turns must NEVER modify it
# (membership lives here — see control_plane.workspace_membership). Kept as a bare string so
# this module stays product-import-free (it is liftable into a standalone brick). The control plane's
# membership writer commits policy/ directly; a turn that touches it is reverted here before the commit.
_POLICY_DIR = "policy"

# ⚠ THE PLATFORM WRITES policy/ WHILE THE TURN IS RUNNING, AND THIS GUARD USED TO DELETE THAT WRITE.
#
# Founder, 2026-09-07, opening an invite the agent had minted a minute earlier: *"This invite link is
# not valid. Ask whoever sent it for a new one."* The workspace's own history said why, twice an hour:
#
#     8dfff9b 19:28:08  policy: mint invite 41cdb3b6a5841ffc (contributor) for oenb-b5e60c
#     1a452f9 19:28:09  oenb-b5e60c: policy/invites.json — removed
#
# The mint is agent-api, writing its own store during the turn. The removal one second later is THIS
# file: the guard captured HEAD before the turn, rebuilt the whole `policy/` subtree from it after,
# and `_commit_mount` then committed the deletion. `policy/` is one write surface with two writers,
# and the second one deleted what the first had just put there — the exact failure `Operating-Loops`
# names, and it failed silently and plausibly, as that class always does.
#
# Two changes, and they are different in kind:
#   * the ANCHOR is no longer "HEAD before the turn". It is advanced over every commit made DURING
#     the turn that carries the platform's own signature (`_is_platform_policy_commit`), so the
#     platform's mid-turn writes are the baseline rather than the thing being reverted;
#   * the RESTORE is no longer a purge-and-rebuild of the directory. It touches only the paths that
#     actually differ from the anchor, so a turn that changed nothing under `policy/` — which is
#     nearly all of them — does not delete and re-checkout a directory another process is writing.
#
# WHAT THE SIGNATURE IS AND IS NOT. It is positive evidence — a shape the platform always emits —
# rather than a comparison against a remembered value, because a remembered baseline goes stale the
# moment the other writer moves, which is the whole bug above. It is NOT a boundary against a hostile
# shell: the agent toolset includes Bash inside this very mount, so an agent that wants to forge a
# commit can forge this one too. Nothing in-tree can stop that, and nothing in-tree ever could. The
# boundary is the MOUNT TABLE, which is why the invite store — the only capability material policy/
# ever held — now lives at `<store-root>/.invites/`, outside every workspace bind
# (`runtime_kernel.mounts.workspace_binds` emits one subpath bind per mounted workspace and never the
# store root). What is left here is the member roster, which travels with the workspace on purpose,
# and for it this guard remains what it always was: a heal that undoes a turn's writes.
_PLATFORM_COMMITTER_EMAIL = "platform@vexa.ai"
_POLICY_SUBJECT_PREFIX = "policy: "


def _policy_head_sha(work: Path) -> Optional[str]:
    """The current HEAD sha, or ``None`` if the repo has no commit yet (freshly-init'd workspace).
    Captured BEFORE a turn runs — while HEAD still reflects the PLATFORM's last policy commit and no
    agent tool has had a chance to move it — so the post-turn guard has a starting anchor."""
    try:
        return _git(work, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return None


def _is_platform_policy_commit(work: Path, sha: str) -> bool:
    """Does ``sha`` carry the signature ``workspace_membership._policy_commit`` always emits?

    Three properties together, all of them things the platform writer does BY CONSTRUCTION and the
    turn-commit path does not: the committer is the platform identity, the subject opens with
    ``policy: ``, and the commit touches NOTHING outside ``policy/`` (it is made with an explicit
    ``-- policy`` pathspec). The turn commit fails the third on any turn that wrote a file, and the
    second on every turn — its subject is ``<workspace>: <path> — <verb>``."""
    try:
        meta = _git(work, "show", "-s", "--format=%ce%n%s", sha)
        names = _git(work, "show", "--name-only", "--format=", sha)
    except subprocess.CalledProcessError:
        return False
    lines = meta.splitlines()
    if len(lines) < 2:
        return False
    committer, subject = lines[0].strip(), lines[1].strip()
    if committer != _PLATFORM_COMMITTER_EMAIL or not subject.startswith(_POLICY_SUBJECT_PREFIX):
        return False
    paths = [p.strip() for p in names.splitlines() if p.strip()]
    return bool(paths) and all(p.startswith(_POLICY_DIR + "/") for p in paths)


def _policy_anchor(work: Path, base_sha: Optional[str]) -> Optional[str]:
    """The sha whose ``policy/`` tree the guard restores to: ``base_sha``, advanced over the platform's
    OWN policy commits made since the turn started.

    Walked oldest-first and stopped at the first commit that is not the platform's, so an agent
    self-commit can never become the anchor even when a platform commit lands after it."""
    if not base_sha:
        return base_sha
    try:
        walk = _git(work, "rev-list", "--reverse", f"{base_sha}..HEAD")
    except subprocess.CalledProcessError:
        return base_sha
    anchor = base_sha
    for sha in (ln.strip() for ln in walk.splitlines()):
        if not sha or not _is_platform_policy_commit(work, sha):
            break
        anchor = sha
    return anchor


def _list_policy_paths_at(work: Path, ref: str) -> set[str]:
    """The set of ``policy/`` file paths tracked at ``ref`` (empty if none / ref invalid)."""
    try:
        out = _git(work, "ls-tree", "-r", "--name-only", ref, "--", _POLICY_DIR)
    except subprocess.CalledProcessError:
        return set()
    return {ln.strip() for ln in out.splitlines() if ln.strip().startswith(_POLICY_DIR + "/")}


def _current_policy_entries(work: Path) -> set[str]:
    """Every path that currently lives under ``policy/`` in the working tree — tracked, staged,
    untracked, or a symlink — so the restore can delete anything the baseline did not contain."""
    entries: set[str] = set()
    # Tracked + staged (index) entries under policy/.
    try:
        for ln in _git(work, "ls-files", "--", _POLICY_DIR).splitlines():
            if ln.strip():
                entries.add(ln.strip())
    except subprocess.CalledProcessError:
        pass
    # Untracked entries under policy/ (ignored ones are enumerated separately — see
    # ``_excluded_policy_entries``, which is what keeps an ATTACHED workspace's member list alive).
    try:
        for ln in _git(work, "ls-files", "--others", "--exclude-standard", "--", _POLICY_DIR).splitlines():
            if ln.strip():
                entries.add(ln.strip())
    except subprocess.CalledProcessError:
        pass
    # And whatever is physically on disk (catches a symlinked-in file or a dir the index doesn't know).
    policy_root = work / _POLICY_DIR
    if policy_root.exists() or policy_root.is_symlink():
        if policy_root.is_symlink() or not policy_root.is_dir():
            entries.add(_POLICY_DIR)
        else:
            for child in policy_root.rglob("*"):
                if child.is_file() or child.is_symlink():
                    entries.add(child.relative_to(work).as_posix())
    return entries


def _excluded_policy_entries(work: Path) -> set[str]:
    """``policy/`` paths git is deliberately IGNORING in this clone — the platform's untracked store.

    ⚠ THE ACCESS WIPE THIS PREVENTS (found working Vexa-ai/vexa#1645). An ATTACHED shared workspace —
    a group whose tree is somebody's cloned GitHub repo — keeps its member list untracked on purpose:
    ``workspace_attach.carry_policy`` copies ``policy/`` across the swap and adds it to the clone's
    ``.git/info/exclude``, because committing our subject ids into their repository would push them,
    and because one local commit diverges the fresh clone. ``read_members`` reads the WORKING TREE, so
    the grant is real and authority is unaffected.

    To a baseline-diffing guard, that file is indistinguishable from an agent's untracked add: absent
    from every commit, present on disk. Deleting it drops every member of that workspace — ``is_member``
    answers None for all of them and the group disappears from every active set — which is the exact
    outcome ``carry_policy`` exists to prevent, undone one turn later by the guard. An agent's own add
    is never ignored (nothing puts ``policy/`` in a seeded workspace's ``.gitignore``), so the ignore
    bit separates the two."""
    try:
        raw = _git(work, "ls-files", "--others", "--ignored", "--exclude-standard", "--", _POLICY_DIR)
    except subprocess.CalledProcessError:
        return set()
    return {ln.strip() for ln in raw.splitlines() if ln.strip()}


def _policy_paths_differing_from(work: Path, anchor: str) -> set[str]:
    """Tracked ``policy/`` paths whose working-tree or index state differs from ``anchor``."""
    out: set[str] = set()
    for args in (("diff", "--name-only", anchor, "--", _POLICY_DIR),
                 ("diff", "--name-only", "--cached", anchor, "--", _POLICY_DIR)):
        try:
            raw = _git(work, *args)
        except subprocess.CalledProcessError:
            continue
        out |= {ln.strip() for ln in raw.splitlines() if ln.strip()}
    return out


def _revert_policy_writes(work: Path, base_sha: Optional[str]) -> list[str]:
    """Restore ``policy/`` to the PLATFORM's own state — the Q3 write-guard (post-turn validation +
    revert). ``policy/`` is PLATFORM-WRITE-ONLY; the agent toolset includes ``Bash``, so a turn can
    ``git add policy/ && git commit`` its own tamper and a working-tree scan would then see a clean
    tree with the forgery living in HEAD. So the guard is anchor-based, not scan-based.

    THE ANCHOR IS ``_policy_anchor``, NOT ``base_sha`` — see the note above ``_PLATFORM_COMMITTER_EMAIL``.
    ``base_sha`` is HEAD as captured before the turn; the anchor is that sha advanced over the platform's
    own policy commits made since, so an invite or a membership the platform wrote WHILE the turn ran is
    the baseline rather than the thing being deleted. Restoring to ``base_sha`` is what removed every
    minted invite one second after it was minted.

    AND IT NEVER SYNCS A TREE. Only the paths that actually differ from the anchor are touched:
    tracked paths are checked out from it, and untracked entries — which the platform never leaves
    behind, since ``_policy_commit`` commits by pathspec as part of the write — are removed as
    agent-authored. A turn that did not touch ``policy/`` (nearly every turn) returns ``[]`` having
    written nothing at all, so this guard can no longer delete a file it never had.

    Returns the affected paths so the caller can flag them."""
    anchor = _policy_anchor(work, base_sha)
    baseline = _list_policy_paths_at(work, anchor) if anchor else set()
    current = _current_policy_entries(work)

    # AGENT-AUTHORED ADDITIONS: present now, absent from the anchor, and NOT the platform's
    # deliberately-untracked store (an attached workspace's carried member list — see
    # `_excluded_policy_entries`). Includes the whole subtree of a `policy/` that did not exist at the
    # anchor at all (a freshly-seeded workspace).
    added = {p for p in current if p not in baseline} - _excluded_policy_entries(work)
    # AGENT-AUTHORED EDITS: a baselined path whose content or index entry moved off the anchor.
    changed = _policy_paths_differing_from(work, anchor) & baseline if anchor else set()

    affected = added | changed
    if not affected:
        return []

    # 1) Drop the agent's additions from index + working tree, path by path. `policy` itself appears
    #    in `current` only when it is a symlink or a plain file (never a directory), so unlinking is
    #    always the right verb here — there is no tree to walk and none to remove.
    for path in sorted(added):
        try:
            _git(work, "rm", "-r", "-f", "--cached", "--ignore-unmatch", "--", path)
        except subprocess.CalledProcessError:
            pass
        target = work / path
        try:
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
        except OSError:
            pass

    # 2) Restore every edited baseline path from the anchor (checkout writes index + working tree).
    for path in sorted(changed):
        try:
            _git(work, "checkout", anchor, "--", path)
        except subprocess.CalledProcessError:
            pass

    return sorted(affected)
def _commit_env(author: Optional[tuple[str, str]]) -> dict:
    """Git env for one attributed commit (D4 / WP-A1.2): AUTHOR = the dispatch principal (the
    authenticated human whose input drove the turn), COMMITTER = the platform. Both must be set or git
    falls back to config/global identity — so we always stamp a committer, and the author when known."""
    env = {
        "GIT_COMMITTER_NAME": "Vexa",
        "GIT_COMMITTER_EMAIL": "platform@vexa.ai",
    }
    if author:
        name, email = author
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_AUTHOR_EMAIL"] = email
    return env


# ── the commit SUBJECT names the change, never the agent's reply ─────────────────────────────────
# ⚠ 2026-09-02, seen by the founder in `_global`'s own history:
#     Done — `STRUCTURE.md` records Vexa as run solo by you, and your desk is
#     Here's what's now in `README.md`:
# The turn's REPLY was the commit message, cut at 72 characters. Every consequence follows from
# that one substitution: a `git log --oneline` of the company layer reads as half-sentences
# addressed to somebody who is not there, a truncated "Here's what's now in" promises a colon and
# delivers nothing, and — the part that actually costs — you cannot see WHICH FILE a commit
# touched without opening it. History is the one record a person reads when they are trying to
# find out what happened, and it was answering a different question.
#
# The subject is derived from the staged tree, which is the only thing that knows what changed.
# The reply keeps its value and goes in the BODY, where a sentence belongs.
_SUBJECT_MAX = 72


def _change_subject(work: Path, env: dict) -> str:
    """`<workspace>: <path> — <what changed>`, ≤72 chars, read off the index.

    Deliberately mechanical. A generated summary of a diff is a second thing that can be wrong
    about the diff, and this line's whole job is to be the one part of the record that cannot be."""
    slug = work.name
    try:
        raw = _git(work, "diff", "--cached", "--name-status", env=env) or ""
    except subprocess.CalledProcessError:
        raw = ""
    rows = [ln.split("\t") for ln in raw.splitlines() if "\t" in ln]
    if not rows:
        return f"{slug}: workspace updated"[:_SUBJECT_MAX]
    verbs = {"A": "added", "M": "updated", "D": "removed", "R": "renamed", "C": "copied"}
    if len(rows) == 1:
        code, path = rows[0][0][:1], rows[0][-1]
        return f"{slug}: {path} — {verbs.get(code, 'changed')}"[:_SUBJECT_MAX]
    # Several files: name the first two and count the rest, so the line still says WHERE rather
    # than only how many — "3 files" alone sends the reader to the diff for the thing the subject
    # exists to save them.
    names = [r[-1] for r in rows]
    head = ", ".join(names[:2])
    rest = f" +{len(names) - 2}" if len(names) > 2 else ""
    return f"{slug}: {head}{rest} — {len(names)} files changed"[:_SUBJECT_MAX]


def _staged_policy_deletions(work: Path, env: dict) -> set[str]:
    """``policy/`` paths staged as DELETED in the index (empty when the repo has no commit yet)."""
    try:
        raw = _git(work, "diff", "--cached", "--name-only", "--diff-filter=D", "--", _POLICY_DIR,
                   env=env)
    except subprocess.CalledProcessError:
        return set()
    return {ln.strip() for ln in raw.splitlines() if ln.strip()}


def _commit_mount(work: Path, *, message: str, author: Optional[tuple[str, str]],
                  policy_removed: Iterable[str] = ()) -> Optional[str]:
    """Commit ``work`` if its tree changed, attributed to ``author`` (committer = platform). Returns the
    new HEAD sha, or None on a clean tree. A path with no ``.git`` is skipped (a mount not yet seeded).
    Best-effort per mount: one mount failing to commit must not abort the others.

    ``policy_removed`` is what the policy guard removed in THIS turn — the only ``policy/`` deletions
    this commit is entitled to record. See the un-staging step below."""
    if not (work / ".git").exists():
        return None
    # Harness continuity is private runtime plumbing, never workspace knowledge. Not every legacy
    # auxiliary mount carries the seed's `.gitignore`, so enforce the exclusion at the commit seam
    # too (the documented contract already promises `.claude/` never enters git history).
    #
    # The exclusion is EXPRESSED TWICE ON PURPOSE, and NOT as an ``add`` pathspec. ``git add -A --
    # . ':(exclude).claude'`` EXITS 1 whenever `.claude` is also matched by a `.gitignore` ("The
    # following paths are ignored by one of your .gitignore files") — because an exclude pathspec
    # still counts as explicitly naming the path. Every SEEDED workspace ships that `.gitignore`,
    # so the pathspec form failed on exactly the mounts it was written for, ``check=True`` raised,
    # the caller's ``except CalledProcessError: continue`` swallowed it, and the turn's work was
    # left STAGED AND NEVER COMMITTED — silently, on every chat turn, for every user. Staging
    # everything under ``.`` and then dropping `.claude` from the index is rc-0 in both worlds
    # (with and without a `.gitignore`) and stages `.claude` in neither. ``git rm --cached
    # --ignore-unmatch`` is the same idiom ``_revert_policy_writes`` already uses above.
    content_pathspec = (".", ":(exclude).claude")
    if not _git(work, "status", "--porcelain", "--", *content_pathspec):
        return None
    env = _commit_env(author)
    _git(work, "add", "-A", "--", ".", env=env)
    _git(work, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", _CONTINUITY_DIR, env=env)
    # NEVER RECORD A DELETION OF A `policy/` PATH THIS TURN DID NOT MAKE (Vexa-ai/vexa#1645).
    # `git add -A` stages the tree AS IT FINDS IT, so anything another writer's file happened not to
    # be at that instant is committed as a deletion by whichever turn runs next — which is exactly how
    # `oenb-b5e60c: policy/invites.json — removed` came to sit one second after every mint. The policy
    # guard is the only thing here entitled to remove a `policy/` path and it says which ones it did;
    # every other staged deletion under `policy/` is put back, in the index and on disk, so the commit
    # carries the platform's tree and the next turn does not re-stage the same removal.
    unentitled = _staged_policy_deletions(work, env) - set(policy_removed)
    for path in sorted(unentitled):
        for restore in (("reset", "-q", "HEAD", "--", path), ("checkout", "HEAD", "--", path)):
            try:
                _git(work, *restore, env=env)
            except subprocess.CalledProcessError:
                pass
    if unentitled and not _git(work, "diff", "--cached", "--name-only", env=env):
        return None  # the only "change" was a deletion this turn had no business recording
    # SUBJECT from the tree; the agent's sentence, if there is one, as the BODY. Two `-m` flags is
    # git's own subject/body split, so `--oneline` shows the change and `git show` still carries
    # what the agent said about it — nothing is lost, it is filed where a reader expects it.
    subject = _change_subject(work, env)
    body = (message or "").strip()
    args = ["commit", "-m", subject]
    if body:
        args += ["-m", body]
    _git(work, *args, env=env)
    return _git(work, "rev-parse", "HEAD", env=env)



def close_event_stream(events: object) -> None:
    """Close a harness event stream we have stopped reading — NOW, at the boundary.

    ⚠ THE HOP THAT ONLY LOOKS LIKE IT CLOSES ITSELF. A generator that wraps another one with a
    plain ``for ev in inner:`` releases ``inner`` when its OWN frame is torn down, and that teardown
    is a CPython implementation detail rather than a language guarantee. Measured on
    Vexa-ai/vexa#1434: on CPython 3.12.3, closing the outer generator left the inner one ALIVE
    (``gi_frame`` not ``None``, one referrer — itself a generator), so
    ``llm.claude_code._exec_subprocess``'s ``finally`` never ran and the CLI child was never killed.
    The write-back budget stopped reading the process and bounded nothing; the worker stayed exactly
    as busy as before. The identical tree passed in 0.67 s on 3.12.13. Both interpreters satisfy
    ``requires-python = ">=3.11"``, so this is not a supported-versus-unsupported line — it is a
    guarantee the chain never had, on any interpreter, and got right by luck on most.

    So EVERY hop of the harness event chain closes what it wraps EXPLICITLY (P22 — guarantee
    teardown at the boundary, never delegate it to something that may not run). ``yield from``
    already does this; a ``for`` loop does not, and this call is the whole of the difference.

    A plain iterable with no ``close`` (a list, a test's fake) is a no-op, and closing an exhausted
    or already-closed generator is one too — so it belongs in a ``finally``, on the normal path as
    much as the early-exit one.
    """
    close = getattr(events, "close", None)
    if close is None:
        return
    close()


def run_harness_turn(
    work: Path | str,
    prompt: str,
    harness: HarnessPort,
    *,
    allowed_tools: Iterable[str] = ("Read", "Write", "Edit"),
    session: Optional[str] = None,
    model: Optional[str] = None,
    mcp_config: Optional[str] = None,
    commit_message: Optional[str] = None,
    commit: bool = True,
    author: Optional[tuple[str, str]] = None,
    extra_mounts: Optional[Iterable[Path | str]] = None,
) -> Iterator[dict]:
    """Run one harness turn over ``work``, streaming normalized UnitEvents, then commit EACH mount.

    The workspace is a FREE ZONE: governance is PROMPT-ONLY (workspace conventions guide the
    agent). After the turn, for EVERY writable mount in the active set (``work`` first, then each of
    ``extra_mounts``) whose tree changed, commit INDEPENDENTLY and emit ``{"type":"commit","sha":...}``
    (WP-A1.2: one commit per changed mount). Attribution (D4): the ``author`` (the dispatch principal)
    authors each commit; the committer is always the platform.

    COMPOSED with the policy guard (Lane M / Q3): ``policy/`` is PLATFORM-WRITE-ONLY (the member roster
    lives there; see ``control_plane.workspace_membership``). Each mount is a separate workspace repo that
    may carry its own ``policy/`` subtree, so the guard runs PER MOUNT: we capture that mount's HEAD
    BEFORE the turn (before any agent tool — Bash included — can move it), and AFTER the turn we restore
    that mount's ``policy/`` to its ANCHOR before its commit (emitting
    ``{"type":"policy-reverted","paths":[…]}``), where the anchor is that pre-turn sha advanced over the
    platform's own policy commits made while the turn ran. Net invariant, and it is two-sided now: no
    agent-authored change to ANY mount's ``policy/`` is ever committed, AND no write the PLATFORM made to
    ``policy/`` during the turn is ever deleted (Vexa-ai/vexa#1645 — the second half was missing, and it
    removed every invite one second after it was minted). Every other change commits, authored by the
    principal. ``_global`` (read-only) is never in the commit set. A mount whose ``policy/`` nobody
    touched makes the guard a no-op that writes nothing. (Hard enforcement is available upstream via
    ``shared.governance`` if it needs to come back.)

    ``commit=False`` is the propose-only path (e.g. a read-only turn): NO git is touched — never
    contend on a workspace another agent may be committing to (the index.lock collision).
    """
    work = Path(work)
    # Build the ordered, de-duped commit set NOW — the primary mount first, then every additional
    # writable mount — so we can capture each mount's policy baseline BEFORE the turn runs. Each mount
    # is a separate workspace repo; ``_global`` (read-only) is never passed in extra_mounts.
    mounts: list[Path] = []
    _seen_pre: set[str] = set()
    for _m in [work, *(Path(m) for m in (extra_mounts or ()))]:
        _key = str(Path(_m).resolve())
        if _key in _seen_pre:
            continue
        _seen_pre.add(_key)
        mounts.append(Path(_m))
    # Capture HEAD's policy tree PER MOUNT, BEFORE the turn — while each still reflects the PLATFORM's
    # last policy commit and no agent tool (Bash included) has had a chance to move it. These are the
    # baselines the per-mount policy guard restores policy/ to, so an agent self-commit of a policy
    # tamper in ANY mount cannot survive.
    policy_baselines: dict[str, Optional[str]] = {}
    if commit:
        for _mount in mounts:
            policy_baselines[str(_mount.resolve())] = _policy_head_sha(_mount)
    done: Optional[dict] = None
    # Held in a NAME, not consumed inline, so the `finally` below has something to close. A caller
    # may stop reading this turn mid-stream — the write-back phase's budget does exactly that — and
    # the harness generator underneath owns the CLI subprocess. See `close_event_stream`.
    stream = harness.run_turn(work, prompt, allowed_tools=allowed_tools, session=session,
                              model=model, mcp_config=mcp_config)
    try:
        for ev in stream:
            if ev.get("type") == "done":
                done = ev
            yield ev
    finally:
        close_event_stream(stream)

    if not commit:
        return

    msg = commit_message or ((done or {}).get("reply") or "agent turn")
    # Per-mount: (1) rebuild policy/ from THIS mount's pre-turn baseline — the security guard, applied to
    # every workspace mount so no agent-authored policy/ change survives anywhere (a no-op on a mount with
    # no policy/); (2) commit the mount's remaining (legitimate) changes, authored by the principal. Each
    # mount is a SEPARATE repo → its own attributed commit; one mount failing must not abort the rest.
    # ``mounts`` is already the ordered, de-duped set captured before the turn.
    for mount in mounts:
        base_sha = policy_baselines.get(str(mount.resolve()))
        try:
            reverted = _revert_policy_writes(mount, base_sha)  # policy/ is PLATFORM-WRITE-ONLY (Q3 guard)
        except subprocess.CalledProcessError:
            reverted = []
        if reverted:
            yield {"type": "policy-reverted", "paths": reverted}
        try:
            # The guard's own removals are the ONLY `policy/` deletions this commit may record.
            sha = _commit_mount(mount, message=msg, author=author, policy_removed=reverted)
        except subprocess.CalledProcessError:
            continue  # one mount's commit failing must not abort the rest of the set
        if sha:
            yield {"type": "commit", "sha": sha}
