"""The judgement: three outcomes, and who the artifact may reach.

The gate answers exactly one question — **do we have positive evidence that two or more
parties were present and talking to each other?** — and it answers it *before* anything
is emailed. Nothing downstream re-decides.

Asymmetry is the whole design. Holding a real meeting costs one person one click.
Emailing a private conversation to an invite list cannot be undone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from .record import MeetingRecord
from .signals import Signals, measure, same_person


class Outcome(str, Enum):
    #: Deliver the artifact to every participant. Requires positive evidence.
    SEND = "send"
    #: Deliver to the meeting's creator ONLY, with a plain note saying why. The default
    #: under any doubt: it keeps the record useful without broadcasting it.
    HOLD_FOR_CREATOR = "hold_for_creator"
    #: Emit no artifact to anyone. Reserved for affirmative evidence of a non-meeting.
    #: The record itself stays in the creator's own list; only the artifact is withheld.
    SUPPRESS = "suppress"


@dataclass(frozen=True)
class Policy:
    """Every threshold, in one visible place, with the corpus margin behind each.

    Calibrated on 22 real records from the founder's own archive (2026-08-16), of which
    2 were not meetings. Margins quoted below are `worst real meeting` vs `threshold` vs
    `worst non-meeting` on that corpus. n=22 is small; these are heuristics, not
    learned parameters, and every one of them is meant to be tuned from production
    hold-rate rather than defended from this sample.
    """

    #: Below this there is nothing to summarize. (Smallest real meeting in the corpus:
    #: 60 segments / ~600 words.)
    min_segments: int = 12
    min_words: int = 120

    #: Fraction of the record's 20 windows containing ≥2 distinct parties.
    #: worst real 0.45 · threshold 0.25 · worst non-meeting 0.05
    min_dialogue_window_share: float = 0.25
    #: Speaker changes per adjacent attributed segment pair.
    #: worst real 0.076 · threshold 0.030 · worst non-meeting 0.002
    min_alternation_rate: float = 0.03
    #: Longest single-voice stretch as a share of all speech.
    #: worst real 0.193 · threshold 0.350 · worst non-meeting 0.566
    max_monologue_ratio: float = 0.35

    #: `sensitive_context` trip point for the domestic-vocabulary probe.
    #: worst real 0.047 · threshold 0.120 · offending record 0.222
    sensitive_domestic_rate: float = 0.12

    # ── soft flags: each is a real risk on its own, none is decisive alone ──────────
    min_speech_density: float = 0.35
    max_language_switch_rate: float = 0.12
    max_top_speaker_share: float = 0.80
    min_second_person_rate: float = 0.10
    min_question_mark_rate: float = 0.03
    #: How many soft flags force a hold. Two, because any single one fires on at least
    #: one genuine meeting in the corpus.
    soft_flags_to_hold: int = 2


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reasons: tuple[str, ...]
    signals: Signals
    sensitive_context: bool = False
    soft_flags: tuple[str, ...] = ()
    note: str = ""

    @property
    def may_broadcast(self) -> bool:
        return self.outcome is Outcome.SEND


NOTES = {
    Outcome.SEND: "",
    Outcome.HOLD_FOR_CREATOR: (
        "This record did not look enough like a meeting to send to everyone on it, "
        "so only you received it. Open the record to check, and send it on if it is fine."
    ),
    Outcome.SUPPRESS: (
        "No artifact was produced for this record: it does not look like a meeting "
        "between people. The recording is still in your list."
    ),
}


def _conversational(s: Signals, p: Policy) -> bool:
    return (
        s.dialogue_window_share >= p.min_dialogue_window_share
        and s.alternation_rate >= p.min_alternation_rate
        and s.monologue_ratio <= p.max_monologue_ratio
    )


def _soft_flags(s: Signals, p: Policy) -> tuple[str, ...]:
    flags = []
    if s.speech_density is not None and s.speech_density < p.min_speech_density:
        flags.append("sparse_speech")
    if s.language_switch_rate > p.max_language_switch_rate:
        flags.append("language_churn")
    if s.top_speaker_share > p.max_top_speaker_share:
        flags.append("one_voice_dominates")
    if (
        s.second_person_rate < p.min_second_person_rate
        and s.question_mark_rate < p.min_question_mark_rate
    ):
        flags.append("no_dialogue_lexicon")
    return tuple(flags)


def evaluate(record: MeetingRecord, policy: Policy | None = None) -> Verdict:
    """Decide what may happen to this record's artifact. Pure; no I/O."""
    p = policy or Policy()
    s = measure(record)
    conversational = _conversational(s, p)
    flags = _soft_flags(s, p)
    sensitive = s.domestic_rate >= p.sensitive_domestic_rate
    reasons: list[str] = []

    def verdict(outcome: Outcome) -> Verdict:
        return Verdict(
            outcome=outcome,
            reasons=tuple(reasons),
            signals=s,
            sensitive_context=sensitive,
            soft_flags=flags,
            note=NOTES[outcome],
        )

    # ── 1. nothing to send ────────────────────────────────────────────────────────
    if s.segment_count < p.min_segments or s.word_count < p.min_words:
        reasons.append("empty_record")
        return verdict(Outcome.SUPPRESS)

    # ── 2. affirmative evidence of a non-meeting → suppress ───────────────────────
    # The bot's own display name attributed as a speaker means the capture took in
    # system/tab/room audio. Combined with no turn-taking, that is playback, not a call.
    if s.bot_speaker_present and not conversational:
        reasons.append("bot_audio_source")
        return verdict(Outcome.SUPPRESS)

    # A roster that WOULD have named a second party names none, and nobody else is
    # heard: there was no one to send this to but the creator, and nothing to send.
    if not conversational and s.counterparty_known and s.counterparty_count == 0:
        reasons.append("no_counterparty_on_roster")
        if sensitive:
            reasons.append("sensitive_context")
        return verdict(Outcome.SUPPRESS)

    # ── 3. sensitive content never broadcasts, whatever else is true ──────────────
    if sensitive:
        reasons.append("sensitive_context")
        return verdict(Outcome.HOLD_FOR_CREATOR)

    # ── 4. cannot confirm a conversation → hold ───────────────────────────────────
    if not conversational:
        reasons.append("no_dialogue_structure")
        return verdict(Outcome.HOLD_FOR_CREATOR)

    if s.substantive_speaker_count < 2:
        reasons.append("single_substantive_speaker")
        return verdict(Outcome.HOLD_FOR_CREATOR)

    if len(flags) >= p.soft_flags_to_hold:
        reasons.append("soft_risk")
        reasons.extend(f"soft:{f}" for f in flags)
        return verdict(Outcome.HOLD_FOR_CREATOR)

    # ── 5. positive evidence, no risk flag → send ─────────────────────────────────
    reasons.append("dialogue_confirmed")
    if s.counterparty_count:
        reasons.append("counterparty_present")
    return verdict(Outcome.SEND)


def route_recipients(
    verdict: Verdict,
    participants: Sequence[str],
    creator: str | None,
) -> tuple[str, ...]:
    """The policy hook the artifact pipeline calls instead of its own recipient list.

    This is the *only* place a broadcast is authorized. A pipeline that mails
    `participants` directly has bypassed the gate.
    """
    if verdict.outcome is Outcome.SUPPRESS:
        return ()
    if verdict.outcome is Outcome.HOLD_FOR_CREATOR:
        return (creator,) if creator else ()
    return tuple(participants)


def gate(
    record: MeetingRecord,
    participants: Iterable[str] = (),
    policy: Policy | None = None,
) -> tuple[Verdict, tuple[str, ...]]:
    """`evaluate` + `route_recipients` in one call — the convenience entry point."""
    v = evaluate(record, policy)
    people = [p for p in participants]
    if not people:
        people = [r for r in record.roster if not (record.creator and same_person(r, record.creator))]
        if record.creator:
            people.append(record.creator)
    return v, route_recipients(v, people, record.creator)


__all__ = ["Outcome", "Policy", "Verdict", "evaluate", "gate", "route_recipients"]
