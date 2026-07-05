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


def _revert_policy_writes(work: Path) -> list[str]:
    """Revert any agent-turn change under ``policy/`` before the turn commit — policy/ is
    PLATFORM-WRITE-ONLY (the Q3 write-guard, default: post-turn validation + revert). Returns the
    reverted paths so the caller can flag them. Tracked policy/ files are checked out back to HEAD;
    newly-added / untracked ones are unstaged and removed."""
    status = _git(work, "status", "--porcelain", "--", _POLICY_DIR)
    if not status:
        return []
    import shutil
    reverted: list[str] = []
    for raw in status.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        untracked = line.lstrip().startswith("??")
        # Porcelain is "XY<space>PATH"; the shared _git strips the first line's leading space, so parse
        # tolerantly: the path starts at the first _POLICY_DIR occurrence (policy/ never appears in the
        # 2-char status code). Renames "old -> new" keep the destination.
        idx = line.find(_POLICY_DIR)
        if idx < 0:
            continue
        path = line[idx:].strip()
        if " -> " in path:  # rename
            path = path.split(" -> ", 1)[1].strip()
        if not path.startswith(_POLICY_DIR):
            continue
        if untracked:
            target = work / path
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
        else:
            try:
                _git(work, "checkout", "HEAD", "--", path)
            except subprocess.CalledProcessError:
                # not in HEAD (agent ADDED it) — unstage + delete
                try:
                    _git(work, "reset", "--", path)
                except subprocess.CalledProcessError:
                    pass
                (work / path).unlink(missing_ok=True)
        reverted.append(path)
    return reverted


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
    done: Optional[dict] = None
    for ev in harness.run_turn(work, prompt, allowed_tools=allowed_tools, session=session,
                               model=model, mcp_config=mcp_config):
        if ev.get("type") == "done":
            done = ev
        yield ev

    if not commit:
        return

    reverted = _revert_policy_writes(work)  # policy/ is PLATFORM-WRITE-ONLY (Q3 guard)
    if reverted:
        yield {"type": "policy-reverted", "paths": reverted}

    if _git(work, "status", "--porcelain"):
        _git(work, "add", "-A")
        msg = commit_message or ((done or {}).get("reply") or "agent turn")
        _git(work, "commit", "-m", msg.splitlines()[0][:72] if msg else "agent turn")
        yield {"type": "commit", "sha": _git(work, "rev-parse", "HEAD")}
