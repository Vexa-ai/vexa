"""Word-level probes. Contributory signals only — never decisive on their own.

Scope, stated up front because it bounds every claim these make: the patterns cover
**English, Russian and German**. On a record in any other language they return ~0, which
reads as "no dialogue markers" — so the lexical signals are wired as *soft* flags that
need corroboration, and the `domestic` probe can only ever ADD caution (force a hold),
never clear a record for sending.
"""

from __future__ import annotations

import re

#: Second-person address — "are you", "тебе", "kannst du". Present in conversation,
#: near-absent in narration/playback.
SECOND_PERSON = re.compile(
    r"\b(you|your|yours|you're|youre|yourself|y'all)\b"
    r"|\b(ты|тебя|тебе|тобой|вы|вас|вам|вами|твой|твоя|твоё|твое|твои|ваш|ваша|ваше|ваши)\b"
    r"|\b(du|dich|dir|dein|deine|deinen|euch|euer|ihnen)\b",
    re.IGNORECASE,
)

#: Interrogatives, for records whose punctuation the STT drops.
QUESTION_WORD = re.compile(
    r"\b(what|why|how|when|who|where|which|can|could|would|should|do|does|did|is|are|will)\b"
    r"|\b(что|почему|зачем|как|когда|кто|где|какой|какая|какие|можно|можешь|можете|ли)\b"
    r"|\b(was|warum|wie|wann|wer|wo|welche|welcher|kannst|k[oö]nnen|k[oö]nnten)\b",
    re.IGNORECASE,
)

#: Domestic / household vocabulary. This is the `sensitive_context` probe: a record
#: soaked in it is a private life, not a meeting — and a private life must never be
#: broadcast to an invite list. Deliberately over-eager; it can only force a hold.
DOMESTIC = re.compile(
    r"\b(мам[аеуы]?|пап[аеуы]?|сынок|доченьк\w*|бабушк\w*|дедушк\w*"
    r"|ребёнок|ребенок|дет(и|ей|ям|ьми)|школ\w*|уроки|ужин\w*|завтрак\w*"
    r"|кушать|поесть|спать|ложись|одевайся|выезжать|посуд\w*|стирк\w*)\b"
    r"|\b(mom|mum|mommy|mummy|dad|daddy|kids|kiddo|grandma|grandpa|granny"
    r"|homework|dinner|breakfast|bedtime|groceries|laundry|dishes)\b"
    r"|\b(mama|papa|kinder|hausaufgaben|abendessen|fr[uü]hst[uü]ck)\b",
    re.IGNORECASE,
)


def rate(pattern: re.Pattern[str], texts: list[str]) -> float:
    """Fraction of segments in which `pattern` matches at least once."""
    if not texts:
        return 0.0
    return sum(1 for t in texts if pattern.search(t)) / len(texts)


def question_mark_rate(texts: list[str]) -> float:
    if not texts:
        return 0.0
    return sum(1 for t in texts if "?" in t) / len(texts)
