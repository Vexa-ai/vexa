"""The cue lexicons the deterministic renderer reads, and the sentence surgery around them.

A cue is a phrase that makes a sentence *evidence* of something: an undertaking, a decision,
a question. The deterministic renderer emits nothing a cue has not matched — it does not
judge salience and it does not infer. That boundary is the point: everything this module can
prove is quotable back to a sentence in the record, and everything else is the model
renderer's job (`render_llm`, stubbed). The archive's highest-value lines were derived
deltas nobody said aloud, and they were also the ones most likely to be wrong; keeping the
two renderers on opposite sides of this line is what makes that difference measurable rather
than an argument.

English and Russian carry lexicons because the founder archive is bilingual and the
bilingual records are where a naive renderer degrades first. A language with no lexicon
matches the English one, which under-produces rather than mis-produces.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Pattern

_FLAGS = re.IGNORECASE | re.UNICODE


def _compile(patterns: Iterable[str]) -> tuple[Pattern[str], ...]:
    return tuple(re.compile(p, _FLAGS) for p in patterns)


#: First-person undertakings — "I will do this thing".
#:
#: Every contraction is anchored on its apostrophe. An unanchored ``i\s*ll`` matches "ill"
#: and ``we\s*ll`` matches "well"; the loose form filed six of Marvin's questions under
#: "You committed to" the first time this renderer ran on a real record.
COMMITMENT: Mapping[str, tuple[Pattern[str], ...]] = {
    "en": _compile(
        [
            r"\bi['’]ll\b",
            r"\bi will\b",
            r"\bi(?:'m| am) (?:going to|gonna)\b",
            r"\bi shall\b",
            r"\blet me (?:send|share|check|look|write|put|set|get|make|run|do|take|pull|draft|prepare)\b",
            r"\bwe['’]ll\b",
            r"\bwe will\b",
            r"\bi(?:'m| am) happy to\b",
            r"\bi(?:'ve| have) got to\b",
            r"\bi(?:'m| am) (?:on|taking) (?:it|that)\b",
            r"\bmy (?:action|todo|to-do)\b",
        ]
    ),
    "ru": _compile(
        [
            r"\bя\s+(?:пришлю|отправлю|сделаю|напишу|посмотрю|проверю|подготовлю|скину|дам|покажу|заведу|добавлю|соберу|уточню|займусь|возьму)\b",
            r"\bя буду\b",
            r"\bя постараюсь\b",
            r"\bдавай(?:те)? я\b",
            r"\bмы\s+(?:сделаем|подготовим|пришлём|пришлем|отправим|посмотрим|заведём|заведем)\b",
            r"(?:^|[\s,—-])(?:пришлю|отправлю|сделаю|напишу|посмотрю|проверю|подготовлю|скину|уточню|соберу|заведу|добавлю|займусь)\b",
            r"\bс меня\b",
            r"\bмяч на моей стороне\b",
        ]
    ),
}

#: Settled outcomes — "this is what we are doing".
DECISION: Mapping[str, tuple[Pattern[str], ...]] = {
    "en": _compile(
        [
            r"\bwe (?:decided|have decided|'ve decided)\b",
            r"\bwe (?:agreed|have agreed|'ve agreed)\b",
            r"\bwe (?:settled|landed) on\b",
            r"\blet(?:'s| us) go with\b",
            r"\bwe(?:'ll| will) go with\b",
            r"\bthe decision is\b",
            r"\bthat(?:'s| is) (?:decided|settled|agreed)\b",
            r"\bso the plan is\b",
        ]
    ),
    "ru": _compile(
        [
            r"\bрешили\b",
            r"\bдоговорились\b",
            r"\bостановились на\b",
            r"\bпринято решение\b",
            r"\bпо итогу\s+(?:решаем|делаем)\b",
            r"\bзначит(?:,)? делаем\b",
        ]
    ),
}

#: Sentences that carry no information on their own, however well they match a cue.
FILLER: tuple[Pattern[str], ...] = _compile(
    [
        r"^\W*(?:yeah|yes|no|ok|okay|right|sure|exactly|thanks|thank you)\W*$",
        r"^\W*(?:да|нет|ага|угу|окей|хорошо|спасибо|понятно)\W*$",
        r"^\W*(?:i\s*['’]?\s*ll|we\s*['’]?\s*ll)\W*$",
    ]
)

#: Item length window. Below the floor a "sentence" is a transcript fragment; above the
#: ceiling it is an unpunctuated monologue block and is truncated rather than dropped.
MIN_ITEM_CHARS = 25
MAX_ITEM_CHARS = 280
#: A question shorter than this is a conversational tic ("right?", "you know?").
MIN_QUESTION_WORDS = 5

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def lexicon(table: Mapping[str, tuple[Pattern[str], ...]], language: str) -> tuple[Pattern[str], ...]:
    return table.get((language or "en").split("-")[0].lower(), table["en"])


def sentences(text: str) -> tuple[str, ...]:
    """Split a block of speech into sentences.

    Transcript segments are chunked by the recogniser, not by grammar, so the renderer works
    over *blocks* (consecutive turns by one speaker) re-split here. Splitting the raw
    segments instead is what produces items like "on your setup. So we'll see if it's going
    to be…" — a fragment quoted back at a participant as though it were their commitment.
    """
    return tuple(s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip())


def is_filler(sentence: str) -> bool:
    return any(p.search(sentence) for p in FILLER)


def matches(sentence: str, patterns: Iterable[Pattern[str]]) -> bool:
    return any(p.search(sentence) for p in patterns)


def asks(sentence: str) -> bool:
    """Any interrogative, however short. A question is never an undertaking or a decision,
    whatever cue it happens to contain — "are we going to send it?" is not a commitment."""
    return sentence.rstrip().endswith("?")


def is_question(sentence: str) -> bool:
    stripped = sentence.rstrip()
    return stripped.endswith("?") and len(stripped.split()) >= MIN_QUESTION_WORDS


def usable(sentence: str) -> bool:
    return len(sentence) >= MIN_ITEM_CHARS and not is_filler(sentence)


def trim(sentence: str) -> str:
    """One item, at most :data:`MAX_ITEM_CHARS`, cut at a word boundary."""
    text = re.sub(r"\s+", " ", sentence).strip()
    if len(text) <= MAX_ITEM_CHARS:
        return text
    cut = text[:MAX_ITEM_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-—")
    return f"{cut}…"
