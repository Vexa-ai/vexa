"""ports.py — the two provider-agnostic ports of the llm module (mirrors runtime_kernel/backend.py).

Two call shapes, two ports:

- ``CompletionPort`` — a plain LLM HTTP call, prompt→text. No tools, no subprocess, no workspace.
  The meeting copilot's card beats run here (everything a beat needs is already in the prompt).
- ``HarnessPort`` — a CLI coding agent driven over a mounted workspace: the tool loop, sessions,
  streamed UnitEvents. Post-meeting docs, chat, and routines run here.

Both are ``typing.Protocol`` — duck-typed like the runtime ``Backend`` port, so adapters need no
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
from dataclasses import dataclass
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
# credentials (ANTHROPIC_*/VEXA_LLM_*/CLAUDE_CODE_OAUTH_TOKEN) to talk to the provider, so those are
# deliberately absent here — this is the tight denylist of vars the model has no legitimate reason to hold.
_HARNESS_SUBPROCESS_DENY_VARS = ("REDIS_URL", "VEXA_AGENT_IDENTITY_TOKEN")


def harness_subprocess_env() -> dict[str, str]:
    """The env for launching an UNTRUSTED model-driven harness subprocess: ``scrubbed_git_env()`` further
    stripped of the host data-plane secrets in ``_HARNESS_SUBPROCESS_DENY_VARS``. Use this — never a raw
    ``os.environ`` / ``scrubbed_git_env`` — to spawn a harness CLI: its Bash tool would otherwise inherit
    the worker's own ``REDIS_URL`` and cross the data-plane tenancy boundary the mounts enforce on disk."""
    return {k: v for k, v in scrubbed_git_env().items() if k not in _HARNESS_SUBPROCESS_DENY_VARS}


# A raw process runner: given an argv + a cwd, yield the process's stdout lines. Injected into CLI
# harness adapters so their parsers are offline-provable with a fake (no CLI, no network).
HarnessExec = Callable[[list[str], str], Iterable[str]]


@dataclass(frozen=True)
class CompletionResult:
    """One completion: the text and the model that produced it (for event attribution)."""

    text: str
    model: str = ""


class CompletionPort(Protocol):
    """A plain prompt→text LLM provider. Raises ``LLMAuthError`` on a rejected credential,
    ``LLMConfigError`` on missing endpoint/model config, ``LLMError`` otherwise."""

    name: str

    def complete(self, prompt: str, *, system: Optional[str] = None,
                 model: Optional[str] = None) -> CompletionResult: ...


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



# The platform-write-only subtree of every workspace repo. Agent turns must NEVER modify it
# (membership/invites live here — see control_plane.workspace_membership). Kept as a bare string so
# this module stays product-import-free (it is liftable into a standalone brick). The control plane's
# membership writer commits policy/ directly; a turn that touches it is reverted here before the commit.
_POLICY_DIR = "policy"


