"""The SQLAlchemy models the production ``SqlAlchemyTranscriptStore`` reads.

RE-EXPORT of the meeting-api's single SQLAlchemy mirror (``meeting_api.sessions.models``). The
collector was a standalone service with its OWN co-located mirror; now that it is folded into the
one meeting-api monolith (P2), all modules must bind the SAME ``declarative_base()`` — two bases
defining the same ``meetings`` / ``transcriptions`` tables in one process would collide. So this
module no longer declares the tables; it re-exports them from the shared ``sessions`` module
(itself a self-contained mirror — NOT an ``admin_api`` import, keeping the isolation gates clean).

Imported lazily by ``adapters.py`` at production runtime only (SQLAlchemy is never imported during
the gate venv's test run — the in-memory fakes never touch it), so no ``greenlet`` pin is needed.
"""
from __future__ import annotations

from ..sessions.models import Base, Meeting, Transcription

__all__ = ["Base", "Meeting", "Transcription"]
