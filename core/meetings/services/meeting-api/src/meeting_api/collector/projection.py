"""List-view shaping for the meetings list (#584).

The meetings-list endpoints (``GET /bots``, ``GET /meetings``) return a row PER meeting. Each row used
to embed that meeting's full ``data`` JSONB — transcripts, speaker events, logs, recordings, and
calendar event snapshots. Those details can make a single page multi-megabyte even though no list
consumer renders them.

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
# (title, docs, scheduled_at, calendar_connection_id, calendar_uid, workspace_id,
# constructed_meeting_url, recording/transcribe flags, …) rides along, because the list DOES render
# some of it. ``calendar_sources`` is the one mixed-weight key: Calendar needs its source identity
# and auto-join policy, but not the embedded raw ICS event snapshot. It is projected separately
# below. Full ``data`` stays on ``GET /meetings/{id}``.
LIST_OMIT_KEYS = frozenset({
    "speaker_events",
    "bot_logs",
    "recordings",
    "status_transition",
    "chat_messages",
    "error_details",
    "last_error",
})

CALENDAR_SOURCE_LIST_KEYS = frozenset({
    "id",
    "name",
    "auto_join",
    "bot_name",
})

# Default page size applied on the list-view path when a caller passes no ``limit`` — turns an
# unbounded full-table response (the outage's proximate trigger) into a bounded page. An explicit
# ``limit`` still wins. Internal callers that reuse ``list_meetings`` to enumerate ALL of a user's
# meetings (get-by-id filter, /bots/status, calendar sync) do NOT take the list-view path and are
# never capped.
DEFAULT_LIST_LIMIT = 50


def project_calendar_sources(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``data`` with each calendar source reduced to :data:`CALENDAR_SOURCE_LIST_KEYS`.

    The stored source carries ``event`` — the whole raw ICS component: every attendee address,
    the organizer, the description, the conference data. That snapshot is internal reconciliation
    state for the sweep; no API consumer renders it. So it stays in the row and never rides a
    response, on ANY read path and for EVERY viewer — a meeting reaches workspace members and
    transcript-share recipients too, and the owner has no use for it either.

    Pure and non-mutating (builds a new dict), so the caller's stored ``data`` is untouched. A
    non-dict ``data`` projects to ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    sources = data.get("calendar_sources")
    if not isinstance(sources, list):
        return dict(data)
    projected = dict(data)
    projected["calendar_sources"] = [
        {k: v for k, v in source.items() if k in CALENDAR_SOURCE_LIST_KEYS}
        for source in sources
        if isinstance(source, dict) and source.get("id")
    ]
    return projected


def project_list_data(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return ``data`` with the heavy :data:`LIST_OMIT_KEYS` dropped; every light key kept.

    Pure and non-mutating (builds a new dict), so the caller's stored ``data`` is untouched and the
    detail view still sees every key. A non-dict ``data`` projects to ``{}``.
    """
    if not isinstance(data, dict):
        return {}
    sources = project_calendar_sources(data).get("calendar_sources")
    projected = {k: v for k, v in data.items()
                 if k not in LIST_OMIT_KEYS and k != "calendar_sources"}
    if isinstance(sources, list):
        projected["calendar_sources"] = sources
    return projected
