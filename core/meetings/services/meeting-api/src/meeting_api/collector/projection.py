"""List-view shaping for the meetings list (#584).

The meetings-list endpoints (``GET /bots``, ``GET /meetings``) return a row PER meeting. Each row used
to embed that meeting's full ``data`` JSONB — transcripts, speaker events, logs, recordings. On a real
583-meeting account the list response was 4.6 MB, and serializing it on the meeting-api event loop
under morning load wedged the loop and caused a ~1.5 h hosted read outage (2026-07-15).

We cannot drop ``data`` from the list wholesale — the list genuinely renders a few LIGHT keys from it
(a meeting's ``title``, connected ``docs``, ``scheduled_at``, the recording/transcribe flags). So the
list keeps those light keys and drops only the heavy detail keys — the ones that made the response
multi-MB and that the list never renders. Full ``data`` (every key) still ships on the detail path
(``GET /meetings/{id}`` and the transcript endpoint).

This module holds the two things the real store (``adapters.py``) and the in-memory fake (``fakes.py``)
must share so they can never diverge: the set of heavy keys dropped from a list row, and the default
page size that bounds an otherwise-unbounded list.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Heavy per-meeting ``data`` keys the list NEVER renders — dropped from list rows. Everything else
# (title, docs, scheduled_at, workspace_id, constructed_meeting_url, recording/transcribe flags, …)
# rides along, because the list DOES render some of it. Full ``data`` stays on ``GET /meetings/{id}``.
# Measured weight on the outage account (sum across meetings / max in one): speaker_events 155 MB /
# 3.2 MB, bot_logs 78 MB, recordings 13 MB, status_transition 6 MB, last_error 2 MB / 2 MB,
# chat_messages 796 KB. Dropping these removes ~99% of the list's bytes.
LIST_OMIT_KEYS = frozenset({
    "speaker_events",
    "bot_logs",
    "recordings",
    "status_transition",
    "chat_messages",
    "error_details",
    "last_error",
})

# Default page size applied on the list-view path when a caller passes no ``limit`` — turns an
# unbounded full-table response (the outage's proximate trigger) into a bounded page. An explicit
# ``limit`` still wins. Internal callers that reuse ``list_meetings`` to enumerate ALL of a user's
# meetings (get-by-id filter, /bots/status, calendar sync) do NOT take the list-view path and are
# never capped.
DEFAULT_LIST_LIMIT = 50


def project_list_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``data`` with the heavy :data:`LIST_OMIT_KEYS` dropped; every light key kept.

    Pure and non-mutating (builds a new dict), so the caller's stored ``data`` is untouched and the
    detail view still sees every key. A non-dict ``data`` projects to ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k not in LIST_OMIT_KEYS}
