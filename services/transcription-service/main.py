"""
Vexa-Compatible Transcription Service
Supports AWS Transcribe (default) or local Whisper. Implements OpenAI Whisper API format for integration.
"""
import asyncio
import io
import json
import logging
import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Backend: "aws" (Amazon Transcribe) or "whisper" (local faster-whisper)
TRANSCRIPTION_BACKEND = os.getenv("TRANSCRIPTION_BACKEND", "aws").strip().lower()
if TRANSCRIPTION_BACKEND not in ("aws", "whisper"):
    TRANSCRIPTION_BACKEND = "aws"

# AWS Transcribe (used when TRANSCRIPTION_BACKEND=aws)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_TRANSCRIPTION_INPUT_BUCKET = os.getenv("AWS_TRANSCRIPTION_INPUT_BUCKET", "").strip()
AWS_TRANSCRIPTION_OUTPUT_BUCKET = os.getenv("AWS_TRANSCRIPTION_OUTPUT_BUCKET", "").strip() or AWS_TRANSCRIPTION_INPUT_BUCKET

# Configuration (used when TRANSCRIPTION_BACKEND=whisper)
WORKER_ID = os.getenv("WORKER_ID", "1")
MODEL_SIZE = os.getenv("MODEL_SIZE", "large-v3-turbo")

# Device detection: Use environment variable or default to cuda for GPU containers
DEVICE = os.getenv("DEVICE", "cuda")

# Compute type optimization: Use INT8 for optimal VRAM efficiency
# Research shows: large-v3-turbo + INT8 = ~2.1 GB VRAM (validated)
# Provides 50-60% VRAM reduction with minimal accuracy loss (~1-2% WER increase)
COMPUTE_TYPE_ENV = os.getenv("COMPUTE_TYPE", "").strip().lower()
if COMPUTE_TYPE_ENV:
    COMPUTE_TYPE = COMPUTE_TYPE_ENV
else:
    # Default to INT8 for both GPU and CPU (optimal balance of speed, memory, and accuracy)
    COMPUTE_TYPE = "int8"

# CPU threads configuration (for CPU mode optimization)
CPU_THREADS = int(os.getenv("CPU_THREADS", "0"))  # 0 = auto-detect

# Quality / decoding parameters (optional)
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, None)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, None)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.debug(f"Invalid int env {name}={raw!r}, using default {default}")
        return default

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, None)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.debug(f"Invalid float env {name}={raw!r}, using default {default}")
        return default

# WhisperLive-inspired defaults (can be overridden via env)
BEAM_SIZE = _env_int("BEAM_SIZE", 5)
BEST_OF = _env_int("BEST_OF", 5)
COMPRESSION_RATIO_THRESHOLD = _env_float("COMPRESSION_RATIO_THRESHOLD", 2.4)
LOG_PROB_THRESHOLD = _env_float("LOG_PROB_THRESHOLD", -1.0)
NO_SPEECH_THRESHOLD = _env_float("NO_SPEECH_THRESHOLD", 0.6)
CONDITION_ON_PREVIOUS_TEXT = _env_bool("CONDITION_ON_PREVIOUS_TEXT", True)
PROMPT_RESET_ON_TEMPERATURE = _env_float("PROMPT_RESET_ON_TEMPERATURE", 0.5)

# VAD parameters
VAD_FILTER = _env_bool("VAD_FILTER", True)
VAD_FILTER_THRESHOLD = _env_float("VAD_FILTER_THRESHOLD", 0.5)
VAD_MIN_SILENCE_DURATION_MS = _env_int("VAD_MIN_SILENCE_DURATION_MS", 160)

# Temperature fallback chain
USE_TEMPERATURE_FALLBACK = _env_bool("USE_TEMPERATURE_FALLBACK", False)
TEMPERATURE_FALLBACK_CHAIN = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

def _looks_like_silence(segments: List[Dict[str, Any]]) -> bool:
    """Heuristic: treat as silence if all segments look like no-speech."""
    if not segments:
        return True
    for s in segments:
        if not (
            float(s.get("no_speech_prob", 0.0)) > NO_SPEECH_THRESHOLD
            and float(s.get("avg_logprob", 0.0)) < LOG_PROB_THRESHOLD
        ):
            return False
    return True

