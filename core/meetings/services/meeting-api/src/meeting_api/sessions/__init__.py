"""sessions — the ``MeetingSession`` model + the shared meeting-api SQLAlchemy mirror.

Front door (P6): import from here, never a deep module path.

A ``MeetingSession`` is one bot CONNECTION to a meeting — N per meeting, keyed by
``session_uid`` (the ``connectionId`` the bot is constructed with). ``bot_spawn`` eager-creates a
row on spawn; ``recordings`` looks it up by ``session_uid`` when the bot uploads a chunk, so the
upload resolves its meeting even before the bot reports ``active``.

This sub-package also owns the meeting-api's single SQLAlchemy mirror (``Meeting`` /
``Transcription`` / ``MeetingSession``) — the SSOT every other module (``collector``,
``recordings``, ``bot_spawn``) binds, so there is ONE ``declarative_base()`` in the monolith.

* ``Base`` / ``Meeting`` / ``Transcription`` / ``MeetingSession`` — the SQLAlchemy models.
* ``new_session(meeting_id, session_uid)`` — build an un-persisted ``MeetingSession`` for the
  eager-create on spawn (the caller adds + commits it in its own session).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def new_session(meeting_id: int, session_uid: str) -> Any:
    """Build (do not persist) a ``MeetingSession`` for ``(meeting_id, session_uid)``.

    SQLAlchemy is imported lazily so importing ``sessions`` (and the in-memory paths) never pulls
    in the ORM — only this helper's caller (``bot_spawn``, at production runtime) does.
    """
    from .models import MeetingSession

    return MeetingSession(
        meeting_id=meeting_id,
        session_uid=session_uid,
        session_start_time=datetime.now(timezone.utc),
    )


def __getattr__(name: str):  # PEP 562 — lazy model re-export (keeps SQLAlchemy off the import path)
    if name in ("Base", "Meeting", "Transcription", "MeetingSession"):
        from . import models

        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Base", "Meeting", "Transcription", "MeetingSession", "new_session"]
