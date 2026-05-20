"""
Vexa TTS Service

Local text-to-speech service using Piper TTS (ONNX).
Exposes OpenAI-compatible /v1/audio/speech endpoint for use by the vexa-bot.
Voices are auto-downloaded from HuggingFace on first use.
"""

import io
import os
import logging
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import numpy as np

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response, StreamingResponse
from fastapi.security import APIKeyHeader

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VOICES_DIR = Path(os.getenv("PIPER_VOICES_DIR", "/app/voices"))
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Target sample rate for output audio.  Piper models output at 22050 Hz but
# the vexa-bot (paplay) expects 24000 Hz.  We resample when they differ.
OUTPUT_SAMPLE_RATE = int(os.getenv("TTS_OUTPUT_SAMPLE_RATE", "24000"))

# Piper voices that should be ready before a deployment accepts traffic. These
# are the supported "major language" voices for prompt /speak output today.
MAJOR_DEFAULT_VOICES = [
    "en_US-amy-medium",
    "en_US-danny-low",
    "es_ES-davefx-medium",
    "fr_FR-siwis-medium",
    "de_DE-thorsten-medium",
    "it_IT-paola-medium",
    "pt_BR-faber-medium",
    "nl_NL-mls-medium",
    "pl_PL-mc_speech-medium",
    "ru_RU-irina-medium",
    "uk_UA-ukrainian_tts-medium",
    "zh_CN-huayan-medium",
    "ar_JO-kareem-medium",
    "tr_TR-dfki-medium",
    "hi_IN-pratham-medium",
]


def _configured_default_voices() -> list[str]:
    raw = os.getenv("PIPER_DEFAULT_VOICES", "major").strip()
    if raw.lower() == "major":
        return MAJOR_DEFAULT_VOICES.copy()
    return [voice.strip() for voice in raw.split(",") if voice.strip()]


DEFAULT_VOICES = _configured_default_voices()
DEFAULT_LOADED_VOICES = [
    voice.strip()
    for voice in os.getenv(
        "PIPER_LOAD_VOICES",
        "en_US-amy-medium,en_US-danny-low,pt_BR-faber-medium,es_ES-davefx-medium",
    ).split(",")
    if voice.strip()
]
PIPER_PRELOAD_STRICT = os.getenv("PIPER_PRELOAD_STRICT", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Map OpenAI voice names to Piper voice names for backward compatibility
VOICE_ALIASES: dict[str, str] = {
    "alloy": "en_US-amy-medium",
    "echo": "en_US-danny-low",
    "fable": "en_US-joe-medium",
    "onyx": "en_US-ryan-medium",
    "nova": "en_US-kristin-medium",
    "shimmer": "en_US-lessac-medium",
}

# Default Piper voice per ISO-639-1 language code. Used when the caller
# does not pin a specific voice and we infer language from the input text.
# All names match the rhasspy/piper-voices catalogue on HuggingFace.
LANG_DEFAULT_VOICE: dict[str, str] = {
    "en": "en_US-amy-medium",
    "es": "es_ES-davefx-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "it": "it_IT-paola-medium",
    "pt": "pt_BR-faber-medium",
    "nl": "nl_NL-mls-medium",
    "pl": "pl_PL-mc_speech-medium",
    "ru": "ru_RU-irina-medium",
    "uk": "uk_UA-ukrainian_tts-medium",
    "zh": "zh_CN-huayan-medium",
    # ja: rhasspy/piper-voices has no Japanese voice today; entries
    # detected as 'ja' fall through to the English default fallback
    # with a structured WARN (tts.lang_detection.unmapped). Followed
    # up under TTS engine-swap research.
    "ar": "ar_JO-kareem-medium",
    "tr": "tr_TR-dfki-medium",
    "ro": "ro_RO-mihai-medium",
    "cs": "cs_CZ-jirka-medium",
    "hu": "hu_HU-anna-medium",
    "el": "el_GR-rapunzelina-low",
    "fi": "fi_FI-harri-medium",
    "da": "da_DK-talesyntese-medium",
    "sv": "sv_SE-nst-medium",
    "no": "no_NO-talesyntese-medium",
    "ca": "ca_ES-upc_ona-medium",
    "vi": "vi_VN-vais1000-medium",
    "fa": "fa_IR-amir-medium",
    "sk": "sk_SK-lili-medium",
    "sl": "sl_SI-artur-medium",
    "lv": "lv_LV-aivars-medium",
    "sr": "sr_RS-serbski_institut-medium",
    "hi": "hi_IN-pratham-medium",
}

# Local file paths are only constructed for the Piper voices this release
# supports. This keeps `/v1/audio/speech` from turning arbitrary request
# strings into filesystem paths while still covering aliases and auto-language
# routing.
SUPPORTED_PIPER_VOICES: dict[str, str] = {
    voice_name: voice_name
    for voice_name in sorted(
        set(MAJOR_DEFAULT_VOICES)
        | set(VOICE_ALIASES.values())
        | set(LANG_DEFAULT_VOICE.values())
    )
}

# Unicode-script → ISO-639-1 hint. Cheap pre-check before invoking
# langdetect; for non-Latin scripts the script alone is enough to pick
# a language with high confidence.
_SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0400, 0x04FF, "ru"),    # Cyrillic — overridden to "uk" if Ukrainian-only chars present (handled below)
    (0x0500, 0x052F, "ru"),    # Cyrillic Supplement
    (0x0590, 0x05FF, "he"),    # Hebrew (no voice today; falls through to en + WARN)
    (0x0600, 0x06FF, "ar"),    # Arabic
    (0x0750, 0x077F, "ar"),    # Arabic Supplement
    (0x08A0, 0x08FF, "ar"),    # Arabic Extended-A
    (0x0900, 0x097F, "hi"),    # Devanagari
    (0x0E00, 0x0E7F, "th"),    # Thai (no voice today; falls through to en + WARN)
    (0x0370, 0x03FF, "el"),    # Greek
    (0x3040, 0x309F, "ja"),    # Hiragana
    (0x30A0, 0x30FF, "ja"),    # Katakana
    (0xAC00, 0xD7AF, "ko"),    # Hangul (no voice today; falls through to en + WARN)
    (0x4E00, 0x9FFF, "zh"),    # CJK Unified Ideographs (defaults to zh; ja text without kana is ambiguous but rare)
]