def _looks_like_hallucination(segments: List[Dict[str, Any]]) -> bool:
    """Heuristic: reject segments that look like hallucinations / low-confidence."""
    for s in segments:
        if float(s.get("compression_ratio", 0.0)) > COMPRESSION_RATIO_THRESHOLD:
            return True
        if float(s.get("avg_logprob", 0.0)) < LOG_PROB_THRESHOLD:
            return True
    return False

# API Token Authentication
API_TOKEN = os.getenv("API_TOKEN", "").strip()
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_token(
    request: Request,
    api_key: Optional[str] = Depends(API_KEY_HEADER)
) -> bool:
    """Verify API token - supports both X-API-Key and Authorization Bearer"""
    if not API_TOKEN:
        # If no token configured, allow all requests (backward compatibility)
        logger.debug("API_TOKEN not configured - allowing all requests")
        return True
    
    # Try X-API-Key header first
    if api_key and api_key == API_TOKEN:
        return True
    
    # Try Authorization Bearer header (for compatibility)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
        if token == API_TOKEN:
            return True
    
    logger.debug(f"Invalid or missing API token - X-API-Key: {api_key is not None}, Authorization: {bool(auth_header)}")
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API token"
    )

app = FastAPI(
    title="Vexa Transcription Service",
    description="Transcription API (AWS Transcribe or Whisper). OpenAI Whisper API compatible.",
    version="1.0.0"
)

# Global Whisper model instance (only used when TRANSCRIPTION_BACKEND=whisper)
model = None  # WhisperModel | None

# Load management: Global concurrency limit and bounded queue
# These settings control how many transcription requests can be processed concurrently
MAX_CONCURRENT_TRANSCRIPTIONS = _env_int("MAX_CONCURRENT_TRANSCRIPTIONS", 2)  # Max concurrent model calls
MAX_QUEUE_SIZE = _env_int("MAX_QUEUE_SIZE", 10)  # Max requests waiting in queue

# Backpressure strategy:
# - If FAIL_FAST_WHEN_BUSY=true, we do NOT wait in a queue; we immediately return 503 so callers
#   (e.g. WhisperLive) can keep buffering and submit a newer/larger window later.
FAIL_FAST_WHEN_BUSY = _env_bool("FAIL_FAST_WHEN_BUSY", True)
BUSY_RETRY_AFTER_S = _env_int("BUSY_RETRY_AFTER_S", 1)

# Semaphore to limit concurrent transcriptions (protects GPU/CPU from overload)
transcription_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTIONS)

# Thread pool for running blocking transcription calls
transcription_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TRANSCRIPTIONS)

# Queue to track waiting requests (for 429/503 responses when full)
# We use a simple counter since FastAPI doesn't have a built-in queue
waiting_requests = 0
waiting_requests_lock = asyncio.Lock()


def _language_to_aws(lang: Optional[str]) -> Optional[str]:
    """Convert optional language to AWS LanguageCode (e.g. en -> en-US)."""
    if not lang or not lang.strip():
        return None
    lang = lang.strip()
    if len(lang) == 2:
        return f"{lang}-{lang.upper()}"
    return lang if "-" in lang else f"{lang}-{lang.upper()}"


def _aws_transcript_to_segments(aws_result: Dict[str, Any]) -> Tuple[str, str, float, List[Dict[str, Any]]]:
    """Map AWS Transcribe transcript JSON to (full_text, language, duration, segments)."""
    results = aws_result.get("results", {})
    transcripts = results.get("transcripts", [])
    full_text = transcripts[0].get("transcript", "").strip() if transcripts else ""
    items = results.get("items", [])
    # Compute duration from last item
    duration = 0.0
    for item in items:
        if item.get("type") == "pronunciation":
            duration = max(duration, float(item.get("end_time", 0)))
    if not full_text:
        return "", "en", duration, []
    # Single segment for full text (OpenAI format compatible)
    segments: List[Dict[str, Any]] = [{
        "id": 0,
        "seek": 0,
        "start": 0.0,
        "end": duration,
        "text": full_text,
        "tokens": [],
        "temperature": 0.0,
        "avg_logprob": None,
        "compression_ratio": None,
        "no_speech_prob": None,
        "audio_start": 0.0,
        "audio_end": duration,
    }]
    return full_text, "en", duration, segments


