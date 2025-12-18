"""
Vexa-Compatible Transcription Service (PoC)
Implements OpenAI Whisper API format for seamless integration with Vexa
"""
import os
import io
import time
import logging
from datetime import datetime
from typing import Optional, List
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
COMPUTE_TYPE_ENV = os.getenv("COMPUTE_TYPE", "").lower()
if COMPUTE_TYPE_ENV:
    COMPUTE_TYPE = COMPUTE_TYPE_ENV
else:
    # Default to INT8 for both GPU and CPU (optimal balance of speed, memory, and accuracy)
    COMPUTE_TYPE = "int8"

# CPU threads configuration (for CPU mode optimization)
CPU_THREADS = int(os.getenv("CPU_THREADS", "0"))  # 0 = auto-detect

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


@app.on_event("startup")
async def startup_event():
    """Initialize Whisper model on startup"""
    global model
    logger.info(f"Worker {WORKER_ID} starting up...")
    logger.info(f"Device: {DEVICE}, Model: {MODEL_SIZE}, Compute: {COMPUTE_TYPE}")
    
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
    task: str = Form("transcribe"),
    _: bool = Depends(verify_api_token)
):
    """
    OpenAI Whisper API compatible transcription endpoint
    
    Required by Vexa's RemoteTranscriber:
    - Accepts multipart/form-data with audio file
    - Returns verbose_json format with segments
    - Includes timing, language, and segment details
    """
    if not requested_model:
        raise HTTPException(status_code=400, detail="Model parameter is required")
    
    start_time = time.time()
    logger.info(f"Worker {WORKER_ID} received transcription request - filename: {file.filename}, content_type: {file.content_type}")
    
    try:
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
        
        # Transcribe
        temp_value = float(temperature) if temperature else 0.0
        logger.info(f"Worker {WORKER_ID} starting transcription - temp: {temp_value}, language: {language}")
        
        segments_list, info = model.transcribe(
            audio_array,
            language=language,
            task=task,
            initial_prompt=prompt,
            temperature=temp_value,
            vad_filter=True,
            word_timestamps=False
        )
        logger.info(f"Worker {WORKER_ID} transcription completed - language: {info.language}")
        
        # Convert segments to list (faster-whisper returns generator)
        segments = []
        for idx, segment in enumerate(segments_list):
            segments.append({
                "id": idx,
                "seek": 0,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "tokens": [],  # Not needed for PoC
                "temperature": temp_value,
                "avg_logprob": segment.avg_logprob,
                "compression_ratio": segment.compression_ratio,
                "no_speech_prob": segment.no_speech_prob,
                # Add audio_ fields that RemoteTranscriber looks for
                "audio_start": segment.start,
                "audio_end": segment.end,
            })
        
        # Build full transcript text
        full_text = " ".join([s["text"].strip() for s in segments])
        
        # Calculate duration
        duration = segments[-1]["end"] if segments else 0.0
        
        processing_time = time.time() - start_time
        logger.info(
            f"Worker {WORKER_ID} completed in {processing_time:.2f}s - "
            f"Duration: {duration:.2f}s, Segments: {len(segments)}, Language: {info.language}"
        )
        
        # Return format expected by Vexa RemoteTranscriber
        response = {
            "text": full_text,
            "language": info.language,
            "duration": duration,
            "segments": segments,
        }
        
        # CTranslate2 handles memory management automatically
        
        return response
        
    except Exception as e:
        logger.error(f"Worker {WORKER_ID} transcription failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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

