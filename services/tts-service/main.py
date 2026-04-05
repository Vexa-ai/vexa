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

# Default voices to pre-load on startup (empty = lazy load all)
DEFAULT_VOICES = os.getenv(
    "PIPER_DEFAULT_VOICES",
    "en_US-amy-medium,en_US-danny-low",
).split(",")

# Map OpenAI voice names to Piper voice names for backward compatibility
VOICE_ALIASES: dict[str, str] = {
    "alloy": "en_US-amy-medium",
    "echo": "en_US-danny-low",
    "fable": "en_US-joe-medium",
    "onyx": "en_US-ryan-medium",
    "nova": "en_US-kristin-medium",
    "shimmer": "en_US-lessac-medium",
}

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


def _resolve_voice_name(name: str) -> str:
    """Resolve an alias (e.g. 'alloy') or pass through a Piper voice name."""
    return VOICE_ALIASES.get(name, name)


def _ensure_voice(voice_name: str) -> "PiperVoice":  # type: ignore[name-defined]
    """Return a loaded PiperVoice, downloading the model if needed."""
    from piper import PiperVoice
    from piper.download_voices import download_voice

    if voice_name in _loaded_voices:
        return _loaded_voices[voice_name]

    model_path = VOICES_DIR / f"{voice_name}.onnx"
    config_path = VOICES_DIR / f"{voice_name}.onnx.json"

    if not model_path.exists() or not config_path.exists():
        logger.info("[TTS] Downloading voice: %s -> %s", voice_name, VOICES_DIR)
        try:
            download_voice(voice_name, VOICES_DIR)
        except Exception as exc:
            logger.error("[TTS] Failed to download voice %s: %s", voice_name, exc)
            raise HTTPException(
                status_code=404,
                detail=f"Voice '{voice_name}' not found and could not be downloaded: {exc}",
            ) from exc

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

    # Pre-load default voices at startup
    for v in DEFAULT_VOICES:
        v = v.strip()
        if v:
            try:
                _ensure_voice(v)
            except Exception:
                logger.warning("[TTS] Could not pre-load voice: %s", v, exc_info=True)

    yield


app = FastAPI(
    title="Vexa TTS Service",
    description="Local text-to-speech synthesis using Piper TTS",
    version="2.0.0",
    lifespan=lifespan,
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

    voice_name = _resolve_voice_name(voice_param)

    logger.info(
        "[TTS] Synthesizing: voice=%s (%s), format=%s, len=%d",
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
                },
            )
    except Exception as exc:
        logger.error("[TTS] Synthesis failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Synthesis failed: {exc}"
        ) from exc
