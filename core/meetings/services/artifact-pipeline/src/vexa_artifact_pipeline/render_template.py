"""The deterministic renderer — a context delta assembled only from evidence in the record.

Four sections, each one a claim the record can be made to prove:

| Section | What has to be true |
|---|---|
| ``decided`` | a sentence carries a decision cue |
| ``you_committed`` | the recipient is the speaker **and** the sentence carries a commitment cue |
| ``owed_to_you`` | someone else commits, **and** the recipient is named in it or is in the conversational neighbourhood |
| ``asked_of_you`` | someone else asks a question, **and** the recipient is named in it or answers next |

It infers nothing. Every item is a sentence a participant actually said, and a section with
no evidence is not emitted — which is also the honest answer to "should the artifact shrink
when nothing happened": here it does, all the way to a single line, and the run log counts
how often that occurs so the question can be settled from data.

**Two known limitations, both upstream of this file.** Speaker attribution collapses in
interview-shaped meetings — the interviewer's questions are filed under the interviewee's
name — so ``you_committed`` inherits whatever the transcription got wrong; the renderer
trusts labels because it has nothing better, and it is why the gate, not the renderer, is
the safety mechanism. And "in the conversational neighbourhood" is a proxy for "said to
you", which is right in a two-party call and looser in a six-party one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from presend_gate.signals import same_person

from .artifact import Artifact, Recipient, Section, dedupe, section
from .cues import (
    COMMITMENT,
    DECISION,
    asks,
    is_question,
    lexicon,
    matches,
    sentences,
    trim,
    usable,
)
from .labels import vocabulary_for
from .ports import FetchedRecord
from .transcript import first_name, mentions, turns

#: How many speaker blocks either side of a commitment count as "said to you".
NEIGHBOURHOOD_BLOCKS = 2
#: Items per section. Past this a reader stops reading, and the tail is the weakest evidence.
MAX_ITEMS = 6


@dataclass(frozen=True)
class _Candidate:
    """One item, with what is needed to rank it: does it name the recipient, and when."""

    text: str
    named: bool
    rank: tuple[int, int]


@dataclass(frozen=True)
class Block:
    """Consecutive turns by one speaker, merged back into speech."""

    index: int
    speaker: str
    text: str

    @property
    def attributed(self) -> bool:
        return bool(self.speaker.strip())


def blocks(record: FetchedRecord) -> tuple[Block, ...]:
    out: list[Block] = []
    for turn in turns(record):
        if out and out[-1].speaker == turn.speaker:
            merged = out[-1]
            out[-1] = Block(merged.index, merged.speaker, f"{merged.text} {turn.text}".strip())
            continue
        out.append(Block(len(out), turn.speaker, turn.text))
    return tuple(out)


class TemplateRenderer:
    """No model, no network, no configuration beyond how loudly to say "nothing happened"."""

    name = "template"

    def __init__(self, *, emit_when_empty: bool = True, max_items: int = MAX_ITEMS) -> None:
        self._emit_when_empty = emit_when_empty
        self._max_items = max_items

    def render(
        self,
        *,
        record: FetchedRecord,
        recipient: Recipient,
        participants: Sequence[Recipient],
        meeting_id: str,
        meeting_label: str,
        language: str,
    ) -> Artifact:
        vocab = vocabulary_for(language)
        commitment_cues = lexicon(COMMITMENT, language)
        decision_cues = lexicon(DECISION, language)
        speech = blocks(record)
        names = _names_for(recipient)

        decided: list[_Candidate] = []
        committed: list[_Candidate] = []
        owed: list[_Candidate] = []
        asked: list[_Candidate] = []

        for block in speech:
            mine = block.attributed and same_person(block.speaker, recipient.display_name)
            near = _speaks_nearby(speech, block.index, recipient)
            for order, sentence in enumerate(sentences(block.text)):
                if not usable(sentence):
                    continue
                named = mentions(sentence, names)
                rank = (block.index, order)
                if asks(sentence):
                    if not mine and is_question(sentence) and (named or _answers_next(speech, block.index, recipient)):
                        asked.append(_Candidate(_attributed(sentence, block.speaker), named, rank))
                    continue
                if matches(sentence, decision_cues):
                    decided.append(_Candidate(_attributed(sentence, block.speaker), named, rank))
                    continue
                if matches(sentence, commitment_cues):
                    if mine:
                        committed.append(_Candidate(trim(sentence), named, rank))
                    elif named or near:
                        owed.append(_Candidate(_attributed(sentence, block.speaker), named, rank))

        built = [
            section("decided", vocab, self._cap(decided)),
            section("you_committed", vocab, self._cap(committed)),
            section("owed_to_you", vocab, self._cap(owed)),
            section("asked_of_you", vocab, self._cap(asked)),
        ]
        built = [s for s in built if not s.is_empty]
        if not built and self._emit_when_empty:
            built = [Section(kind="nothing_recorded", title=vocab.heading("nothing_recorded"), body=vocab.nothing_recorded)]

        return Artifact(
            recipient=recipient,
            meeting_id=meeting_id,
            meeting_label=meeting_label,
            language=language,
            sections=tuple(built),
            renderer=self.name,
        )

    def _cap(self, candidates: Sequence["_Candidate"]) -> tuple[str, ...]:
        """Cap by relevance, read in transcript order.

        When more evidence exists than fits, the sentences that name the recipient outrank
        the ones that only sat near them — the neighbourhood rule is a proxy and this is
        where it yields to the stronger signal. What survives is then re-sorted back into
        the order it was said, because a list of quotes out of sequence reads as invented.
        """
        ranked = sorted(dict.fromkeys(candidates), key=lambda c: (not c.named, c.rank))
        return dedupe(c.text for c in sorted(ranked[: self._max_items], key=lambda c: c.rank))


def _names_for(recipient: Recipient) -> tuple[str, ...]:
    """The strings that count as "the recipient was named" inside a sentence."""
    out = [recipient.display_name]
    given = first_name(recipient.display_name)
    if len(given) >= 3:
        out.append(given)
    return tuple(dict.fromkeys(n for n in out if n))


def _attributed(sentence: str, speaker: str) -> str:
    body = trim(sentence)
    return f"{body} — {speaker}" if speaker.strip() else body


def _speaks_nearby(speech: Sequence[Block], index: int, recipient: Recipient) -> bool:
    lo, hi = max(0, index - NEIGHBOURHOOD_BLOCKS), index + NEIGHBOURHOOD_BLOCKS + 1
    return any(
        b.attributed and same_person(b.speaker, recipient.display_name)
        for b in speech[lo:hi]
        if b.index != index
    )


def _answers_next(speech: Sequence[Block], index: int, recipient: Recipient) -> bool:
    return any(
        b.attributed and same_person(b.speaker, recipient.display_name)
        for b in speech[index + 1 : index + 1 + NEIGHBOURHOOD_BLOCKS]
    )


__all__ = ["Block", "TemplateRenderer", "blocks"]
