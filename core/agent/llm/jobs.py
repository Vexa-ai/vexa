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

This module imports NOTHING of the project (the `llm` package must stay liftable into a standalone
brick): the worker injects its spawner at boot and the seam is a single callable.

IT ALSO CARRIES THE OTHER DIRECTION (Vexa-ai/vexa#1613) — whether the code running RIGHT NOW is a
job. A job runs on its own thread (`worker/jobs.JobRunner._run`), so the mark is a THREAD-LOCAL and
needs no signature change through `HarnessPort.run_turn`, which three adapters implement and only
one of them cares. The harness reads it to pick a budget: a job is not a turn, and the founder's
OeNB job died on the per-turn tool-call budget after 72 steps because it was billed as one.

…AND #1622 GENERALISES THAT MARK FROM A BOOLEAN TO A KIND. "Is this a job?" turned out to be the
first instance of a question with four answers — a chat turn, a job (Create/Extend), a post-meeting
room run and a flow step are four different shapes of work behind one number, and the number was
sized for the shortest of them. So the thread carries WHICH KIND it is running and the harness reads
that; `in_job()` stays exactly as it was, because a job's own machinery asks a yes/no question and
should not have to know about a vocabulary it does not use.
"""
from __future__ import annotations

import threading
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


# ── am I a job? ──────────────────────────────────────────────────────────────────────────────────
#
# THREAD-LOCAL, not an env var and not a parameter. A job shares the worker process with the chat
# it was asked in — the two run at the same time, on different threads — so a process-wide flag
# would give the chat turn the job's budget and vice versa, at random. The worker's job turn sets
# it on the thread that will iterate the harness (`worker/engine.serve`), and nothing else writes it.

_JOB_THREAD = threading.local()


def mark_job_thread(on: bool = True) -> None:
    """Declare (or clear) that THIS THREAD is running a background job's turn."""
    _JOB_THREAD.on = bool(on)


def in_job() -> bool:
    """Is the current thread running a background job's turn? Default false everywhere else."""
    return bool(getattr(_JOB_THREAD, "on", False))


# ── which KIND of turn am I? (Vexa-ai/vexa#1622) ────────────────────────────────────────────────
#
# The same thread-local discipline, one question wider. `worker.engine.run_turn_over_workspace` is
# the single funnel every governed turn passes through, so it is the one place that can name the
# kind — it knows the job mark, the flow mark and the room stamp — and it sets this on the thread
# that will iterate the harness, exactly as the job mark is set.

#: The four kinds a budget is sized for. Closed, and NAMED HERE rather than in the harness, because
#: the producer and the reader of this value must not disagree about the spelling of a dial.
TURN_KINDS = ("chat", "job", "room", "flow")

_TURN_KIND = threading.local()


def mark_turn_kind(kind: str = "") -> None:
    """Declare (or clear, with ``""``) which kind of turn THIS THREAD is running."""
    _TURN_KIND.kind = str(kind or "").strip().lower()


def turn_kind() -> str:
    """The kind of turn on this thread — one of ``TURN_KINDS``.

    A thread nobody marked answers ``job`` when the job flag is set and ``chat`` otherwise. Both
    fallbacks are deliberate: a caller that never learned about kinds (an offline eval, a test
    driving the harness directly) gets exactly the budget it got before this existed."""
    kind = str(getattr(_TURN_KIND, "kind", "") or "")
    if kind in TURN_KINDS:
        return kind
    return "job" if in_job() else "chat"


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