def _detect_language(text: str) -> Optional[str]:
    """Return ISO-639-1 lang code for `text`, or None if undetermined.

    Strategy:
      1. Pre-pass for Hiragana/Katakana — definitive Japanese marker even
         when mixed with CJK Unified Ideographs (kanji).
      2. Unicode-script heuristic for the first non-ASCII char.
      3. Latin-script disambiguation via langdetect.
    """
    if not text:
        return None

    # 1. Whole-text scan: any kana → Japanese (kanji-only is ambiguous
    # zh↔ja, defaults to zh below; with kana present the answer is
    # unambiguous).
    for ch in text:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF):
            return "ja"
        if (0xAC00 <= cp <= 0xD7AF):
            # Hangul anywhere → Korean.
            return "ko"

    # 2. Script heuristic — first non-ASCII char drives the decision.
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            continue
        for lo, hi, lang in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                return lang
        # First non-ASCII char isn't a tracked script; fall through to langdetect.
        break

    # 3. langdetect for Latin-script text (en/es/fr/de/it/pt/nl/...).
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # deterministic — same text → same result
        return detect(text)
    except Exception as exc:
        # langdetect raises LangDetectException on inputs too short / no
        # detectable features. Surface a structured signal instead of
        # silently swallowing — caller logs `tts.lang_detection.failed`.
        logger.debug("[TTS] langdetect failed on text len=%d: %s", len(text), exc)
        return None

# ---------------------------------------------------------------------------
# Audio resampling (linear interpolation — lightweight, no scipy needed)
# ---------------------------------------------------------------------------


