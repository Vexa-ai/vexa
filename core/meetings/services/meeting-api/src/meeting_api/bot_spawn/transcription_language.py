"""The language a meeting bot transcribes in — transcription-language.v1, meeting-api's reading.

Three tiers can set it, and the first one that sets EITHER field supplies the whole setting:

  1. meeting     — the ``POST /bots`` body (``language`` / ``allowed_languages``; ``"auto"`` sets
                   this tier to auto over any default), and every accepted live change;
  2. user        — the person's default, stored by identity and read out of the bot-context answer
                   this service already fetches on every spawn (``transcription_language``);
  2b. history    — with no default of their own, the languages the person's recent meetings were
                   spoken in (``history_tier``), restricted to that list;
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


#: Whisper's language names (and the aliases its tokenizer accepts) → code. 0.12 passed ``language``
#: through untouched, so callers sent names as well as codes; `legacy_spawn_language` reads them.
_NAMES = {
    "english": "en", "chinese": "zh", "mandarin": "zh", "german": "de", "spanish": "es",
    "castilian": "es", "russian": "ru", "korean": "ko", "french": "fr", "japanese": "ja",
    "portuguese": "pt", "turkish": "tr", "polish": "pl", "catalan": "ca", "valencian": "ca",
    "dutch": "nl", "flemish": "nl", "arabic": "ar", "swedish": "sv", "italian": "it",
    "indonesian": "id", "hindi": "hi", "finnish": "fi", "vietnamese": "vi", "hebrew": "he",
    "ukrainian": "uk", "greek": "el", "malay": "ms", "czech": "cs", "romanian": "ro",
    "moldavian": "ro", "moldovan": "ro", "danish": "da", "hungarian": "hu", "tamil": "ta",
    "norwegian": "no", "thai": "th", "urdu": "ur", "croatian": "hr", "bulgarian": "bg",
    "lithuanian": "lt", "latin": "la", "maori": "mi", "malayalam": "ml", "welsh": "cy",
    "slovak": "sk", "telugu": "te", "persian": "fa", "latvian": "lv", "bengali": "bn",
    "serbian": "sr", "azerbaijani": "az", "slovenian": "sl", "kannada": "kn", "estonian": "et",
    "macedonian": "mk", "breton": "br", "basque": "eu", "icelandic": "is", "armenian": "hy",
    "nepali": "ne", "mongolian": "mn", "bosnian": "bs", "kazakh": "kk", "albanian": "sq",
    "swahili": "sw", "galician": "gl", "marathi": "mr", "punjabi": "pa", "panjabi": "pa",
    "sinhala": "si", "sinhalese": "si", "khmer": "km", "shona": "sn", "yoruba": "yo",
    "somali": "so", "afrikaans": "af", "occitan": "oc", "georgian": "ka", "belarusian": "be",
    "tajik": "tg", "sindhi": "sd", "gujarati": "gu", "amharic": "am", "yiddish": "yi", "lao": "lo",
    "uzbek": "uz", "faroese": "fo", "haitian creole": "ht", "haitian": "ht", "pashto": "ps",
    "pushto": "ps", "turkmen": "tk", "nynorsk": "nn", "maltese": "mt", "sanskrit": "sa",
    "luxembourgish": "lb", "letzeburgesch": "lb", "myanmar": "my", "burmese": "my",
    "tibetan": "bo", "tagalog": "tl", "malagasy": "mg", "assamese": "as", "tatar": "tt",
    "hawaiian": "haw", "lingala": "ln", "hausa": "ha", "bashkir": "ba", "javanese": "jw",
    "sundanese": "su", "cantonese": "yue",
}
#: Deprecated ISO 639 codes some clients still send.
_OLD_CODES = {"iw": "he", "in": "id", "ji": "yi", "jv": "jw"}


def legacy_spawn_language(language: Any, allowed_languages: Any = None) -> tuple[Any, Optional[str]]:
    """``POST /bots`` ``language`` as 0.12 callers send it → the value `spawn_tier` reads, and a note
    when the request's value was changed or ignored (for the log, never a refusal).

    0.12 forwarded ``language`` to the bot untouched, so ``en-US``, ``EN``, ``pt_BR`` and ``English``
    were all accepted. They are read here as the code they name (``en``, ``en``, ``pt``, ``en``). A
    value that names no language is treated as unset — the spawn goes ahead on the lower tiers, as it
    did on 0.12 — instead of a 422 the caller never got before.

    This leniency is for the 0.12 request shape only: a request that also sends
    ``allowed_languages`` (a 0.13 field) is held to the contract exactly, and so is every other input
    (the live change, the person's default, the deployment default)."""
    if allowed_languages is not None or language is None or not isinstance(language, str):
        return language, None
    raw = language.strip()
    if raw in _OLD_CODES:
        return _OLD_CODES[raw], f"language {raw!r} read as {_OLD_CODES[raw]!r}"
    if CODE.match(raw) or raw == AUTO:
        return raw, None
    low = raw.lower()
    if low == AUTO:
        return AUTO, f"language {raw!r} read as 'auto'"
    primary = low.replace("_", "-").split("-", 1)[0]
    code = _OLD_CODES.get(primary, primary)
    if CODE.match(code) and ("-" in low.replace("_", "-") or low == primary):
        return code, f"language {raw!r} read as {code!r}"
    name = _NAMES.get(" ".join(low.split()))
    if name:
        return name, f"language {raw!r} read as {name!r}"
    return None, f"language {raw!r} names no language code; ignored (lower tiers apply)"


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


#: The history tier: a language counts when it is at least this share of the person's recent
#: segments, with at least this many segments in all; the tier sets a list of 2 to 3 languages.
HISTORY_MIN_SHARE = 0.05
HISTORY_MIN_SEGMENTS = 50
HISTORY_MAX_LANGUAGES = 3


#: Whether a person who set no language gets the languages of their recent meetings (``history_tier``).
ENV_HISTORY = "VEXA_LANGUAGE_HISTORY_TIER"


def history_tier_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """``on`` turns the history tier on; anything else (the default) is the 0.12 behaviour, where a
    person who set nothing gets the deployment default, then auto-detection."""
    env = os.environ if env is None else env
    return (env.get(ENV_HISTORY) or "").strip().lower() in ("on", "true", "1", "yes")


def history_tier(counts: Optional[Mapping[str, int]]) -> Optional[LanguageSetting]:
    """The languages this person's recent meetings were actually spoken in, as a RESTRICTED setting,
    or None.

    A real call (2026-10-14) in Russian and English came back with 22 segments tagged pt, de, pl,
    fr, cs and others — short windows detected freely. With no setting of their own, the person's
    own history says which languages they speak: every language with at least
    ``HISTORY_MIN_SHARE`` of their recent segments. One language is not a list (a person who has
    only spoken English yet is not pinned to it), and a thin history is not one either."""
    if not counts:
        return None
    valid = {k: int(v) for k, v in counts.items() if isinstance(k, str) and CODE.match(k) and int(v) > 0}
    total = sum(valid.values())
    if total < HISTORY_MIN_SEGMENTS:
        return None
    kept = [k for k, n in sorted(valid.items(), key=lambda kv: (-kv[1], kv[0]))
            if n / total >= HISTORY_MIN_SHARE][:HISTORY_MAX_LANGUAGES]
    if len(kept) < 2:
        return None
    return _setting(None, tuple(kept))


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
    history: Optional[LanguageSetting] = None,
) -> dict:
    """The effective setting (``EffectiveSetting``): the first tier that is set, with its source.
    The person's own history comes after their explicit default and before the deployment's."""
    for source, tier in (("meeting", meeting), ("user", user), ("history", history),
                         ("deployment", deployment)):
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
