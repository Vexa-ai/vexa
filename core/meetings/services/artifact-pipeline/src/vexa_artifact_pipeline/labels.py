"""Everything the artifact says in its own voice, per language, in one table.

The artifact is addressed to a participant in the meeting's language, so every fixed
string it carries — the three header keys, the section headings, the record-link text,
the closing line — is language-dependent. Keeping them here rather than inside the
renderers is what makes two renderers produce the *same* document shape: a renderer
chooses which sections exist and what goes in them, never what they are called.

The header keys are also a wire contract. The postman that mails an artifact parses
``To`` / ``Meeting`` / ``record`` (and their Russian spellings) out of the first lines to
find the record the magic link must point at, so these strings are read by a machine as
well as by a person.

A language with no entry falls back to English headings — visible, and better than
inventing a translation. `meeting-language ≠ recipient-language` is a real case the
product has not decided yet (§1 of the spec leaves it open); today the artifact speaks
the language the meeting was held in.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

#: Section order in the emitted markdown, so two renderers cannot disagree about it.
#: A section kind absent from a given artifact is simply not emitted.
KIND_ORDER: tuple[str, ...] = (
    "decided",
    "you_committed",
    "owed_to_you",
    "asked_of_you",
    "worth_knowing",
    "nothing_recorded",
)


@dataclass(frozen=True)
class Vocabulary:
    language: str
    to: str
    meeting: str
    record: str
    open_record: str
    placeholder: str
    minutes: str
    footer: str
    nothing_recorded: str
    sections: Mapping[str, str]

    def heading(self, kind: str) -> str:
        return self.sections.get(kind, kind.replace("_", " "))


ENGLISH = Vocabulary(
    language="en",
    to="To",
    meeting="Meeting",
    record="record",
    open_record="open the record",
    placeholder="(placeholder link)",
    minutes="m",
    footer="Reply to adjust what I track for you.",
    nothing_recorded=(
        "Nothing was decided, committed or asked that names you. "
        "The record is linked above if you want to read it yourself."
    ),
    sections=MappingProxyType(
        {
            "decided": "Decided",
            "you_committed": "You committed to",
            "owed_to_you": "Owed to you",
            "asked_of_you": "Asked of you",
            "worth_knowing": "Worth knowing",
            "nothing_recorded": "Nothing changed for you",
        }
    ),
)

RUSSIAN = Vocabulary(
    language="ru",
    to="Кому",
    meeting="Встреча",
    record="запись",
    open_record="открыть запись",
    placeholder="(ссылка-заглушка)",
    minutes="мин",
    footer="Ответь, чтобы поменять то, что я за тебя отслеживаю.",
    nothing_recorded=(
        "Решений, обещаний и вопросов, которые касаются тебя, не зафиксировано. "
        "Ссылка на запись выше, если хочешь прочитать сам."
    ),
    sections=MappingProxyType(
        {
            "decided": "Решено",
            "you_committed": "Ты взял на себя",
            "owed_to_you": "Тебе должны",
            "asked_of_you": "Тебя спросили",
            "worth_knowing": "Стоит знать",
            "nothing_recorded": "Для тебя ничего не изменилось",
        }
    ),
)

_BY_LANGUAGE: Mapping[str, Vocabulary] = MappingProxyType({"en": ENGLISH, "ru": RUSSIAN})

#: Platform ids as the meeting API states them → the name a participant recognises.
PLATFORM_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "google_meet": "Google Meet",
        "google-meet": "Google Meet",
        "meet": "Google Meet",
        "teams": "Microsoft Teams",
        "msteams": "Microsoft Teams",
        "zoom": "Zoom",
        "jitsi": "Jitsi",
        "discord": "Discord",
    }
)


def vocabulary_for(language: str | None) -> Vocabulary:
    return _BY_LANGUAGE.get((language or "en").split("-")[0].lower(), ENGLISH)


def platform_name(platform: str | None) -> str:
    if not platform:
        return ""
    return PLATFORM_NAMES.get(str(platform).lower(), str(platform))
