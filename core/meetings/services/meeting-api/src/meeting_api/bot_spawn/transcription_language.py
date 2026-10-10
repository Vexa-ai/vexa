"""The language a meeting bot transcribes in — transcription-language.v1, meeting-api's reading.

Three tiers can set it, and the first one that sets EITHER field supplies the whole setting:

  1. meeting     — the ``POST /bots`` body (``language`` / ``allowed_languages``; ``"auto"`` sets
                   this tier to auto over any default), and every accepted live change;
  2. user        — the person's default, stored by identity and read out of the bot-context answer
                   this service already fetches on every spawn (``transcription_language``);
  3. deployment  — ``DEFAULT_TRANSCRIPTION_LANGUAGE`` + ``DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES``;

and auto underneath. Whole-setting precedence, not field-by-field: a meeting that says ``de`` means
"pinned to German", and must not quietly turn into "German or English" because a lower tier holds a
list.

The one rule beyond shapes: when both fields are set, ``language`` is one of the list. Every input is
held to it here, the same way the contract's ``validate.mjs`` and identity hold it, against the same
goldens (``core/meetings/contracts/transcription-language.v1/golden``).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

#: A Whisper language code (`de`, `en`, `yue`).
CODE = re.compile(r"^[a-z]{2,3}$")
#: The longest list a tier may hold.
MAX_LANGUAGES = 16

ENV_LANGUAGE = "DEFAULT_TRANSCRIPTION_LANGUAGE"
ENV_ALLOWED = "DEFAULT_TRANSCRIPTION_ALLOWED_LANGUAGES"

#: The word that sets the meeting tier to auto on a spawn request.
AUTO = "auto"


class LanguageRefused(ValueError):
    """An input this contract refuses. The message names the field and the fix."""


@dataclass(frozen=True)
class LanguageSetting:
    language: Optional[str]
    allowed_languages: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"language": self.language, "allowed_languages": list(self.allowed_languages)}


AUTO_SETTING = LanguageSetting(None, ())


def _code(value: Any, field: str) -> str:
    if not isinstance(value, str) or not CODE.match(value):
        raise LanguageRefused(
            f"'{field}' must be a two- or three-letter lower-case language code such as 'de' or "
            f"'en' (got {value!r})")
    return value


def _codes(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise LanguageRefused(f"'{field}' must be a list of language codes such as [\"de\", \"en\"]")
    if len(value) > MAX_LANGUAGES:
        raise LanguageRefused(f"'{field}' names {len(value)} languages; the most is {MAX_LANGUAGES}")
    codes = tuple(_code(v, field) for v in value)
    if len(set(codes)) != len(codes):
        raise LanguageRefused(f"'{field}' names a language more than once")
    return codes


def _setting(language: Optional[str], allowed: tuple[str, ...]) -> LanguageSetting:
    if language is not None and allowed and language not in allowed:
        raise LanguageRefused(
            f"'language' {language!r} must be one of 'allowed_languages' {list(allowed)} — it is the "
            "language a window detected outside the list falls back to")
    return LanguageSetting(language, allowed)


def spawn_tier(language: Any, allowed_languages: Any) -> Optional[LanguageSetting]:
    """The meeting tier of a spawn request, or None when the request leaves it to lower tiers.

    Null or absent fields are unset; an empty list is unset; ``language == "auto"`` (with no list)
    is the meeting tier set to auto."""
    if language == AUTO:
        if allowed_languages not in (None, []):
            raise LanguageRefused("'language' 'auto' cannot be combined with 'allowed_languages'")
        return AUTO_SETTING
    lang = None if language is None else _code(language, "language")
    allowed = () if allowed_languages is None else _codes(allowed_languages, "allowed_languages")
    if lang is None and not allowed:
        return None
    return _setting(lang, allowed)


def config_update(body: Any) -> tuple[LanguageSetting, Optional[str]]:
    """A live change's body (``ConfigUpdate``) → the setting it REPLACES the running one with, and
    the task it carries. Omitted or null fields are cleared; at least one of the two is present."""
    if not isinstance(body, dict):
        raise LanguageRefused("the body must be an object with 'language' and/or 'allowed_languages'")
    extra = set(body) - {"language", "allowed_languages", "task"}
    if extra:
        raise LanguageRefused(f"unknown field(s) {sorted(extra)}; a change names 'language' and/or "
                              "'allowed_languages'")
    if "language" not in body and "allowed_languages" not in body:
        raise LanguageRefused("a change names 'language' and/or 'allowed_languages' "
                              "(both null means auto)")
    task = body.get("task")
    if task is not None and not isinstance(task, str):
        raise LanguageRefused("'task' must be a string")
    raw_lang = body.get("language")
    lang = None if raw_lang is None else _code(raw_lang, "language")
    raw_list = body.get("allowed_languages")
    allowed = () if raw_list is None else _codes(raw_list, "allowed_languages")
    return _setting(lang, allowed), task


def user_tier(bot_context: Mapping[str, Any]) -> Optional[LanguageSetting]:
    """The person's default out of identity's bot-context answer, or None.

    Identity validates on write, so a value refused here is a store this build cannot read; it is
    treated as unset (a default is a convenience, joining the call is the product) and the caller
    logs it."""
    block = bot_context.get("transcription_language")
    if not isinstance(block, dict):
        return None
    lang = block.get("language") or None
    allowed = block.get("allowed_languages") or []
    return spawn_tier(lang, allowed if allowed else None)


def deployment_tier(env: Optional[Mapping[str, str]] = None) -> Optional[LanguageSetting]:
    """The deployment default from the environment, or None. Raises ``LanguageRefused`` on a value
    the contract refuses — ``__main__`` calls this at boot so a bad value stops the service there."""
    env = os.environ if env is None else env
    raw_lang = (env.get(ENV_LANGUAGE) or "").strip()
    raw_list = (env.get(ENV_ALLOWED) or "").strip()
    try:
        lang = _code(raw_lang, ENV_LANGUAGE) if raw_lang else None
        allowed = _codes(raw_list.split(","), ENV_ALLOWED) if raw_list else ()
        if lang is None and not allowed:
            return None
        return _setting(lang, allowed)
    except LanguageRefused as refused:
        raise LanguageRefused(
            f"{refused} — set {ENV_LANGUAGE} to one code (e.g. de) and {ENV_ALLOWED} to "
            "comma-separated codes with no spaces (e.g. de,en), or leave them empty") from None


def resolve(
    meeting: Optional[LanguageSetting],
    user: Optional[LanguageSetting],
    deployment: Optional[LanguageSetting],
) -> dict:
    """The effective setting (``EffectiveSetting``): the first tier that is set, with its source."""
    for source, tier in (("meeting", meeting), ("user", user), ("deployment", deployment)):
        if tier is not None:
            return {**tier.as_dict(), "source": source}
    return {**AUTO_SETTING.as_dict(), "source": "auto"}


def reconfigure_act(setting: LanguageSetting, task: Optional[str] = None) -> dict:
    """The acts.v1 ``reconfigure`` that carries ``setting`` to a running bot. Both fields are always
    sent, so the bot ends up on exactly this setting whatever it held before."""
    act: dict[str, Any] = {
        "action": "reconfigure",
        "language": setting.language,
        "allowedLanguages": list(setting.allowed_languages),
    }
    if task is not None:
        act["task"] = task
    return act