def _policy_head_sha(work: Path) -> Optional[str]:
    """The current HEAD sha, or ``None`` if the repo has no commit yet (freshly-init'd workspace).
    Captured BEFORE a turn runs — while HEAD still reflects the PLATFORM's last policy commit and no
    agent tool has had a chance to move it — so the post-turn guard has a trustworthy baseline."""
    try:
        return _git(work, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return None


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
    # Untracked (incl. would-be-ignored is out of scope; policy/ is not ignored) entries under policy/.
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


def _revert_policy_writes(work: Path, base_sha: Optional[str]) -> list[str]:
    """Make ``policy/`` HEAD-AUTHORITATIVE, not working-tree-scanned — the Q3 write-guard (default:
    post-turn validation + revert). policy/ is PLATFORM-WRITE-ONLY; the platform's last policy commit is
    ``base_sha`` (HEAD captured BEFORE the turn, before any agent tool ran). The agent toolset includes
    ``Bash``, so an agent turn can ``git add policy/ && git commit`` its OWN tamper mid-turn — a
    working-tree scan then sees a clean tree and the forgery survives in HEAD. This guard instead
    RESTORES the whole ``policy/`` subtree to its ``base_sha`` state, discarding ANY agent change to
    policy/ whether COMMITTED (self-commit), staged, uncommitted, a symlink, or a brand-new policy/ in a
    freshly-seeded workspace. Returns the affected paths so the caller can flag them.

    Mechanism: (1) delete everything currently under policy/ from index + disk; (2) restore the baseline
    policy/ files from ``base_sha`` (a no-op if the baseline had no policy/). The subsequent turn commit
    therefore records the PLATFORM's policy tree, never the agent's."""
    import shutil

    baseline = _list_policy_paths_at(work, base_sha) if base_sha else set()
    current = _current_policy_entries(work)
    # Anything present now that is not identical-to-baseline is suspect; but rather than diff contents,
    # we unconditionally rebuild policy/ from the baseline (cheap, and content tamper of a baselined file
    # via self-commit would otherwise slip a working-tree scan). affected = union of what we touch.
    affected = set(current) | set(baseline)
    if not affected:
        return []

    # 1) Purge the current policy/ subtree from index + working tree (handles committed, staged,
    #    untracked, symlink, and directory cases uniformly).
    try:
        _git(work, "rm", "-r", "-f", "--cached", "--ignore-unmatch", "--", _POLICY_DIR)
    except subprocess.CalledProcessError:
        pass
    policy_root = work / _POLICY_DIR
    try:
        if policy_root.is_symlink() or (policy_root.exists() and not policy_root.is_dir()):
            policy_root.unlink(missing_ok=True)
        elif policy_root.is_dir():
            shutil.rmtree(policy_root, ignore_errors=True)
    except OSError:
        pass

    # 2) Restore the baseline policy/ from base_sha (checkout writes both index + working tree).
    if baseline and base_sha:
        try:
            _git(work, "checkout", base_sha, "--", _POLICY_DIR)
        except subprocess.CalledProcessError:
            # Path-by-path fallback if the bulk checkout is refused for any single entry.
            for path in sorted(baseline):
                try:
                    _git(work, "checkout", base_sha, "--", path)
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


def _commit_mount(work: Path, *, message: str, author: Optional[tuple[str, str]]) -> Optional[str]:
    """Commit ``work`` if its tree changed, attributed to ``author`` (committer = platform). Returns the
    new HEAD sha, or None on a clean tree. A path with no ``.git`` is skipped (a mount not yet seeded).
    Best-effort per mount: one mount failing to commit must not abort the others."""
    if not (work / ".git").exists():
        return None
    if not _git(work, "status", "--porcelain"):
        return None
    env = _commit_env(author)
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-m", (message.splitlines()[0][:72] if message else "agent turn"), env=env)
    return _git(work, "rev-parse", "HEAD", env=env)



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

    COMPOSED with the policy guard (Lane M / Q3): ``policy/`` is PLATFORM-WRITE-ONLY (membership/invites
    live there; see ``control_plane.workspace_membership``). Each mount is a separate workspace repo that
    may carry its own ``policy/`` subtree, so the guard runs PER MOUNT: we capture that mount's HEAD
    policy tree BEFORE the turn (the platform's last policy commit, before any agent tool — Bash included
    — can move it), and AFTER the turn we rebuild that mount's ``policy/`` from its baseline BEFORE its
    commit (emitting ``{"type":"policy-reverted","paths":[…]}``). Net invariant: no agent-authored change
    to ANY mount's ``policy/`` can ever be committed — on the private baseline OR any shared workspace —
    while every other change commits, authored by the principal. ``_global`` (read-only) is never in the
    commit set. A mount with no ``policy/`` makes the guard a no-op. (Hard enforcement is available
    upstream via ``shared.governance`` if it needs to come back.)

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
    for ev in harness.run_turn(work, prompt, allowed_tools=allowed_tools, session=session,
                               model=model, mcp_config=mcp_config):
        if ev.get("type") == "done":
            done = ev
        yield ev

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
            sha = _commit_mount(mount, message=msg, author=author)
        except subprocess.CalledProcessError:
            continue  # one mount's commit failing must not abort the rest of the set
        if sha:
            yield {"type": "commit", "sha": sha}
