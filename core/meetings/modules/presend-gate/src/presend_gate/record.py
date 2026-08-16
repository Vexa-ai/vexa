"""The normalized record the gate reasons over, plus the api.v1 transcript adapter.

The gate deliberately does NOT depend on any transport shape. Callers normalize into
`MeetingRecord`; `from_transcript_payload` is the one adapter shipped, for the
`GET /transcripts/{platform}/{id}` payload the meeting-api returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

# A single segment longer than this is a timing artifact, not speech. Clamped so a
# garbage `end` cannot dominate every duration-weighted signal.
MAX_SEGMENT_SECONDS = 120.0


@dataclass(frozen=True)
class Segment:
    """One speaker-attributed utterance. `speaker` may be empty (unattributed)."""

    speaker: str = ""
    text: str = ""
    start: float = 0.0
    end: float = 0.0
    language: str | None = None

    @property
    def seconds(self) -> float:
        """Duration, robust to the absolute-epoch `start`/`end` shape some records carry.

        Only the *difference* is used, so epoch-shaped and relative-shaped records
        both yield a sane span; nonsense spans clamp to `MAX_SEGMENT_SECONDS`.
        """
        span = self.end - self.start
        if span <= 0:
            return 0.0
        return min(span, MAX_SEGMENT_SECONDS)


@dataclass(frozen=True)
class MeetingRecord:
    """Everything the gate is allowed to look at.

    `roster_source` is load-bearing, not decoration:

    - ``"invite"``   — the roster came from the invitation the bot's mailbox received.
                       Authoritative: it says who was *asked* to be there.
    - ``"observed"`` — the roster is who the platform UI showed while the bot sat in
                       the call. Weaker: it misses people who never turned up in the
                       participant panel, and it can be empty for reasons unrelated
                       to whether a meeting happened.
    - ``"none"``     — no roster at all. **Absence is not evidence of absence** — see
                       `policy.py`; three real meetings in the calibration corpus have
                       no roster.
    """

    meeting_id: str = ""
    platform: str | None = None
    segments: tuple[Segment, ...] = ()
    roster: tuple[str, ...] = ()
    roster_source: str = "none"
    #: Who invited the bot / owns the record. Used to tell "someone else was there"
    #: apart from "only the account owner was there".
    creator: str | None = None
    #: The bot's own display name(s) in the meeting UI. A speaker label matching one
    #: of these is proof the capture picked up system/playback audio, not a person.
    bot_names: tuple[str, ...] = ()
    wall_seconds: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _wall_seconds(payload: dict[str, Any]) -> float | None:
    start, end = _parse_ts(payload.get("start_time")), _parse_ts(payload.get("end_time"))
    if start and end and end > start:
        return (end - start).total_seconds()
    return None


def _as_float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def from_transcript_payload(
    payload: dict[str, Any],
    *,
    creator: str | None = None,
    bot_names: Iterable[str] = (),
    roster: Sequence[str] | None = None,
    roster_source: str | None = None,
) -> MeetingRecord:
    """Normalize a meeting-api transcript payload into a `MeetingRecord`.

    `roster` overrides what the payload carries — pass the **invite** roster here when
    the meeting arrived through the mailroom binding (`roster_source="invite"`), which
    is the strongest evidence the gate can be given. With no override the adapter falls
    back to `data.participants`, the roster the bot *observed* in the meeting UI.
    """
    data = payload.get("data") or {}
    segments = tuple(
        Segment(
            speaker=(s.get("speaker") or "").strip(),
            text=(s.get("text") or ""),
            start=_as_float(s.get("start")),
            end=_as_float(s.get("end")),
            language=s.get("language"),
        )
        for s in (payload.get("segments") or [])
        if (s.get("text") or "").strip()
    )

    if roster is not None:
        names, source = tuple(roster), (roster_source or "invite")
    else:
        observed = data.get("participants") or []
        names = tuple(str(p) for p in observed)
        source = roster_source or ("observed" if observed else "none")

    return MeetingRecord(
        meeting_id=str(payload.get("id") or ""),
        platform=payload.get("platform"),
        segments=segments,
        roster=names,
        roster_source=source,
        creator=creator,
        bot_names=tuple(bot_names),
        wall_seconds=_wall_seconds(payload),
        extra={"completion_reason": data.get("completion_reason"), "name": data.get("name")},
    )
