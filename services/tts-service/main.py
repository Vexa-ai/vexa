"""
Vexa TTS Service

Text-to-speech service using OpenAI TTS API.
Exposes OpenAI-compatible /v1/audio/speech endpoint for use by the vexa-bot.
"""
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set - TTS synthesis will fail")
    yield


app = FastAPI(
    title="Vexa TTS Service",
    description="Text-to-speech synthesis for Vexa voice agent",
    version="1.0.0",
    lifespan=lifespan,
)


async def verify_api_key(api_key: str = Depends(API_KEY_HEADER)):
    """Optional API key validation - if TTS_API_TOKEN is set, require it."""
    token = os.getenv("TTS_API_TOKEN", "").strip()
    if token and api_key != token:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


@app.get("/health")
async def health():
    return {"status": "ok", "service": "tts-service"}


@app.post("/v1/audio/speech")
async def speech(
    request: Request,
    _: str = Depends(verify_api_key),
):
    """
    Synthesize text to speech. OpenAI-compatible API.
    Request body: {"model": "tts-1", "input": "text", "voice": "nova", "response_format": "pcm"}
    Returns: raw PCM audio (Int16LE, 24kHz, mono)
    """
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not configured",
        )

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}") from e

    model = body.get("model", "tts-1")
    text = body.get("input", "")
    voice = body.get("voice", "alloy")
    response_format = body.get("response_format", "pcm")

    if not text:
        raise HTTPException(status_code=400, detail="'input' (text) is required")

    valid_voices = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
    if voice not in valid_voices:
        voice = "alloy"

    if response_format not in ("pcm", "mp3", "opus", "aac", "wav", "flac"):
        response_format = "pcm"

    logger.info(f"[TTS] Synthesizing: model={model}, voice={voice}, len={len(text)}")

    url = f"{OPENAI_BASE_URL.rstrip('/')}/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }

    async def stream():
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    err_body = await resp.aread()
                    raise HTTPException(
                        status_code=502,
                        detail=f"OpenAI TTS error {resp.status_code}: {err_body.decode()[:200]}",
                    )
                async for chunk in resp.aiter_bytes(chunk_size=8192):
                    yield chunk

    media_type = "application/octet-stream"
    if response_format == "pcm":
        media_type = "audio/pcm"
    elif response_format == "mp3":
        media_type = "audio/mpeg"

    return StreamingResponse(
        stream(),
        media_type=media_type,
        headers={"Content-Disposition": "inline; filename=speech.audio"},
    )
