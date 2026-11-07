"""worker.py — the in-container agent harness (the ``vexa-agent`` image entrypoint).

This module is now a THIN RE-EXPORT SHIM over ``worker.engine``, the GENERIC turn engine (the governed
turn over the llm harness port, ``serve``, ``main``). Everything is re-exported here so every existing
``from worker.worker import X`` keeps resolving, and ``worker/__main__.py``
(``from worker.worker import main; main()``) still works.

It used to re-export a second module, ``worker.meeting`` — the live meeting COPILOT: card/note parsing,
``serve_meeting``, the doc turn, and a ``completion_factory`` seam onto ``llm.CompletionPort``. PRD
decision 34 deleted it. The product runs no model calls of its own beside the agent; a meeting reaches
the agent over the MCP, on a human's turn, and the live view shows the raw transcript.

The PATCHABLE SEAM tests (and operators embedding the worker) use lives here:

- ``harness_factory`` → the ``llm.HarnessPort`` adapter for workspace turns (default: env-selected via
  ``VEXA_RUNNER``). ``run_turn_over_workspace`` resolves it through this module at call time.
"""
from __future__ import annotations

from llm import harness_from_env

# The patchable adapter seam (see module docstring). Tests patch this with a fake.
harness_factory = harness_from_env

# Generic engine (incl. main, serve, run_turn_over_workspace, auth guards, _Stream, TurnFn, …).
from worker.engine import *  # noqa: F401,F403,E402
from worker.engine import (  # noqa: E402 — explicit re-exports for names `*` skips (underscore-prefixed) + clarity
    DEFAULT_CHAT_SESSION,
    TurnFn,
    _AUTH_SIGNATURE_RE,
    _Stream,
    _auth_error_event,
    _chat_resume_max_bytes,
    _ensure_repo,
    _resume_id,
    _session_file,
    log,
    looks_like_auth_failure,
    main,
    preflight_provider_guard,
    provider_host,
    run_turn_over_workspace,
    serve,
    start_prompt,
)


if __name__ == "__main__":  # pragma: no cover
    main()
