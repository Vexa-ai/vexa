"""worker.py — the in-container agent harness (the ``vexa-agent`` image entrypoint).

This module is now a THIN RE-EXPORT SHIM. The harness was split into two modules:

- ``worker.engine``  — the GENERIC turn engine (auth guards, the governed claude turn, ``serve``, ``main``).
- ``worker.meeting`` — the live MEETING copilot (card/note parsing, ``serve_meeting``, the doc turn).

Everything is re-exported here so every existing ``from worker.worker import X`` keeps resolving, and
``worker/__main__.py`` (``from worker.worker import main; main()``) still works. See those modules for the
real implementations and their doctrine.
"""
from __future__ import annotations

# Generic engine (incl. main, serve, run_turn_over_workspace, auth guards, _Stream, TurnFn, …).
from worker.engine import *  # noqa: F401,F403
from worker.engine import (  # explicit re-exports for names `*` skips (underscore-prefixed) + clarity
    DEFAULT_CHAT_SESSION,
    TurnFn,
    _AUTH_SIGNATURE_RE,
    _Stream,
    _auth_error_event,
    _chat_resume_max_bytes,
    _ensure_repo,
    _exec_claude,
    _link_chat_into_workspace,
    _link_skills_into_workspace,
    _resume_id,
    _session_file,
    _session_transcript_bytes,
    log,
    looks_like_auth_failure,
    main,
    preflight_provider_guard,
    provider_host,
    run_turn_over_workspace,
    serve,
    start_prompt,
)

# Meeting copilot (incl. serve_meeting, build_card_prompt, parse_*, meeting_*_turn, …).
from worker.meeting import *  # noqa: F401,F403
from worker.meeting import (  # explicit re-exports for names `*` skips (underscore-prefixed) + clarity
    MEETING_DOC_PROMPT,
    _CARD_FRAME,
    _CARD_GROUP,
    _PROC_LINE_RE,
    _STEERING_SECTION,
    _accumulate_card,
    _emit_beat,
    _emit_turn,
    _extract_json_value,
    _first_person_note_text,
    _model_error_event,
    _proc_note,
    _set_cursor,
    build_card_prompt,
    fallback_processed_notes,
    meeting_card_turn,
    meeting_doc_turn,
    parse_cards,
    parse_notes,
    render_meeting_transcript,
    serve_meeting,
    upsert_meeting_transcript_file,
)

# `from worker.meeting import *` shadowed run_turn_over_workspace with meeting's call-time indirection
# wrapper; restore the engine implementation as the canonical shim binding (the real turn runner, and the
# name tests patch). meeting's wrapper resolves THROUGH this binding at call time, so patches still apply.
from worker.engine import run_turn_over_workspace  # noqa: F401,F811

# shared.agent_config names the original worker.py imported at module level — keep them importable from
# worker.worker for any caller that relied on `from worker.worker import MeetingConfig` etc.
from shared.agent_config import (  # noqa: F401
    DEFAULT_CARD_KINDS,
    DEFAULT_POLISH_RULES,
    DEFAULT_TAG_RULES,
    MeetingConfig,
    load_meeting_config,
)


if __name__ == "__main__":  # pragma: no cover
    main()