async def _transcribe_with_aws(
    audio_bytes: bytes,
    content_type: str,
    language: Optional[str],
    task: str,
) -> Tuple[str, str, float, List[Dict[str, Any]]]:
    """Upload audio to S3, run AWS Transcribe job, poll and return (full_text, language, duration, segments)."""
    import boto3
    job_id = f"vexa-{uuid.uuid4().hex[:12]}"
    # Determine format from content type or default wav
    if "mp3" in (content_type or ""):
        ext, media_format = "mp3", "mp3"
    elif "flac" in (content_type or ""):
        ext, media_format = "flac", "flac"
    else:
        ext, media_format = "wav", "wav"
    key = f"transcription-input/{job_id}.{ext}"
    bucket = AWS_TRANSCRIPTION_INPUT_BUCKET
    if not bucket:
        raise HTTPException(status_code=503, detail="AWS_TRANSCRIPTION_INPUT_BUCKET not configured")
    s3 = boto3.client("s3", region_name=AWS_REGION)
    transcribe = boto3.client("transcribe", region_name=AWS_REGION)
    await asyncio.get_event_loop().run_in_executor(
        transcription_executor,
        lambda: s3.put_object(Bucket=bucket, Key=key, Body=audio_bytes, ContentType=content_type or f"audio/{ext}"),
    )
    media_uri = f"s3://{bucket}/{key}"
    job_name = f"vexa-{job_id}"
    await asyncio.get_event_loop().run_in_executor(
        transcription_executor,
        lambda: transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode=_language_to_aws(language) or "en-US",
            MediaFormat=media_format,
            Media={"MediaFileUri": media_uri},
            OutputBucketName=AWS_TRANSCRIPTION_OUTPUT_BUCKET or bucket,
        ),
    )
    # Poll until complete (with timeout)
    for _ in range(120):
        await asyncio.sleep(2)
        job = await asyncio.get_event_loop().run_in_executor(
            transcription_executor,
            lambda: transcribe.get_transcription_job(TranscriptionJobName=job_name),
        )
        status = job["TranscriptionJob"]["TranscriptionJobStatus"]
        if status == "COMPLETED":
            transcript_uri = job["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
            # transcript_uri is https://s3.../...; fetch JSON
            import urllib.request
            raw = await asyncio.get_event_loop().run_in_executor(
                transcription_executor,
                lambda: urllib.request.urlopen(transcript_uri).read(),
            )
            aws_result = json.loads(raw.decode("utf-8"))
            try:
                await asyncio.get_event_loop().run_in_executor(
                    transcription_executor,
                    lambda: s3.delete_object(Bucket=bucket, Key=key),
                )
            except Exception:
                pass
            return _aws_transcript_to_segments(aws_result)
        if status == "FAILED":
            reason = job["TranscriptionJob"].get("FailureReason", "Unknown")
            try:
                s3.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"AWS Transcribe job failed: {reason}")
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass
    raise HTTPException(status_code=504, detail="AWS Transcribe job timed out")


