"""A person's default transcription language — transcription-language.v1's user tier, identity's
reading.

Identity stores it (``users.data.transcription_prefs.language`` / ``.allowed_languages``) and hands
it to meetings in the internal bot-context answer as ``transcription_language``; meetings decides
precedence. The rule is the contract's, read here against the same goldens meeting-api reads
(``core/meetings/contracts/transcription-language.v1/golden``): codes are ``^[a-z]{2,3}$``, a list
holds each code once and at most 16, and when both are set the language is one of the list.
"""
from __future__ import annotations

import re
from typing import Any, Optional

CODE = re.compile(r"^[a-z]{2,3}$")
MAX_LANGUAGES = 16
LANGUAGE_KEY = "language"
ALLOWED_KEY = "allowed_languages"


class Refused(ValueError):
    """A value the contract refuses; the message names the field and the fix."""


def _code(value: Any) -> str:
    if not isinstance(value, str) or not CODE.match(value):
        raise Refused(f"'language' must be a two- or three-letter lower-case code such as 'de' "
                      f"or 'en' (got {value!r}); send \"\" to clear it")
    return value


def _codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise Refused("'allowed_languages' must be a list of codes such as [\"de\", \"en\"]; "
                      "send [] to clear it")
    if len(value) > MAX_LANGUAGES:
        raise Refused(f"'allowed_languages' names {len(value)} languages; the most is {MAX_LANGUAGES}")
    for v in value:
        if not isinstance(v, str) or not CODE.match(v):
            raise Refused(f"'allowed_languages' holds {v!r}, which is not a two- or three-letter "
                          "lower-case code")
    if len(set(value)) != len(value):
        raise Refused("'allowed_languages' names a language more than once")
    return list(value)


def apply(stored: dict, update: dict) -> dict:
    """``stored`` transcription prefs with the language fields of ``update`` applied (absent = keep,
    ``""``/``None`` = clear ``language``, ``[]``/``None`` = clear the list). Validated as a whole:
    the result must hold the membership rule, whichever half changed."""
    out = dict(stored or {})
    if LANGUAGE_KEY in update:
        raw = update[LANGUAGE_KEY]
        if raw in (None, ""):
            out.pop(LANGUAGE_KEY, None)
        else:
            out[LANGUAGE_KEY] = _code(raw)
    if ALLOWED_KEY in update:
        raw = update[ALLOWED_KEY]
        codes = [] if raw is None else _codes(raw)
        if codes:
            out[ALLOWED_KEY] = codes
        else:
            out.pop(ALLOWED_KEY, None)
    language, allowed = out.get(LANGUAGE_KEY), out.get(ALLOWED_KEY) or []
    if language and allowed and language not in allowed:
        raise Refused(f"'language' {language!r} must be one of 'allowed_languages' {allowed} — it is "
                      "the language a window detected outside the list falls back to")
    return out


def read(prefs: dict) -> dict:
    """The language fields of ``GET /user/transcription``."""
    prefs = prefs or {}
    return {LANGUAGE_KEY: prefs.get(LANGUAGE_KEY) or None,
            ALLOWED_KEY: list(prefs.get(ALLOWED_KEY) or [])}


def for_bot_context(prefs: dict) -> Optional[dict]:
    """The bot-context ``transcription_language`` block, or None when the person set neither."""
    fields = read(prefs)
    return fields if fields[LANGUAGE_KEY] or fields[ALLOWED_KEY] else None
