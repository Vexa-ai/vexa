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
    Used for the local ``_git`` AND for launching harness CLIs (they shell out to git in the
    workspace and would inherit the same poisoned discovery)."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_REPO_DISCOVERY_VARS}


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

    def prepare(self, work: Path) -> None:
        """Harness-specific workspace hooks before a turn (continuity/skills wiring). May no-op."""
        ...

    def transcript_bytes(self, work: Path, session_id: str) -> int:
        """Size of the stored transcript behind ``session_id`` (resume-cost accounting); 0 if unknown."""
        ...

    def preflight(self) -> Optional[str]:
        """Boot-time credential sanity check — a loud warning string, or None. May no-op."""
        ...


def _git(work: Path, *args: str) -> str:
    """Local git runner (trimmed stdout). Deliberately NOT shared.adapters._git — this module owns
    zero product imports so it stays liftable. Scrubbed env: the turn commit must land on ``work``,
    never on a repo a hook exported via GIT_DIR."""
    proc = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True, check=True,
                          env=scrubbed_git_env())
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
) -> Iterator[dict]:
    """Run one harness turn over ``work``, streaming normalized UnitEvents, then commit.

    The workspace is a FREE ZONE: governance is PROMPT-ONLY (workspace conventions guide the
    agent). After the turn we do not validate or revert writes — if the tree changed, commit and
    emit ``{"type":"commit","sha":...}`` — with ONE exception: ``policy/`` is PLATFORM-WRITE-ONLY
    (membership/invites live there; see ``control_plane.workspace_membership``). Any agent-turn
    change under ``policy/`` is reverted before the commit (emitting ``{"type":"policy-reverted",
    "paths":[…]}``). (Hard enforcement is available upstream via ``shared.governance`` if it needs
    to come back.)

    ``commit=False`` is the propose-only path (e.g. a read-only turn): NO git is touched — never
    contend on a workspace another agent may be committing to (the index.lock collision).
    """
    work = Path(work)
    # Capture HEAD BEFORE the turn — while it still reflects the PLATFORM's last policy commit and no
    # agent tool (Bash included) has had a chance to move it. This is the baseline the policy guard
    # restores policy/ to, so an agent self-commit of a policy tamper cannot survive.
    base_sha = _policy_head_sha(work) if commit else None
    done: Optional[dict] = None
    for ev in harness.run_turn(work, prompt, allowed_tools=allowed_tools, session=session,
                               model=model, mcp_config=mcp_config):
        if ev.get("type") == "done":
            done = ev
        yield ev

    if not commit:
        return

    reverted = _revert_policy_writes(work, base_sha)  # policy/ is PLATFORM-WRITE-ONLY (Q3 guard)
    if reverted:
        yield {"type": "policy-reverted", "paths": reverted}

    if _git(work, "status", "--porcelain"):
        _git(work, "add", "-A")
        msg = commit_message or ((done or {}).get("reply") or "agent turn")
        _git(work, "commit", "-m", msg.splitlines()[0][:72] if msg else "agent turn")
        yield {"type": "commit", "sha": _git(work, "rev-parse", "HEAD")}
