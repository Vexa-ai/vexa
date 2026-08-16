"""Measure a record. No judgement here — `policy.py` does the judging.

Every signal is a plain number or bool so it can be logged, tabulated and argued with.
The ones that matter are the *interleaving* signals: whether two parties took turns.
Speaker COUNT is not one of them — attribution collapses, and a real meeting whose
attribution collapsed looks exactly like a monologue by that measure.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Sequence

from . import lexicon
from .record import MeetingRecord

#: The record is cut into this many equal segment-count windows; a window "has dialogue"
#: when ≥2 distinct speakers appear inside it. Segment-count windows rather than clock
#: windows because some records carry epoch-shaped timestamps (a known data defect) and
#: the gate must not inherit that bug.
DIALOGUE_WINDOWS = 20

#: A speaker holding less than this share of speech is a bystander ("thanks", "bye"),
#: not a second party to the conversation.
SUBSTANTIVE_SHARE = 0.03

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)


def normalize_name(name: str) -> str:
    """Lowercase, drop punctuation, sort tokens — so "Hanke, Marvin" == "Marvin Hanke"."""
    cleaned = _PUNCT.sub(" ", (name or "").lower())
    return " ".join(sorted(t for t in cleaned.split() if t))


def same_person(a: str, b: str, *, threshold: float = 0.85) -> bool:
    """Fuzzy identity match.

    Display names are typed by humans and mangled by platforms: the calibration corpus
    holds "Dmitry Grankin", "Dmitriy Grankin" and "Dmtiry Grankin" for one person. An
    exact match would read that transposition as a second participant — which is
    precisely the failure this gate exists to prevent.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def is_bot_label(name: str, bot_names: Sequence[str]) -> bool:
    """Does this speaker label denote the bot itself?

    Containment, not equality: platforms decorate the bot's display name with suffixes
    it does not choose — Teams rendered ours as ``"Vexa test (Unverified)"``. Erring
    toward "this is the bot" only ever withholds an artifact, never broadcasts one, so
    the loose match is on the safe side of the asymmetry.
    """
    label_tokens = set(normalize_name(name).split())
    if not label_tokens:
        return False
    for bot in bot_names:
        bot_tokens = set(normalize_name(bot).split())
        if bot_tokens and bot_tokens <= label_tokens:
            return True
        if same_person(name, bot):
            return True
    return False


_is_bot = is_bot_label


