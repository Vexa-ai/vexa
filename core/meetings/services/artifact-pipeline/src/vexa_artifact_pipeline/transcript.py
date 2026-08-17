"""Reading a record: turns, language, and the label a human recognises the meeting by.

Everything here is derived from the payload the meeting API returned, and every derivation
is deliberately conservative, because two shapes in the archive break the naive reading:

* **Absolute-epoch offsets.** Four records in the calibration corpus carry epoch seconds in
  *both* ``start`` and ``end``, so a turn's position in the meeting is meaningless while its
  *duration* is fine. Nothing here uses an absolute offset; ordering comes from the payload's
  own segment order, which is what the transcript stream produced.
* **Display names are not identity.** One person appears three ways in one archive
  (``Dmitry Grankin`` / ``Dmitriy Grankin`` / ``Dmtiry Grankin``). Matching a speaker label
  to a participant is therefore fuzzy, and it is delegated to the pre-send gate's
  ``same_person``, so the pipeline and the gate agree about who is who.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .labels import platform_name, vocabulary_for
from .ports import FetchedRecord


@dataclass(frozen=True)
class Turn:
    """One speaker-attributed utterance, in transcript order."""

    index: int
    speaker: str
    text: str
    language: str | None = None

    @property
    def attributed(self) -> bool:
        return bool(self.speaker.strip())


def turns(record: FetchedRecord) -> tuple[Turn, ...]:
    out: list[Turn] = []
    for i, seg in enumerate(record.segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append(
            Turn(
                index=len(out),
                speaker=(seg.get("speaker") or "").strip(),
                text=text,
                language=seg.get("language"),
            )
        )
    return tuple(out)


def speaker_counts(record: FetchedRecord) -> Counter:
    """Attributed speaker labels → how many turns each holds."""
    counts: Counter = Counter()
    for turn in turns(record):
        if turn.attributed:
            counts[turn.speaker] += 1
    return counts


def dominant_language(record: FetchedRecord) -> str:
    """The language the meeting was actually held in.

    Per-segment language is the evidence; ``data.languages`` is only a set of everything the
    recogniser ever guessed (one corpus record lists ``de, en, pt, ru`` for a call held in
    English), so it is used only when no segment carries a language at all.
    """
    counts: Counter = Counter()
    for turn in turns(record):
        if turn.language:
            counts[str(turn.language).split("-")[0].lower()] += 1
    if counts:
        return counts.most_common(1)[0][0]
    declared = ((record.payload.get("data") or {}).get("languages")) or []
    return str(declared[0]).split("-")[0].lower() if declared else "en"


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def meeting_label(record: FetchedRecord, language: str) -> str:
    """``2026-05-18 · Microsoft Teams · 60m`` — date, platform, duration.

    The parts a reader uses to recognise which meeting this was, and nothing else. Each
    part is omitted when the record does not state it, rather than guessed: a made-up
    duration on an email subject line is a small lie with no upside.
    """
    vocab = vocabulary_for(language)
    start, end = _parse_ts(record.payload.get("start_time")), _parse_ts(record.payload.get("end_time"))
    parts: list[str] = []
    if start:
        parts.append(start.date().isoformat())
    platform = platform_name(record.payload.get("platform"))
    if platform:
        parts.append(platform)
    if start and end and end > start:
        minutes = int(round((end - start).total_seconds() / 60))
        if minutes > 0:
            parts.append(f"{minutes}{vocab.minutes}" if vocab.minutes == "m" else f"{minutes} {vocab.minutes}")
    if not parts:
        name = (record.payload.get("data") or {}).get("name")
        parts.append(str(name) if name else f"record {record.record_id}")
    return " · ".join(parts)


def observed_roster(record: FetchedRecord) -> tuple[str, ...]:
    """The roster the bot saw in the meeting UI. Absence is not evidence of absence."""
    people = (record.payload.get("data") or {}).get("participants") or []
    return tuple(str(p).strip() for p in people if str(p).strip())


def first_name(display_name: str) -> str:
    """The token a colleague would use to address this person, for mention matching.

    Platform display names carry surnames-first (``Hanke, Marvin``), pronouns
    (``Julianne Appleton (she / her)``) and org suffixes; the leading token of the cleaned
    name is the one that appears in speech.
    """
    cleaned = display_name.split("(")[0].strip()
    if "," in cleaned:
        tail = cleaned.split(",", 1)[1].strip()
        if tail:
            return tail.split()[0]
    parts = cleaned.split()
    return parts[0] if parts else ""


def mentions(text: str, names: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(n and n.lower() in lowered for n in names)
