"""Deferred transcription from the recording (#525 C1) — the pure flow over ports.

A completed meeting with a master recording gains transcript rows on demand:
resolve the master via the recordings seam, POST it to the STT service, normalize
the detected language to ISO-639-1 (#355 defect 3), store rows through the
collector's durable-write seam. Every failure is a typed ``TranscribeFault`` —
never a silent ``[]`` (#355 defects 1 and 2; P18).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, Protocol


class TranscribeFault(Exception):
    """A typed refusal/failure of the deferred-transcribe flow.

    ``kind`` is the machine-readable reason the router maps to an HTTP status:
    ``not_found`` · ``no_recording`` · ``already_transcribed`` · ``no_segments`` ·
    ``provider_rejected`` · ``unavailable`` · ``stt_unconfigured``.
    ``provider_code``/``status`` carry the upstream provider's error verbatim so an
    operator sees WHY (0.10 collapsed a Groq model_not_found 404 into a generic 502).
    """

    def __init__(
        self,
        kind: str,
        detail: str = "",
        *,
        status: Optional[int] = None,
        provider_code: Optional[str] = None,
    ):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.status = status
        self.provider_code = provider_code


class SttTranscriber(Protocol):
    async def transcribe(self, audio: bytes, *, language: Optional[str] = None) -> dict: ...


# Whisper's own language inventory, inverted: full name (lowercased) → ISO-639-1 code.
# Groq/OpenAI verbose_json name the language in full ("English"); the collector's
# ``transcriptions.language`` column is String(10) and every consumer expects a code,
# so normalization happens HERE, at storage time (#525 A3).
_NAME_TO_ISO = {
    "english": "en", "chinese": "zh", "german": "de", "spanish": "es", "russian": "ru",
    "korean": "ko", "french": "fr", "japanese": "ja", "portuguese": "pt", "turkish": "tr",
    "polish": "pl", "catalan": "ca", "dutch": "nl", "arabic": "ar", "swedish": "sv",
    "italian": "it", "indonesian": "id", "hindi": "hi", "finnish": "fi", "vietnamese": "vi",
    "hebrew": "he", "ukrainian": "uk", "greek": "el", "malay": "ms", "czech": "cs",
    "romanian": "ro", "danish": "da", "hungarian": "hu", "tamil": "ta", "norwegian": "no",
    "thai": "th", "urdu": "ur", "croatian": "hr", "bulgarian": "bg", "lithuanian": "lt",
    "latin": "la", "maori": "mi", "malayalam": "ml", "welsh": "cy", "slovak": "sk",
    "telugu": "te", "persian": "fa", "latvian": "lv", "bengali": "bn", "serbian": "sr",
    "azerbaijani": "az", "slovenian": "sl", "kannada": "kn", "estonian": "et",
    "macedonian": "mk", "breton": "br", "basque": "eu", "icelandic": "is", "armenian": "hy",
    "nepali": "ne", "mongolian": "mn", "bosnian": "bs", "kazakh": "kk", "albanian": "sq",
    "swahili": "sw", "galician": "gl", "marathi": "mr", "punjabi": "pa", "sinhala": "si",
    "khmer": "km", "shona": "sn", "yoruba": "yo", "somali": "so", "afrikaans": "af",
    "occitan": "oc", "georgian": "ka", "belarusian": "be", "tajik": "tg", "sindhi": "sd",
    "gujarati": "gu", "amharic": "am", "yiddish": "yi", "lao": "lo", "uzbek": "uz",
    "faroese": "fo", "haitian creole": "ht", "pashto": "ps", "turkmen": "tk",
    "nynorsk": "nn", "maltese": "mt", "sanskrit": "sa", "luxembourgish": "lb",
    "myanmar": "my", "tibetan": "bo", "tagalog": "tl", "malagasy": "mg", "assamese": "as",
    "tatar": "tt", "hawaiian": "haw", "lingala": "ln", "hausa": "ha", "bashkir": "ba",
    "javanese": "jw", "sundanese": "su", "cantonese": "yue",
}


def normalize_language(value: Optional[str]) -> Optional[str]:
    """A provider language value → ISO-639-1 code, or None when it can't be one.

    Codes pass through; full names map via the whisper inventory; anything else
    stores as NULL — never a value the String(10) column and code-expecting
    consumers can't hold (the 0.10 read path dropped every segment over exactly
    this, #355 defect 3).
    """
    if not value:
        return None
    v = value.strip().lower()
    if v in _NAME_TO_ISO:
        return _NAME_TO_ISO[v]
    if len(v) <= 3:  # already a code (whisper's own form, incl. "haw"/"yue")
        return v
    return None


async def transcribe_meeting(
    *,
    store: Any,
    stt: SttTranscriber,
    resolve_master: Callable[[int], Awaitable[Optional[bytes]]],
    user_id: int,
    meeting_id: int,
    language: Optional[str] = None,
) -> dict:
    """Transcribe a completed meeting's master recording into transcript rows.

    Returns ``{"meeting_id", "segments_stored", "language"}``. Raises
    ``TranscribeFault`` for every refusal — the router owns the HTTP mapping.
    """
    doc = await store.get_transcript_by_id(user_id, meeting_id)
    if doc is None:
        raise TranscribeFault("not_found", f"meeting {meeting_id} not found for this user")
    if doc.get("segments"):
        # Q2 ruling (2026-07-21): a meeting with transcript rows is refused, typed.
        raise TranscribeFault(
            "already_transcribed",
            f"meeting {meeting_id} already has {len(doc['segments'])} transcript segments",
        )

    audio = await resolve_master(meeting_id)
    if audio is None:
        raise TranscribeFault(
            "no_recording",
            f"meeting {meeting_id} has no finalized master recording (recording disabled or empty)",
        )

    result = await stt.transcribe(audio, language=language)
    if "segments" not in result:
        # The ABSENT key means response_format=verbose_json was not honored (#355
        # defect 2). A present-but-empty list is legitimate silence, not this fault.
        raise TranscribeFault(
            "no_segments",
            "provider returned no segments[] — response_format=verbose_json not honored "
            "by the transcription backend",
        )

    lang = normalize_language(result.get("language") or language)
    segments = []
    for i, seg in enumerate(result["segments"]):
        if "start" not in seg or "end" not in seg or not str(seg.get("text", "")).strip():
            continue  # provider hygiene: a row needs a span and words (0.10's own filter)
        start = float(seg["start"])
        segments.append({
            "segment_id": f"deferred:{i}:{start:.3f}",  # the 0.10-observable id shape
            "start": start,
            "end": float(seg["end"]),
            "text": seg["text"],
            "language": lang,
            "source": "deferred",
        })
    await store.upsert_segments(meeting_id, segments)
    return {"meeting_id": meeting_id, "segments_stored": len(segments), "language": lang}