@dataclass(frozen=True)
class Signals:
    # volume
    segment_count: int = 0
    word_count: int = 0
    speech_seconds: float = 0.0
    wall_seconds: float | None = None
    speech_density: float | None = None

    # who
    speaker_count: int = 0
    substantive_speaker_count: int = 0
    bot_speaker_present: bool = False
    unattributed_share: float = 0.0

    # conversation shape — the spine
    dialogue_window_share: float = 0.0
    alternation_rate: float = 0.0
    monologue_ratio: float = 1.0
    top_speaker_share: float = 1.0

    # roster
    roster_size: int = 0
    roster_source: str = "none"
    counterparty_count: int = 0
    counterparty_known: bool = False

    # language
    language_count: int = 0
    dominant_language_share: float = 1.0
    minor_language_count: int = 0
    language_switch_rate: float = 0.0

    # lexicon (contributory only — en/ru/de coverage)
    second_person_rate: float = 0.0
    question_mark_rate: float = 0.0
    question_word_rate: float = 0.0
    domestic_rate: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def measure(record: MeetingRecord) -> Signals:
    segments = record.segments
    n = len(segments)
    if n == 0:
        return Signals(roster_size=len(record.roster), roster_source=record.roster_source)

    bots = record.bot_names
    labels = [s.speaker for s in segments]
    bot_present = any(_is_bot(sp, bots) for sp in labels if sp)
    # A party is a human voice. The bot's own label is audio the *machine* emitted or
    # picked up off the room/tab — never a participant, so it is excluded from every
    # turn-taking measure rather than counted as a second voice.
    party = [None if (not sp or _is_bot(sp, bots)) else sp for sp in labels]
    distinct = sorted({sp for sp in party if sp})

    # ── turn structure ────────────────────────────────────────────────────────────
    # Unattributed segments are skipped rather than treated as a speaker, so a run of
    # blanks between two blocks neither fakes a turn nor hides one.
    turn_seq = [sp for sp in party if sp]
    transitions = sum(1 for i in range(1, len(turn_seq)) if turn_seq[i] != turn_seq[i - 1])
    alternation = transitions / max(1, len(turn_seq) - 1)

    windows: list[list[str]] = []
    size = max(1, -(-n // DIALOGUE_WINDOWS))  # ceil
    for i in range(0, n, size):
        windows.append([sp for sp in party[i : i + size] if sp])
    dialogue_windows = sum(1 for w in windows if len(set(w)) >= 2)
    dialogue_window_share = dialogue_windows / max(1, len(windows))

    # ── duration weighting (falls back to per-segment weight when timing is absent) ──
    spans = [s.seconds for s in segments]
    speech = sum(spans)
    weights = spans if speech > 0 else [1.0] * n
    total_weight = sum(weights)

    by_speaker: Counter[str] = Counter()
    party_weight = 0.0
    for sp, w in zip(party, weights):
        if sp:
            by_speaker[sp] += w
            party_weight += w
    top_share = (max(by_speaker.values()) / party_weight) if by_speaker and party_weight else 1.0

    # Longest single-voice stretch over the WHOLE record: a long unbroken block of bot
    # audio or of unattributed audio is monologue evidence in its own right.
    run, best_run, prev = 0.0, 0.0, object()
    for sp, w in zip(labels, weights):
        key = sp or "<unattributed>"
        run = run + w if key == prev else w
        prev = key
        best_run = max(best_run, run)
    monologue_ratio = best_run / total_weight if total_weight else 1.0

    substantive = sum(
        1
        for sp in distinct
        if party_weight and by_speaker[sp] / party_weight >= SUBSTANTIVE_SHARE
    )
    unattributed = sum(1 for sp in party if not sp) / n

    # ── roster ────────────────────────────────────────────────────────────────────
    roster = [r for r in record.roster if r and not _is_bot(r, bots)]
    creator = record.creator
    counterparties = [r for r in roster if not (creator and same_person(r, creator))]
    # Speakers count as counterparty evidence too: someone who is heard was there,
    # whether or not the participant panel captured them.
    for sp in distinct:
        if creator and same_person(sp, creator):
            continue
        if not any(same_person(sp, c) for c in counterparties):
            counterparties.append(sp)
    # "Known" means a source exists that WOULD have named a counterparty had there been
    # one. Only a roster qualifies. Speakers add counterparties when attribution works,
    # but their absence proves nothing — attribution collapsing to a single label is a
    # routine capture defect, not evidence that the meeting had one person in it.
    counterparty_known = record.roster_source in ("invite", "observed")

    # ── language ──────────────────────────────────────────────────────────────────
    langs = Counter((s.language or "?") for s in segments)
    dominant = max(langs.values()) / n
    minor = sum(1 for c in langs.values() if c / n < 0.02)
    seq = [(s.language or "?") for s in segments]
    switches = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1]) / max(1, n - 1)

    texts = [s.text for s in segments]
    words = sum(len(t.split()) for t in texts)

    return Signals(
        segment_count=n,
        word_count=words,
        speech_seconds=round(speech, 1),
        wall_seconds=record.wall_seconds,
        speech_density=round(speech / record.wall_seconds, 3)
        if record.wall_seconds and record.wall_seconds > 0
        else None,
        speaker_count=len(distinct),
        substantive_speaker_count=substantive,
        bot_speaker_present=bot_present,
        unattributed_share=round(unattributed, 3),
        dialogue_window_share=round(dialogue_window_share, 3),
        alternation_rate=round(alternation, 4),
        monologue_ratio=round(monologue_ratio, 3),
        top_speaker_share=round(top_share, 3),
        roster_size=len(roster),
        roster_source=record.roster_source,
        counterparty_count=len(counterparties),
        counterparty_known=counterparty_known,
        language_count=len(langs),
        dominant_language_share=round(dominant, 3),
        minor_language_count=minor,
        language_switch_rate=round(switches, 3),
        second_person_rate=round(lexicon.rate(lexicon.SECOND_PERSON, texts), 3),
        question_mark_rate=round(lexicon.question_mark_rate(texts), 3),
        question_word_rate=round(lexicon.rate(lexicon.QUESTION_WORD, texts), 3),
        domestic_rate=round(lexicon.rate(lexicon.DOMESTIC, texts), 3),
    )
