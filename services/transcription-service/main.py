"""
Vexa-Compatible Transcription Service (PoC)
Implements OpenAI Whisper API format for seamless integration with Vexa
"""
import os
import io
import time
import logging
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
import uvicorn
from faster_whisper import WhisperModel
# faster-whisper uses CTranslate2 internally (no PyTorch needed)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
WORKER_ID = os.getenv("WORKER_ID", "1")
MODEL_SIZE = os.getenv("MODEL_SIZE", "large-v3-turbo")

# Device detection: Use environment variable or default to cuda for GPU containers
# CTranslate2 (used by faster-whisper) will automatically detect and use CUDA if available
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
        logger.warning(f"Invalid int env {name}={raw!r}, using default {default}")
        return default

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, None)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float env {name}={raw!r}, using default {default}")
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
        logger.warning("API_TOKEN not configured - allowing all requests")
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
    
    logger.warning(f"Invalid or missing API token - X-API-Key: {api_key is not None}, Authorization: {bool(auth_header)}")
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API token"
    )

app = FastAPI(
    title="Vexa Transcription Service",
    description="OpenAI Whisper API compatible transcription service",
    version="1.0.0"
)

# Global model instance
model: Optional[WhisperModel] = None

# Load management: Global concurrency limit and bounded queue
# These settings control how many transcription requests can be processed concurrently.
# MAX_ACTIVE_REQUESTS is the preferred name; MAX_CONCURRENT_TRANSCRIPTIONS is kept for compatibility.
MAX_CONCURRENT_TRANSCRIPTIONS = _env_int("MAX_ACTIVE_REQUESTS", _env_int("MAX_CONCURRENT_TRANSCRIPTIONS", 2))
MAX_QUEUE_SIZE = _env_int("MAX_QUEUE_SIZE", 10)  # Max requests waiting in queue

# Backpressure strategy:
# - If FAIL_FAST_WHEN_BUSY=true, we do NOT wait in a queue; we immediately return 503 so callers
#   (e.g. WhisperLive) can keep buffering and submit a newer/larger window later.
FAIL_FAST_WHEN_BUSY = _env_bool("FAIL_FAST_WHEN_BUSY", True)
BUSY_RETRY_AFTER_S = _env_int("BUSY_RETRY_AFTER_S", 1)
REALTIME_RESERVED_SLOTS = _env_int("REALTIME_RESERVED_SLOTS", 1)

# Semaphore to limit concurrent transcriptions (protects GPU/CPU from overload)
transcription_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTIONS)

# Thread pool for running blocking transcription calls
transcription_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TRANSCRIPTIONS)

# Queue to track waiting requests (for 429/503 responses when full)
# We use a simple counter since FastAPI doesn't have a built-in queue
waiting_requests = 0
waiting_requests_lock = asyncio.Lock()

# Active in-flight counters per tier for admission decisions.
active_realtime_requests = 0
active_deferred_requests = 0
active_requests_lock = asyncio.Lock()


def _normalize_transcription_tier(raw: Optional[str]) -> str:
    tier = (raw or "realtime").strip().lower()
    return tier if tier in ("realtime", "deferred") else "realtime"


def _deferred_capacity_available(active_rt: int, active_df: int) -> bool:
    deferred_limit = max(0, MAX_CONCURRENT_TRANSCRIPTIONS - REALTIME_RESERVED_SLOTS)
    total_active = active_rt + active_df
    return deferred_limit > 0 and active_df < deferred_limit and total_active < MAX_CONCURRENT_TRANSCRIPTIONS


