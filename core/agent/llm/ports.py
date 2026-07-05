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
    agent). After the turn we do not validate or revert writes — for EVERY mount in the active set
    (``work`` first, then each of ``extra_mounts``) whose tree changed, commit INDEPENDENTLY and emit
    ``{"type":"commit","sha":...}`` (WP-A1.2: one commit per changed mount). Attribution (D4): the
    ``author`` (the dispatch principal) authors each commit; the committer is always the platform.

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

    msg = commit_message or ((done or {}).get("reply") or "agent turn")
    # The primary mount first (the cwd the turn ran in), then every additional active mount. Each is a
    # SEPARATE repo → its own attributed commit. De-dup so an extra_mount that equals `work` isn't twice.
    seen: set[str] = set()
    for mount in [work, *(Path(m) for m in (extra_mounts or ()))]:
        key = str(Path(mount).resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            sha = _commit_mount(Path(mount), message=msg, author=author)
        except subprocess.CalledProcessError:
            continue  # one mount's commit failing must not abort the rest of the set
        if sha:
            yield {"type": "commit", "sha": sha}