@app.on_event("startup")
async def startup_event():
    """Initialize backend: AWS (no model) or Whisper model."""
    global model
    logger.debug("Transcription service starting with backend=%s", TRANSCRIPTION_BACKEND)
    if TRANSCRIPTION_BACKEND == "aws":
        logger.debug("Using AWS Transcribe (region=%s, input_bucket=%s)", AWS_REGION, AWS_TRANSCRIPTION_INPUT_BUCKET or "(not set)")
        if not AWS_TRANSCRIPTION_INPUT_BUCKET:
            logger.debug("AWS_TRANSCRIPTION_INPUT_BUCKET is not set; /v1/audio/transcriptions will return 503 until configured")
        return
    # Whisper backend
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.debug("TRANSCRIPTION_BACKEND=whisper but faster-whisper not installed. pip install faster-whisper")
        raise
    logger.debug("Worker %s loading Whisper - Device: %s, Model: %s, Compute: %s", WORKER_ID, DEVICE, MODEL_SIZE, COMPUTE_TYPE)
    try:
        model_kwargs = {
            "model_size_or_path": MODEL_SIZE,
            "device": DEVICE,
            "compute_type": COMPUTE_TYPE,
            "download_root": "/app/models"
        }
        if DEVICE == "cpu" and CPU_THREADS > 0:
            model_kwargs["cpu_threads"] = CPU_THREADS
        model = WhisperModel(**model_kwargs)
        logger.debug("Worker %s ready - Whisper model loaded", WORKER_ID)
    except Exception as e:
        logger.debug("Failed to load Whisper model: %s", e)
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancer"""
    healthy = (TRANSCRIPTION_BACKEND == "aws") or (model is not None)
    health_status = {
        "status": "healthy" if healthy else "unhealthy",
        "backend": TRANSCRIPTION_BACKEND,
        "worker_id": WORKER_ID,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if TRANSCRIPTION_BACKEND == "whisper":
        health_status["model"] = MODEL_SIZE
        health_status["device"] = DEVICE
        health_status["gpu_available"] = DEVICE == "cuda"
        if DEVICE == "cuda":
            health_status["compute_type"] = COMPUTE_TYPE
    else:
        health_status["aws_region"] = AWS_REGION
        health_status["aws_bucket_configured"] = bool(AWS_TRANSCRIPTION_INPUT_BUCKET)
    if not healthy:
        return JSONResponse(content=health_status, status_code=503)
    return health_status


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    requested_model: str = Form(..., alias="model"),
    temperature: str = Form("0"),
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: str = Form("verbose_json"),
    timestamp_granularities: str = Form("segment"),
    task: str = Form("transcribe"),
    _: bool = Depends(verify_api_token)
):
    """
    Transcription endpoint (AWS Transcribe or Whisper).
    OpenAI Whisper API compatible.
    """
    if not requested_model:
        raise HTTPException(status_code=400, detail="Model parameter is required")
    if TRANSCRIPTION_BACKEND == "aws" and not AWS_TRANSCRIPTION_INPUT_BUCKET:
        raise HTTPException(status_code=503, detail="AWS Transcribe not configured (set AWS_TRANSCRIPTION_INPUT_BUCKET)")
    if TRANSCRIPTION_BACKEND == "whisper" and model is None:
        raise HTTPException(status_code=503, detail="Whisper model not loaded")
    # Load management: Check queue size before accepting request
    async with waiting_requests_lock:
        global waiting_requests
        # Fail-fast mode: don't accept work we can't start immediately.
        # This avoids "processing the first chunk" (small/old) and lets upstream buffer/coalesce.
        if FAIL_FAST_WHEN_BUSY and (transcription_semaphore.locked() or waiting_requests > 0):
            raise HTTPException(
                status_code=503,
                detail="Service busy. Please retry later.",
                headers={"Retry-After": str(max(1, BUSY_RETRY_AFTER_S))},
            )
        if waiting_requests >= MAX_QUEUE_SIZE:
            logger.debug(
                f"Worker {WORKER_ID} queue full ({waiting_requests}/{MAX_QUEUE_SIZE}). "
                f"Rejecting request with 503."
            )
            raise HTTPException(
                status_code=503,
                detail="Service temporarily overloaded. Please retry later.",
                headers={"Retry-After": str(max(1, BUSY_RETRY_AFTER_S))}
            )
        waiting_requests += 1
    
    try:
        # Acquire semaphore (blocks if MAX_CONCURRENT_TRANSCRIPTIONS is reached)
        await transcription_semaphore.acquire()
        
        async with waiting_requests_lock:
            waiting_requests -= 1
        
        start_time = time.time()
        logger.debug("Transcription request - filename: %s, content_type: %s, backend: %s", file.filename, file.content_type, TRANSCRIPTION_BACKEND)
        audio_bytes = await file.read()
        logger.debug("Read %s bytes of audio", len(audio_bytes))

        if TRANSCRIPTION_BACKEND == "aws":
            full_text, detected_language, duration, segments = await _transcribe_with_aws(
                audio_bytes, file.content_type or "", language, task
            )
        else:
            # Whisper path
            audio_io = io.BytesIO(audio_bytes)
            try:
                audio_array, sample_rate = sf.read(audio_io, dtype=np.float32)
            except Exception as e:
                logger.debug("Failed to decode audio: %s", e)
                raise HTTPException(status_code=400, detail=f"Failed to decode audio file: {e}")
            if len(audio_array.shape) > 1:
                audio_array = np.mean(audio_array, axis=1)
            audio_array = np.ascontiguousarray(audio_array, dtype=np.float32)
            requested_temp = float(temperature) if temperature else 0.0
            temps = TEMPERATURE_FALLBACK_CHAIN if USE_TEMPERATURE_FALLBACK else [requested_temp]
            best: Optional[Tuple[str, str, float, List[Dict[str, Any]]]] = None
            last_info = None
            last_segments: List[Dict[str, Any]] = []
            for t in temps:
                def _transcribe_sync():
                    return model.transcribe(
                        audio_array,
                        language=language,
                        task=task,
                        initial_prompt=prompt,
                        temperature=t,
                        beam_size=BEAM_SIZE,
                        best_of=BEST_OF,
                        compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
                        log_prob_threshold=LOG_PROB_THRESHOLD,
                        no_speech_threshold=NO_SPEECH_THRESHOLD,
                        condition_on_previous_text=CONDITION_ON_PREVIOUS_TEXT,
                        prompt_reset_on_temperature=PROMPT_RESET_ON_TEMPERATURE,
                        vad_filter=VAD_FILTER,
                        vad_parameters={"threshold": VAD_FILTER_THRESHOLD, "min_silence_duration_ms": VAD_MIN_SILENCE_DURATION_MS},
                        word_timestamps=False,
                    )
                segments_list, info = await asyncio.get_event_loop().run_in_executor(transcription_executor, _transcribe_sync)
                last_info = info
                segments = []
                for idx, segment in enumerate(segments_list):
                    segments.append({
                        "id": idx, "seek": 0, "start": segment.start, "end": segment.end, "text": segment.text,
                        "tokens": [], "temperature": t, "avg_logprob": segment.avg_logprob,
                        "compression_ratio": segment.compression_ratio, "no_speech_prob": segment.no_speech_prob,
                        "audio_start": segment.start, "audio_end": segment.end,
                    })
                last_segments = segments
                if _looks_like_silence(segments):
                    best = ("", info.language, 0.0, [])
                    break
                if not _looks_like_hallucination(segments):
                    full_text = " ".join([s["text"].strip() for s in segments]).strip()
                    duration = segments[-1]["end"] if segments else 0.0
                    best = (full_text, info.language, duration, segments)
                    break
            if best is None:
                info = last_info
                segments = last_segments
                full_text = " ".join([s["text"].strip() for s in segments]).strip()
                duration = segments[-1]["end"] if segments else 0.0
                best = (full_text, info.language if info else (language or "unknown"), duration, segments)
            full_text, detected_language, duration, segments = best

        processing_time = time.time() - start_time
        logger.debug("Transcription completed in %.2fs - language: %s, segments: %s", processing_time, detected_language, len(segments))
        response = {"text": full_text, "language": detected_language, "duration": duration, "segments": segments}
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions (429, 503, etc.)
        raise
    except Exception as e:
        logger.debug(f"Worker {WORKER_ID} transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Always release semaphore, even on error
        transcription_semaphore.release()


@app.get("/")
async def root():
    """Root endpoint with service info"""
    return {
        "service": "Vexa Transcription Service",
        "backend": TRANSCRIPTION_BACKEND,
        "worker_id": WORKER_ID,
        "status": "ready" if (TRANSCRIPTION_BACKEND == "aws" or model is not None) else "initializing",
        "model": MODEL_SIZE if TRANSCRIPTION_BACKEND == "whisper" else None,
        "device": DEVICE if TRANSCRIPTION_BACKEND == "whisper" else None,
        "endpoints": {"transcribe": "/v1/audio/transcriptions", "health": "/health"},
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )

