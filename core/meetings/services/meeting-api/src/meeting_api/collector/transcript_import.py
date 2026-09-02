"""Transcript IMPORT — the pure half of "this meeting already happened, here are its words".

The product feature is *bring a transcript Vexa did not record* (a Zoom export, an LFX/TSC
recording, minutes from a call nobody sent a bot to) into a meeting row, so everything
downstream — the canvas, ``GET /transcripts/by-id/{id}``, search, the post-meeting flows — sees
it exactly as it sees a recorded one. The capture double the rehearsal rig runs is the SAME
feature with ``source='seed'``; that is why there is one route and not two.

Everything here is pure: parse, validate, derive. The persistence lives in the store
(``TranscriptStore.complete_transcript_import``) — this module is imported by the route, by both
store implementations, and by the tests, so the identity of an import has ONE definition.

**The identity of an import is ``(source, meeting_id)``.** That derives the ``session_uid``, which
derives every segment id. A bot run's session uid is a uuid4 minted per spawn — right for a run
that could happen twice, wrong here: importing the same source into the same meeting a second time
is the SAME import, not a second one. Deriving rather than minting is what makes the route
idempotent at the row AND at the segment (a re-write upserts on ``(meeting_id, segment_id)``
instead of doubling the transcript, which is what a hand-rolled ``INSERT … ON CONFLICT DO
NOTHING`` with a fresh uuid could never guarantee).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# The declared origins of an import. ``import`` is a person bringing a transcript from elsewhere;
# ``seed`` is the rehearsal rig's capture double. They differ ONLY in the provenance recorded on
# the row — a reader can always tell a double from a real import, which is the whole point of
# naming the source rather than letting the double pretend to be a recording.
SOURCES = frozenset({"import", "seed"})

# A cap, refused rather than truncated: silently storing part of what a caller sent is a worse
# failure than telling them it did not fit (the same rule `annotate_meeting` applies to metadata).
MAX_SEGMENTS = 20000
MAX_TEXT = 8000


def session_uid_for(source: str, meeting_id: int) -> str:
    """The import's session identity — DERIVED, never minted. See the module docstring."""
    return f"import-{source}-{int(meeting_id)}"


def segment_id_for(session_uid: str, index: int) -> str:
    """The segment identity the ``(meeting_id, segment_id)`` upsert keys on. Positional inside the
    import, so re-importing a corrected transcript UPDATES each segment in place."""
    return f"{session_uid}-{index:06d}"


def parse_instant(raw) -> Optional[datetime]:
    """ISO-8601 (``Z`` accepted) or epoch seconds → an aware UTC datetime. ``None`` when neither.

    Both shapes because the two callers already speak both: a calendar/export stamp is ISO, a
    recording tool's is epoch. Refusing one of them would just move the conversion into every
    caller, where it would be written slightly differently each time.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(float(raw), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        if s.replace(".", "", 1).lstrip("-").isdigit():
            return datetime.fromtimestamp(float(s), timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def normalize_segments(raw, session_uid: str) -> "tuple[Optional[list], Optional[str]]":
    """``[{start, end, speaker, text, language?}, …]`` → the store's segment shape, or an error.

    Returns ``(segments, None)`` or ``(None, reason)``. The reason is returned rather than raised
    so the route decides the status code and the tests can assert the sentence a caller reads.

    Deliberately STRICTER than the live-stream coercion in ``ingest._coerce_segment``: that one
    drops a malformed segment and keeps the stream flowing, because a live meeting must not stop
    for one bad frame. An import is a single, whole artifact a person handed us — dropping part of
    it silently would hand them back a transcript with holes they cannot see. So one bad segment
    refuses the whole import and names its index.
    """
    if not isinstance(raw, list) or not raw:
        return None, "'segments' must be a non-empty array"
    if len(raw) > MAX_SEGMENTS:
        return None, f"'segments' exceeds the {MAX_SEGMENTS}-segment cap ({len(raw)} sent)"
    out = []
    for i, seg in enumerate(raw):
        if not isinstance(seg, dict):
            return None, f"segment {i}: must be an object"
        try:
            start = float(seg.get("start"))
            end = float(seg.get("end", seg.get("start")))
        except (TypeError, ValueError):
            return None, f"segment {i}: 'start' and 'end' must be numbers (seconds from the start)"
        if start < 0 or end < 0:
            return None, f"segment {i}: 'start' and 'end' are seconds from the start, never negative"
        if end < start:
            start, end = end, start
        text = seg.get("text")
        if text is not None and not isinstance(text, str):
            return None, f"segment {i}: 'text' must be a string"
        text = (text or "").strip()
        if len(text) > MAX_TEXT:
            return None, f"segment {i}: 'text' exceeds {MAX_TEXT} characters"
        speaker = seg.get("speaker")
        if speaker is not None and not isinstance(speaker, str):
            return None, f"segment {i}: 'speaker' must be a string"
        language = seg.get("language")
        if language is not None and not isinstance(language, str):
            return None, f"segment {i}: 'language' must be a string"
        out.append({
            "segment_id": segment_id_for(session_uid, i),
            "session_uid": session_uid,
            "start": start,
            "end": end,
            "text": text,
            "speaker": (speaker or None),
            "language": (language or None),
            "completed": True,
        })
    return out, None


def occurrence_window(
    segments: list, started_at, ended_at,
) -> "tuple[Optional[datetime], Optional[datetime], Optional[str]]":
    """The meeting's real occurrence window: ``(start, end, None)`` or ``(None, None, reason)``.

    ``started_at`` is REQUIRED and is the single fact an import carries that a bot run cannot: a
    run's ``start_time``/``end_time`` are stamped from ``now()`` at the FSM's transitions, so a
    meeting that happened last Tuesday is not expressible through the bot path at all. That is
    exactly why the rehearsal rig used to reach past the service and ``UPDATE meetings`` by hand.

    ``ended_at`` is optional: absent, the end is the start plus the transcript's own length (the
    last segment's ``end`` is seconds from the start of the capture, so it IS the duration). An
    ``ended_at`` BEFORE the start is refused rather than swapped — an inverted window is a caller
    bug, and silently reordering it would seed the meeting at a time nobody asked for.
    """
    start = parse_instant(started_at)
    if start is None:
        return None, None, "'started_at' is required — ISO-8601 or epoch seconds"
    duration = max((float(s["end"]) for s in segments), default=0.0)
    end = parse_instant(ended_at)
    if ended_at not in (None, "") and end is None:
        return None, None, "'ended_at' is neither ISO-8601 nor epoch seconds"
    if end is None:
        end = start + timedelta(seconds=duration)
    if end < start:
        return None, None, "'ended_at' is before 'started_at'"
    return start, end, None


# The statuses that mean a BOT IS IN FLIGHT on this row. An import is refused on all of them: the
# FSM is never fought (`update_planned_meeting` refuses for the same reason), and a transcript
# landing under a running bot would interleave two capture sources on one meeting. Terminal
# (`completed`/`failed`) is NOT here — a finished row may be re-imported (idempotently), and the
# intent statuses (`idle`/`scheduled`) are the normal case: a planned row that has now happened.
IN_FLIGHT_STATUSES = frozenset({
    "requested", "joining", "awaiting_admission", "needs_help", "active", "stopping",
})