@app.on_event("startup")
async def startup_event():
    """Initialize Whisper model on startup"""
    global model
    logger.info(f"Worker {WORKER_ID} starting up...")
    logger.info(f"Device: {DEVICE}, Model: {MODEL_SIZE}, Compute: {COMPUTE_TYPE}")
    logger.info(
        "Quality params - "
        f"beam_size={BEAM_SIZE}, best_of={BEST_OF}, "
        f"cond_prev_text={CONDITION_ON_PREVIOUS_TEXT}, "
        f"compression_ratio_threshold={COMPRESSION_RATIO_THRESHOLD}, "
        f"log_prob_threshold={LOG_PROB_THRESHOLD}, "
        f"no_speech_threshold={NO_SPEECH_THRESHOLD}, "
        f"vad_filter={VAD_FILTER}"
    )
    
    try:
        # Build model initialization parameters
        model_kwargs = {
            "model_size_or_path": MODEL_SIZE,
            "device": DEVICE,
            "compute_type": COMPUTE_TYPE,
            "download_root": "/app/models"
        }
        
        # Add CPU threads for CPU mode (optimization from research)
        if DEVICE == "cpu" and CPU_THREADS > 0:
            model_kwargs["cpu_threads"] = CPU_THREADS
            logger.info(f"Worker {WORKER_ID} using {CPU_THREADS} CPU threads")
        
        model = WhisperModel(**model_kwargs)
        logger.info(f"Worker {WORKER_ID} ready - Model loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise


@app.get("/health")
async def health_check():
    """Health check endpoint for load balancer"""
    health_status = {
        "status": "healthy" if model is not None else "unhealthy",
        "worker_id": WORKER_ID,
        "timestamp": datetime.utcnow().isoformat(),
        "model": MODEL_SIZE,
        "device": DEVICE,
        "gpu_available": DEVICE == "cuda",
    }
    
    if DEVICE == "cuda":
        # CTranslate2 (via faster-whisper) handles GPU automatically
        health_status["compute_type"] = COMPUTE_TYPE
    
    if model is None:
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
    transcription_tier_form: Optional[str] = Form(None, alias="transcription_tier"),
    task: str = Form("transcribe"),
    _: bool = Depends(verify_api_token)
):
    """
    OpenAI Whisper API compatible transcription endpoint
    
    Required by Vexa's RemoteTranscriber:
    - Accepts multipart/form-data with audio file
    - Returns verbose_json format with segments
    - Includes timing, language, and segment details
    
    Load management:
    - Limits concurrent transcriptions to prevent GPU/CPU overload
    - Returns 429/503 when queue is full to signal backpressure
    """
    if not requested_model:
        raise HTTPException(status_code=400, detail="Model parameter is required")
    global waiting_requests, active_realtime_requests, active_deferred_requests

    tier_from_header = request.headers.get("X-Transcription-Tier")
    transcription_tier = _normalize_transcription_tier(transcription_tier_form or tier_from_header)

    semaphore_acquired = False
    waiting_counted = False
    active_counted = False
    
    # Load management: Check queue size before accepting request
    async with waiting_requests_lock:
        async with active_requests_lock:
            current_active_rt = active_realtime_requests
            current_active_df = active_deferred_requests

        if transcription_tier == "deferred":
            if not _deferred_capacity_available(current_active_rt, current_active_df):
                raise HTTPException(
                    status_code=503,
                    detail="Deferred tier is out of capacity. Please retry later.",
                    headers={"Retry-After": str(max(1, BUSY_RETRY_AFTER_S))},
                )
        # Fail-fast mode: don't accept work we can't start immediately.
        # This avoids "processing the first chunk" (small/old) and lets upstream buffer/coalesce.
        if FAIL_FAST_WHEN_BUSY and (transcription_semaphore.locked() or waiting_requests > 0):
            raise HTTPException(
                status_code=503,
                detail="Service busy. Please retry later.",
                headers={"Retry-After": str(max(1, BUSY_RETRY_AFTER_S))},
            )
        if waiting_requests >= MAX_QUEUE_SIZE:
            logger.warning(
                f"Worker {WORKER_ID} queue full ({waiting_requests}/{MAX_QUEUE_SIZE}). "
                f"Rejecting request with 503."
            )
            raise HTTPException(
                status_code=503,
                detail="Service temporarily overloaded. Please retry later.",
                headers={"Retry-After": str(max(1, BUSY_RETRY_AFTER_S))}
            )
        waiting_requests += 1
        waiting_counted = True
    
    try:
        # Acquire semaphore (blocks if MAX_CONCURRENT_TRANSCRIPTIONS is reached)
        await transcription_semaphore.acquire()
        semaphore_acquired = True
        
        async with waiting_requests_lock:
            if waiting_counted:
                waiting_requests -= 1
                waiting_counted = False

        async with active_requests_lock:
            if transcription_tier == "deferred":
                active_deferred_requests += 1
            else:
                active_realtime_requests += 1
            active_counted = True
        
        start_time = time.time()
        logger.info(
            f"Worker {WORKER_ID} received transcription request - "
            f"tier={transcription_tier}, filename: {file.filename}, content_type: {file.content_type}"
        )
        # Read audio file
        audio_bytes = await file.read()
        logger.info(f"Worker {WORKER_ID} read {len(audio_bytes)} bytes of audio data")
        
        # Convert to format suitable for faster-whisper
        # Use soundfile to properly decode audio formats (WAV, MP3, etc.)
        audio_io = io.BytesIO(audio_bytes)
        try:
            audio_array, sample_rate = sf.read(audio_io, dtype=np.float32)
            logger.info(f"Worker {WORKER_ID} decoded audio - shape: {audio_array.shape}, sample_rate: {sample_rate}")
        except Exception as e:
            logger.error(f"Worker {WORKER_ID} failed to decode audio with soundfile: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to decode audio file: {e}")
        
        # Ensure mono audio (convert stereo to mono if needed)
        if len(audio_array.shape) > 1:
            audio_array = np.mean(audio_array, axis=1)
            logger.info(f"Worker {WORKER_ID} converted to mono - shape: {audio_array.shape}")
        
        # Ensure audio is contiguous array
        audio_array = np.ascontiguousarray(audio_array, dtype=np.float32)
        
        # Transcribe (with optional temperature fallback)
        requested_temp = float(temperature) if temperature else 0.0
        temps = TEMPERATURE_FALLBACK_CHAIN if USE_TEMPERATURE_FALLBACK else [requested_temp]

        logger.info(
            f"Worker {WORKER_ID} starting transcription - requested_temp: {requested_temp}, "
            f"temps: {temps}, language: {language}, task: {task}, vad_filter: {VAD_FILTER}"
        )

        best: Optional[Tuple[str, str, float, List[Dict[str, Any]]]] = None
        last_info = None
        last_segments: List[Dict[str, Any]] = []

        for t in temps:
            # Run blocking transcription in thread pool to avoid blocking event loop
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
                    vad_parameters={
                        "threshold": VAD_FILTER_THRESHOLD,
                        "min_silence_duration_ms": VAD_MIN_SILENCE_DURATION_MS,
                    },
                    word_timestamps=False,
                )
            
            segments_list, info = await asyncio.get_event_loop().run_in_executor(
                transcription_executor, _transcribe_sync
            )
            last_info = info

            # Convert segments to list (faster-whisper returns generator)
            segments: List[Dict[str, Any]] = []
            for idx, segment in enumerate(segments_list):
                segments.append({
                    "id": idx,
                    "seek": 0,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "tokens": [],  # Not needed for PoC
                    "temperature": t,
                    "avg_logprob": segment.avg_logprob,
                    "compression_ratio": segment.compression_ratio,
                    "no_speech_prob": segment.no_speech_prob,
                    # Add audio_ fields that RemoteTranscriber looks for
                    "audio_start": segment.start,
                    "audio_end": segment.end,
                })
            last_segments = segments

            if _looks_like_silence(segments):
                best = ("", info.language, 0.0, [])
                logger.info(f"Worker {WORKER_ID} detected silence (temp={t})")
                break

            is_hallucination = _looks_like_hallucination(segments)

            if not is_hallucination:
                full_text = " ".join([s["text"].strip() for s in segments]).strip()
                duration = segments[-1]["end"] if segments else 0.0
                best = (full_text, info.language, duration, segments)
                logger.info(f"Worker {WORKER_ID} accepted transcription (temp={t})")
                break
            else:
                logger.info(f"Worker {WORKER_ID} rejected transcription as hallucination/low-confidence (temp={t})")

        if best is None:
            # Fall back to last attempt (even if it looks low-quality) to preserve backward behavior.
            info = last_info
            segments = last_segments
            full_text = " ".join([s["text"].strip() for s in segments]).strip()
            duration = segments[-1]["end"] if segments else 0.0
            best = (full_text, info.language if info else (language or "unknown"), duration, segments)

        full_text, detected_language, duration, segments = best
        logger.info(f"Worker {WORKER_ID} transcription completed - language: {detected_language}")
        
        processing_time = time.time() - start_time
        logger.info(
            f"Worker {WORKER_ID} completed in {processing_time:.2f}s - "
            f"Duration: {duration:.2f}s, Segments: {len(segments)}, Language: {detected_language}"
        )
        
        # Return format expected by Vexa RemoteTranscriber
        response = {
            "text": full_text,
            "language": detected_language,
            "duration": duration,
            "segments": segments,
        }
        
        # CTranslate2 handles memory management automatically
        
        return response
        
    except HTTPException:
        # Re-raise HTTP exceptions (429, 503, etc.)
        raise
    except Exception as e:
        logger.error(f"Worker {WORKER_ID} transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Keep counters and semaphore balanced even on early failures.
        if active_counted:
            async with active_requests_lock:
                if transcription_tier == "deferred":
                    active_deferred_requests = max(0, active_deferred_requests - 1)
                else:
                    active_realtime_requests = max(0, active_realtime_requests - 1)
            active_counted = False

        if waiting_counted:
            async with waiting_requests_lock:
                waiting_requests = max(0, waiting_requests - 1)
            waiting_counted = False

        if semaphore_acquired:
            transcription_semaphore.release()


@app.get("/")
async def root():
    """Root endpoint with service info"""
    return {
        "service": "Vexa Transcription Service",
        "worker_id": WORKER_ID,
        "model": MODEL_SIZE,
        "device": DEVICE,
        "status": "ready" if model is not None else "initializing",
        "endpoints": {
            "transcribe": "/v1/audio/transcriptions",
            "health": "/health"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
