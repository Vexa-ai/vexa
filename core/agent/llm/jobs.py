"""jobs.py — the harness's request seam for a background job (Vexa-ai/vexa#1584).

A JOB runs a long act outside the turn loop; the runner is the WORKER's (`worker/jobs.py`), because
it belongs above the harness and must be one implementation for every runner. What belongs HERE is
only the other half: the thing a harness's `spawn_job` tool calls, and the answer the model reads.

TWO LINES OF POLICY, AND BOTH ARE THE HARNESS'S OWN RULES:

* **A tool nothing can serve is not attached.** ``configured()`` is what `openai_agent`'s
  `_CONDITIONAL` table asks before it offers `spawn_job` — the same discipline `WebSearch` follows.
  Advertising a tool with no backend teaches the model that backgrounding does not work, and that
  lesson outlives the turn.
* **A refusal is a normal tool result.** A duplicate job, a spawner that raised — the model is told,
  in words, and picks another move. That is what a loop is for.

This module imports NOTHING (the `llm` package must stay liftable into a standalone brick): the
worker injects its spawner at boot and the seam is a single callable.
"""
from __future__ import annotations

from typing import Callable, Optional

#: ``(kind, target, brief) -> (ok, text)``. Installed by the worker; absent everywhere else.
Spawner = Callable[[str, str, str], "tuple[bool, str]"]

_spawner: Optional[Spawner] = None


def set_spawner(fn: Optional[Spawner]) -> None:
    """Install (or clear) the process's job spawner. Called once at worker boot."""
    global _spawner
    _spawner = fn


def configured() -> bool:
    """Can this process actually run a job? Read by the harness before attaching the tool."""
    return _spawner is not None


def spawn(kind: str, target: str, brief: str) -> "tuple[bool, str]":
    """Ask for a background job → ``(ok, text)``, tool-result shaped. Never raises."""
    fn = _spawner
    if fn is None:
        return False, ("background jobs are not available here — do the work in this turn, or say "
                       "plainly that it is too long to do now")
    if not (brief or "").strip():
        return False, "spawn_job needs a `brief`: the job does not see this conversation"
    try:
        return fn(kind or "job", (target or "").strip(), brief)
    except Exception as exc:  # noqa: BLE001 — a failed spawn is a failed tool, never a dead turn
        return False, f"could not start the job: {type(exc).__name__}: {exc}"
