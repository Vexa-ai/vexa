"""Synthetic record builders.

The gate was calibrated on 22 real recordings from the founder's own archive. Those
recordings are **private** — one of them is a family conversation, which is the entire
reason this module exists — so they are not in this repository and never will be. These
builders reproduce the *shapes* measured from that corpus; the real corpus is replayed
by `test_corpus.py` when an operator points `VEXA_PRESEND_CORPUS` at it.
"""

from __future__ import annotations

from presend_gate import MeetingRecord, Segment

DIALOGUE = [
    "So what did you decide on the pricing question?",
    "We looked at it again and I think your read was right.",
    "Can you send me the numbers after this?",
    "Yes, I will put them in a doc and share it with you today.",
    "How long do you need for the integration work?",
    "Two weeks if nothing else lands on us, three if it does.",
]

MONOLOGUE = [
    "the court then considered the second appeal and rejected it on procedural grounds",
    "and the commission published its finding later that same afternoon",
    "which the parties had already anticipated in their filings",
]

DOMESTIC = [
    "во сколько нам выезжать, ты собрался уже?",
    "ребёнок ещё не позавтракал, разбуди его пожалуйста",
    "я работаю сейчас, спроси у мамы про школу",
    "ложись, я приду через десять минут",
]


def conversation(
    speakers: list[str],
    n: int,
    *,
    lines: list[str] | None = None,
    seconds: float = 5.0,
    language: str = "en",
    languages: list[str] | None = None,
    start_at: float = 0.0,
) -> tuple[Segment, ...]:
    """Round-robin `n` segments across `speakers` — the shape of a real conversation."""
    lines = lines or DIALOGUE
    out = []
    t = start_at
    for i in range(n):
        lang = languages[i % len(languages)] if languages else language
        out.append(
            Segment(
                speaker=speakers[i % len(speakers)],
                text=lines[i % len(lines)],
                start=t,
                end=t + seconds,
                language=lang,
            )
        )
        t += seconds
    return tuple(out)


def blocks(
    speakers: list[str],
    per_block: int,
    *,
    lines: list[str] | None = None,
    seconds: float = 5.0,
    language: str = "en",
) -> tuple[Segment, ...]:
    """One solid block per speaker, no interleaving — two audio sources, not a dialogue."""
    lines = lines or MONOLOGUE
    out = []
    t = 0.0
    for speaker in speakers:
        for i in range(per_block):
            out.append(
                Segment(speaker=speaker, text=lines[i % len(lines)], start=t, end=t + seconds, language=language)
            )
            t += seconds
    return tuple(out)


def record(segments: tuple[Segment, ...], **kwargs) -> MeetingRecord:
    speech = sum(s.seconds for s in segments)
    kwargs.setdefault("meeting_id", "test")
    kwargs.setdefault("platform", "google_meet")
    kwargs.setdefault("wall_seconds", speech / 0.9 if speech else None)
    return MeetingRecord(segments=segments, **kwargs)
