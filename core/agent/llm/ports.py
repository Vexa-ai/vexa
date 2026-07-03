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

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Protocol

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
    zero product imports so it stays liftable."""
    proc = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


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
    emit ``{"type":"commit","sha":...}``. (Hard enforcement is available upstream via
    ``shared.governance`` if it needs to come back.)

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

    if _git(work, "status", "--porcelain"):
        _git(work, "add", "-A")
        msg = commit_message or ((done or {}).get("reply") or "agent turn")
        _git(work, "commit", "-m", msg.splitlines()[0][:72] if msg else "agent turn")
        yield {"type": "commit", "sha": _git(work, "rev-parse", "HEAD")}