def _resample_int16(pcm_int16: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample raw Int16LE mono PCM from src_rate to dst_rate."""
    if src_rate == dst_rate:
        return pcm_int16
    samples = np.frombuffer(pcm_int16, dtype=np.int16).astype(np.float32)
    duration = len(samples) / src_rate
    n_out = int(duration * dst_rate)
    indices = np.linspace(0, len(samples) - 1, n_out)
    resampled = np.interp(indices, np.arange(len(samples)), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


# ---------------------------------------------------------------------------
# Voice manager — download and cache Piper voices
# ---------------------------------------------------------------------------

_loaded_voices: dict[str, "PiperVoice"] = {}  # type: ignore[name-defined]
def _voice_model_paths(voice_name: str) -> tuple[Path, Path]:
    """Return safe local Piper model paths for a validated catalogue voice."""
    voice_file_stem = SUPPORTED_PIPER_VOICES.get(voice_name)
    if voice_file_stem is None:
        raise HTTPException(status_code=400, detail=f"Unsupported voice name: {voice_name!r}")

    voices_root = VOICES_DIR.resolve()
    model_path = (voices_root / f"{voice_file_stem}.onnx").resolve()
    config_path = (voices_root / f"{voice_file_stem}.onnx.json").resolve()
    try:
        model_path.relative_to(voices_root)
        config_path.relative_to(voices_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid voice path: {voice_name!r}") from exc

    return model_path, config_path


def _download_voice_model(voice_name: str) -> None:
    """Ensure a Piper voice model exists locally without loading it into RAM."""
    from piper.download_voices import download_voice

    model_path, config_path = _voice_model_paths(voice_name)

    if model_path.exists() and config_path.exists():
        return

    logger.info("[TTS] Downloading voice: %s -> %s", voice_name, VOICES_DIR)
    try:
        download_voice(voice_name, VOICES_DIR)
    except Exception as exc:
        logger.error("[TTS] Failed to download voice %s: %s", voice_name, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Voice '{voice_name}' not found and could not be downloaded: {exc}",
        ) from exc


def _resolve_voice_name(name: str, *, text: str = "") -> str:
    """Resolve a voice request to a Piper voice name.

    Resolution order:
      1. `auto` (or empty) → detect language of `text`, look up
         LANG_DEFAULT_VOICE; if no mapping, log a structured WARN
         (`tts.lang_detection.unmapped`) and use English default.
      2. Known OpenAI-style alias (alloy / nova / echo / …):
         a. If the input text is Latin-script-only (no Cyrillic, CJK,
            Arabic, etc.), use the alias's English Piper voice as the
            historical default.
         b. If the text has a non-Latin script, the alias would route
            English espeak-ng over non-Latin codepoints and emit
            letter-by-letter spelling. Detect language from text and
            override the alias with the detected language's voice. Log
            a structured INFO (`tts.alias_overridden_by_language`) so
            the override is observable.
         c. Honors backward-compat for English-only callers; protects
            every other caller from the "speaking-letter-by-letter"
            failure mode.
      3. Anything else → pass through as a literal Piper voice name.
    """
    if name in (None, "", "auto"):
        lang = _detect_language(text)
        if lang and lang in LANG_DEFAULT_VOICE:
            chosen = LANG_DEFAULT_VOICE[lang]
            logger.info("[TTS] auto-lang detected=%s voice=%s text_len=%d", lang, chosen, len(text))
            return chosen
        logger.warning(
            "[TTS] tts.lang_detection.unmapped detected=%s text_len=%d "
            "— falling back to English default (#NEW-tts-lang-fallback)",
            lang, len(text),
        )
        return LANG_DEFAULT_VOICE["en"]

    if name in VOICE_ALIASES:
        # Alias = backward-compat OpenAI voice name. They all map to
        # English Piper voices. If the text is non-Latin, English voice
        # would mispronounce letter-by-letter — override.
        has_non_latin = any(ord(ch) >= 0x80 for ch in text)
        if has_non_latin:
            lang = _detect_language(text)
            if lang and lang != "en" and lang in LANG_DEFAULT_VOICE:
                chosen = LANG_DEFAULT_VOICE[lang]
                logger.info(
                    "[TTS] tts.alias_overridden_by_language alias=%s detected=%s "
                    "voice=%s text_len=%d — alias would emit letter-by-letter on "
                    "non-Latin input",
                    name, lang, chosen, len(text),
                )
                return chosen
        return VOICE_ALIASES[name]

    # Pass-through literal Piper voice name (e.g. en_US-amy-medium).
    return name


def _ensure_voice(voice_name: str) -> "PiperVoice":  # type: ignore[name-defined]
    """Return a loaded PiperVoice, downloading the model if needed."""
    from piper import PiperVoice

    if voice_name in _loaded_voices:
        return _loaded_voices[voice_name]

    model_path, config_path = _voice_model_paths(voice_name)
    _download_voice_model(voice_name)

    logger.info("[TTS] Loading voice: %s", voice_name)
    voice = PiperVoice.load(str(model_path), str(config_path))
    _loaded_voices[voice_name] = voice
    logger.info(
        "[TTS] Voice loaded: %s (sample_rate=%d)",
        voice_name,
        voice.config.sample_rate,
    )
    return voice


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    VOICES_DIR.mkdir(parents=True, exist_ok=True)

    # Prepare default voices at startup. Release deployments should fail
    # readiness when a promised voice model cannot be made local; only a small
    # hot subset is loaded into RAM to keep Helm memory predictable.
    for v in DEFAULT_VOICES:
        v = v.strip()
        if v:
            try:
                _download_voice_model(v)
                if v in DEFAULT_LOADED_VOICES:
                    _ensure_voice(v)
            except Exception:
                logger.warning("[TTS] Could not prepare voice: %s", v, exc_info=True)
                if PIPER_PRELOAD_STRICT:
                    raise

    yield


_VEXA_ENV = os.getenv("VEXA_ENV", "development")
_PUBLIC_DOCS = _VEXA_ENV != "production"
app = FastAPI(
    title="Vexa TTS Service",
    description="Local text-to-speech synthesis using Piper TTS",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _PUBLIC_DOCS else None,
    redoc_url="/redoc" if _PUBLIC_DOCS else None,
    openapi_url="/openapi.json" if _PUBLIC_DOCS else None,
)


# ---------------------------------------------------------------------------
# Auth (optional)
# ---------------------------------------------------------------------------


async def verify_api_key(api_key: str = Depends(API_KEY_HEADER)):
    """Optional API key validation — if TTS_API_TOKEN is set, require it."""
    token = os.getenv("TTS_API_TOKEN", "").strip()
    if token and api_key != token:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "tts-service",
        "provider": "piper",
        "output_sample_rate": OUTPUT_SAMPLE_RATE,
        "configured_default_voices": DEFAULT_VOICES,
        "default_loaded_voices": DEFAULT_LOADED_VOICES,
        "preload_strict": PIPER_PRELOAD_STRICT,
        "prepared_voices": sorted(
            p.name[:-5]
            for p in VOICES_DIR.glob("*.onnx")
            if not p.name.endswith(".onnx.json")
        ),
        "loaded_voices": list(_loaded_voices.keys()),
        "voice_aliases": VOICE_ALIASES,
    }


@app.get("/voices")
async def list_voices():
    """List available (already downloaded) voices and known aliases."""
    downloaded = []
    if VOICES_DIR.exists():
        downloaded = sorted(
            p.stem.replace(".onnx", "")
            for p in VOICES_DIR.glob("*.onnx")
            if not p.name.endswith(".onnx.json")
        )
    return {
        "downloaded": downloaded,
        "loaded": list(_loaded_voices.keys()),
        "aliases": VOICE_ALIASES,
    }


@app.post("/v1/audio/speech")
async def speech(
    request: Request,
    _: str = Depends(verify_api_key),
):
    """
    Synthesize text to speech. OpenAI-compatible API.

    Request body: {"input": "text", "voice": "alloy", "response_format": "wav"}
    - voice: Piper voice name (e.g. "en_US-amy-medium") or alias ("alloy", "nova", etc.)
    - response_format: "wav" (default) or "pcm" (raw Int16LE)
    - model: ignored (kept for OpenAI API compatibility)

    Returns: audio in requested format
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}") from e

    text = body.get("input", "")
    voice_param = body.get("voice", "alloy")
    response_format = body.get("response_format", "wav")

    if not text:
        raise HTTPException(status_code=400, detail="'input' (text) is required")

    if not text.strip():
        raise HTTPException(status_code=400, detail="'input' text is empty")

    voice_name = _resolve_voice_name(voice_param, text=text)

    logger.info(
        "[TTS] Synthesizing: voice_param=%s resolved=%s format=%s len=%d",
        voice_param,
        voice_name,
        response_format,
        len(text),
    )

    try:
        voice = _ensure_voice(voice_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to load voice '{voice_name}': {exc}"
        ) from exc

    src_rate = voice.config.sample_rate
    dst_rate = OUTPUT_SAMPLE_RATE

    # Synthesize
    try:
        if response_format == "pcm":
            # Raw Int16LE PCM — collect, resample, stream
            def pcm_stream():
                for chunk in voice.synthesize(text):
                    raw = chunk.audio_int16_bytes
                    yield _resample_int16(raw, src_rate, dst_rate)

            return StreamingResponse(
                pcm_stream(),
                media_type="audio/pcm",
                headers={
                    "Content-Disposition": "inline; filename=speech.pcm",
                    "X-Sample-Rate": str(dst_rate),
                    "X-Sample-Width": "2",
                    "X-Channels": "1",
                    "X-Vexa-TTS-Voice": voice_name,
                    "X-Vexa-TTS-Voice-Param": str(voice_param),
                },
            )
        else:
            # Default: WAV — synthesize, resample, write WAV at target rate
            pcm_data = b""
            for chunk in voice.synthesize(text):
                pcm_data += chunk.audio_int16_bytes

            pcm_data = _resample_int16(pcm_data, src_rate, dst_rate)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                wav_file.setframerate(dst_rate)
                wav_file.setsampwidth(2)
                wav_file.setnchannels(1)
                wav_file.writeframes(pcm_data)

            wav_bytes = buf.getvalue()

            return Response(
                content=wav_bytes,
                media_type="audio/wav",
                headers={
                    "Content-Disposition": "inline; filename=speech.wav",
                    "Content-Length": str(len(wav_bytes)),
                    "X-Vexa-TTS-Voice": voice_name,
                    "X-Vexa-TTS-Voice-Param": str(voice_param),
                },
            )
    except Exception as exc:
        logger.error("[TTS] Synthesis failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Synthesis failed: {exc}"
        ) from exc
