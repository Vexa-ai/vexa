"""
Remote API transcriber wrapper for WhisperLive.

This module provides a RemoteTranscriber class that wraps any HTTP-based speech-to-text API,
converting it to match the interface of the local WhisperModel for seamless integration.
Supports configurable endpoints like Fireworks.ai, Groq, etc.
"""

import os
import io
import tempfile
import wave
import logging
import time
from typing import BinaryIO, Iterable, List, Optional, Tuple, Union
import numpy as np
import httpx

from .transcriber import Segment, TranscriptionInfo, TranscriptionOptions, VadOptions

logger = logging.getLogger(__name__)

# Busy/overload signal from remote API. We intentionally bubble this up so the caller
# (WhisperLive server) can keep buffering and transcribe the *latest* audio window
# instead of blocking on retries for an older chunk.
class RemoteTranscriberOverloaded(RuntimeError):
    def __init__(self, status_code: int, retry_after_s: float = 0.0, detail: str = ""):
        self.status_code = int(status_code)
        self.retry_after_s = float(retry_after_s or 0.0)
        self.detail = detail or ""
        super().__init__(f"Remote transcriber overloaded (HTTP {self.status_code}, retry_after={self.retry_after_s}s): {self.detail}")


# Language name to ISO-639-1 code mapping
LANGUAGE_NAME_TO_CODE = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "arabic": "ar",
    "hindi": "hi",
    "dutch": "nl",
    "polish": "pl",
    "turkish": "tr",
    "vietnamese": "vi",
    "thai": "th",
    "greek": "el",
    "czech": "cs",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "hungarian": "hu",
    "romanian": "ro",
    "ukrainian": "uk",
    "hebrew": "he",
    "indonesian": "id",
    "malay": "ms",
    "tagalog": "tl",
}


def _to_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_probability(value):
    """
    Clamp probability value to [0.0, 1.0] range.
    For Fireworks API, no_speech_prob values > 1.0 are clamped to 1.0.
    Note: Fireworks API may return no_speech_prob in a different format than local Whisper.
    """
    try:
        val = float(value)
        # Handle log probabilities or values > 1.0 by clamping
        if val > 1.0:
            # If value > 1.0, it might be a log probability or different scale
            # For now, clamp to 1.0, but this might need adjustment based on API behavior
            return 1.0
        return max(0.0, min(1.0, val))
    except (TypeError, ValueError):
        return 0.0


def normalize_language_code(language: Optional[str]) -> Optional[str]:
    """
    Convert language name (e.g., "English") to ISO-639-1 code (e.g., "en").
    Returns the code if it's already a code, or converts if it's a name.
    """
    if not language:
        return None
    
    language_lower = language.lower().strip()
    
    # If it's already a 2-letter code, return as-is
    if len(language_lower) == 2 and language_lower.isalpha():
        return language_lower
    
    # Try to map from name to code
    return LANGUAGE_NAME_TO_CODE.get(language_lower, language_lower)


class RemoteTranscriber:
    """
    Wrapper for remote HTTP API transcription that matches WhisperModel interface.
    
    Converts audio numpy arrays to WAV bytes in memory and calls remote HTTP API
    with retry logic and connection pooling, then converts responses to Segment format.
    Optimized for real-time performance with reduced latency.
    """
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        transcription_tier: str = "realtime",
        temperature: Optional[str] = None,
        vad_model: Optional[str] = None,
        timestamp_granularities: Optional[str] = None,
        sampling_rate: int = 16000,
    ):
        """
        Initialize remote transcriber.
        
        Args:
            api_url: API endpoint URL. If None, reads from REMOTE_TRANSCRIBER_URL env var.
            api_key: API key for authentication. If None, reads from REMOTE_TRANSCRIBER_API_KEY env var.
            model: Model name. If None, reads from REMOTE_TRANSCRIBER_MODEL env var.
            temperature: Temperature parameter. If None, reads from REMOTE_TRANSCRIBER_TEMPERATURE env var, defaults to "0".
            vad_model: VAD model name. If None, reads from REMOTE_TRANSCRIBER_VAD_MODEL env var.
            sampling_rate: Audio sampling rate (default 16000 Hz).
        """
        self.api_url = api_url or os.getenv("REMOTE_TRANSCRIBER_URL")
        if not self.api_url:
            raise ValueError(
                "Remote transcriber URL not provided. Set REMOTE_TRANSCRIBER_URL environment variable "
                "or pass api_url parameter."
            )
        
        self.api_key = (api_key or os.getenv("REMOTE_TRANSCRIBER_API_KEY") or "").strip()
        if not self.api_key:
            raise ValueError(
                "Remote transcriber API key not provided. Set REMOTE_TRANSCRIBER_API_KEY environment variable "
                "or pass api_key parameter."
            )
        # Log masked API key for debugging (first 4 and last 4 chars)
        api_key_masked = f"{self.api_key[:4]}...{self.api_key[-4:]}" if len(self.api_key) > 8 else "***"
        
        # Model is required by API format but ignored by transcription-service (uses its own MODEL_SIZE)
        # Default to "default" if not provided
        self.model = model or os.getenv("REMOTE_TRANSCRIBER_MODEL") or "default"
        
        # Hardcode response format to verbose_json
        self.response_format = "verbose_json"
        self.temperature = temperature or os.getenv("REMOTE_TRANSCRIBER_TEMPERATURE", "0")
        self.vad_model = vad_model or os.getenv("REMOTE_TRANSCRIBER_VAD_MODEL")
        normalized_tier = str(transcription_tier or "realtime").strip().lower()
        self.transcription_tier = normalized_tier if normalized_tier in ("realtime", "deferred") else "realtime"
        # Request only segment timestamps (no word-level precision needed)
        self.timestamp_granularities = "segment"
        self.sampling_rate = sampling_rate
        
        # Retry configuration
        self.max_retries = 3
        self.initial_retry_delay = 1.0  # seconds
        self.max_retry_delay = 10.0  # seconds
        
        # Create HTTP client with connection pooling for better performance
        self.http_client = httpx.Client(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            http2=False,  # Disable HTTP/2 for compatibility
        )
    
    def _numpy_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """
        Convert numpy audio array to WAV file bytes in memory.
        
        Args:
            audio: Audio array (float32, normalized to [-1, 1]).
            
        Returns:
            WAV file bytes.
        """
        # Ensure audio is float32 and in valid range
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        
        # Clamp to valid range
        audio = np.clip(audio, -1.0, 1.0)
        
        # Convert to int16 PCM
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Create WAV file in memory
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self.sampling_rate)
            wav_file.writeframes(audio_int16.tobytes())
        
        return wav_buffer.getvalue()
    
    def _numpy_to_wav_file(self, audio: np.ndarray, temp_dir: Optional[str] = None) -> str:
        """
        Convert numpy audio array to temporary WAV file.
        
        Args:
            audio: Audio array (float32, normalized to [-1, 1]).
            temp_dir: Optional directory for temp file.
            
        Returns:
            Path to temporary WAV file.
        """
        # Ensure audio is float32 and in valid range
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        
        # Clamp to valid range
        audio = np.clip(audio, -1.0, 1.0)
        
        # Convert to int16 PCM
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Create temporary file
        fd, temp_path = tempfile.mkstemp(suffix='.wav', dir=temp_dir)
        os.close(fd)
        
        # Write WAV file
        with wave.open(temp_path, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(self.sampling_rate)
            wav_file.writeframes(audio_int16.tobytes())
        
        return temp_path
    
    def _call_remote_api(
        self,
        audio_bytes: bytes,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        task: str = "transcribe",
    ) -> dict:
        """
        Call remote HTTP API with retry logic.
        
        Args:
            audio_bytes: Audio file bytes (WAV format).
            language: Language code (ISO-639-1) or None for auto-detect.
            prompt: Optional prompt for context/spelling.
            task: "transcribe" or "translate".
            
        Returns:
            API response as dict.
        """
        retry_count = 0
        last_exception = None
        
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Transcription-Tier": self.transcription_tier,
        }
        
        # Prepare form data
        data = {
            "model": self.model,
            "temperature": self.temperature,
            "transcription_tier": self.transcription_tier,
        }
        
        if self.vad_model:
            data["vad_model"] = self.vad_model
        
        if language:
            data["language"] = language
        
        if prompt:
            data["prompt"] = prompt
        
        if task == "translate":
            data["task"] = task
        
        # Add response_format if supported (some APIs may ignore this)
        if self.response_format:
            data["response_format"] = self.response_format
        
        if self.timestamp_granularities:
            data["timestamp_granularities"] = self.timestamp_granularities
        
        # Log request details (masked)
        auth_header_masked = f"Bearer {self.api_key[:4]}...{self.api_key[-4:]}" if len(self.api_key) > 8 else "Bearer ***"
        
        while retry_count <= self.max_retries:
            try:
                # Use in-memory file upload for better performance
                files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
                
                response = self.http_client.post(
                    self.api_url,
                    headers=headers,
                    files=files,
                    data=data,
                )
                
                # IMPORTANT: Don't block inside this call on overload/busy.
                # WhisperLive already buffers/coalesces audio; we want the caller to keep
                # accumulating and then transcribe the latest window.
                if response.status_code in (429, 503):
                    retry_after_raw = response.headers.get("Retry-After", "1")
                    try:
                        retry_after = float(retry_after_raw)
                    except Exception:
                        retry_after = 1.0
                    raise RemoteTranscriberOverloaded(
                        status_code=response.status_code,
                        retry_after_s=retry_after,
                        detail=response.text[:500] if response.text else "",
                    )
                
                response.raise_for_status()
                
                # Parse response
                if self.response_format == "verbose_json" or self.response_format == "json":
                    result = response.json()
                    return result
                else:
                    text = response.text.strip()
                    if text.startswith("{") or text.startswith("["):
                        result = response.json()
                        return result
                    else:
                        return {"text": text}
                    
            except httpx.HTTPStatusError as e:
                # Handle HTTP errors (but not 429/503 which are handled above)
                if e.response is not None and e.response.status_code in (429, 503):
                    retry_after_raw = e.response.headers.get("Retry-After", "1")
                    try:
                        retry_after = float(retry_after_raw)
                    except Exception:
                        retry_after = 1.0
                    raise RemoteTranscriberOverloaded(
                        status_code=e.response.status_code,
                        retry_after_s=retry_after,
                        detail=e.response.text[:500] if e.response.text else "",
                    )
                last_exception = e
                retry_count += 1
                
                if retry_count <= self.max_retries:
                    # Exponential backoff
                    delay = min(
                        self.initial_retry_delay * (2 ** (retry_count - 1)),
                        self.max_retry_delay
                    )
                    logger.warning(
                        f"Remote API call failed (attempt {retry_count}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Remote API call failed after {self.max_retries} retries: {e}")
                    raise
            except RemoteTranscriberOverloaded:
                # Bubble up overload so the caller can keep buffering/coalescing
                # instead of blocking on retries for an older chunk.
                raise
            except Exception as e:
                last_exception = e
                retry_count += 1
                
                if retry_count <= self.max_retries:
                    # Exponential backoff
                    delay = min(
                        self.initial_retry_delay * (2 ** (retry_count - 1)),
                        self.max_retry_delay
                    )
                    logger.warning(
                        f"Remote API call failed (attempt {retry_count}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Remote API call failed after {self.max_retries} retries: {e}")
                    raise
        
        # Should not reach here, but just in case
        raise last_exception or RuntimeError("Remote API call failed")
    
    def _response_to_segments(
        self,
        api_response: dict,
        segment_id_start: int = 0,
    ) -> List[Segment]:
        """
        Convert API response to Segment objects.
        
        Supports multiple response formats:
        - verbose_json: Full response with segments array
        - json: JSON with segments or text
        - text: Simple text response
        
        Args:
            api_response: API response dict.
            segment_id_start: Starting ID for segments.
            
        Returns:
            List of Segment objects.
        """
        segments = []
        
        # Check if response has segments array (verbose_json format)
        api_segments = api_response.get("segments", [])
        
        
        if not api_segments:
            # If no segments, check if there's just text
            text = api_response.get("text", "")
            if text.strip():
                # Create a single segment
                duration = api_response.get("duration", 0.0)
                # Handle no_speech_prob: if text exists, speech was detected
                raw_no_speech_prob = api_response.get("no_speech_prob", 0.0)
                clamped_prob = _clamp_probability(raw_no_speech_prob)
                if clamped_prob >= 1.0:
                    # Text exists but no_speech_prob is high - likely inverted or wrong scale
                    clamped_prob = 0.1
                
                segments.append(Segment(
                    id=segment_id_start,
                    seek=0,
                    start=0.0,
                    end=duration if duration > 0 else len(text) * 0.1,  # Estimate if no duration
                    text=text,
                    tokens=api_response.get("tokens", []),
                    avg_logprob=api_response.get("avg_logprob", -0.5),
                    compression_ratio=api_response.get("compression_ratio", 1.0),
                    no_speech_prob=clamped_prob,
                    words=None,  # Will be populated if word timestamps available
                    temperature=float(self.temperature),
                ))
            return segments
        
        # Process each segment
        for idx, api_seg in enumerate(api_segments):
            # Word-level timestamps not used - set to None
            words = None
            
            # Determine best available timestamps
            start = api_seg.get("audio_start")
            end = api_seg.get("audio_end")
            
            if start is None:
                start = api_seg.get("start", 0.0)
            if end is None:
                end = api_seg.get("end")
            
            start = _to_float(start, default=0.0)
            end = _to_float(end, default=None)
            
            if end is None or end <= start:
                seg_duration = _to_float(api_seg.get("duration"), default=None)
                if seg_duration and seg_duration > 0:
                    end = start + seg_duration
            
            if end is None or end <= start:
                total_duration = _to_float(api_response.get("duration"), default=None)
                if total_duration and total_duration > 0:
                    # Cap to total duration if provided, otherwise just extend slightly
                    end = min(total_duration, start + total_duration) if start > 0 else total_duration
                else:
                    end = start + 0.5  # fallback to half-second span to keep segments valid
            
            if end is None or end <= start:
                end = start + 0.5
            
            # Handle no_speech_prob: Fireworks API may return values > 1.0 or use inverted logic
            # If text is present and non-empty, assume speech was detected (no_speech_prob should be low)
            raw_no_speech_prob = api_seg.get("no_speech_prob", 0.0)
            clamped_prob = _clamp_probability(raw_no_speech_prob)
            
            # If Fireworks returns no_speech_prob >= 1.0 but segment has text,
            # it likely means speech WAS detected (inverted logic or different scale)
            # Set to a low value to prevent filtering out valid segments
            if clamped_prob >= 1.0 and api_seg.get("text", "").strip():
                # Segment has text, so speech was detected - use low no_speech_prob
                clamped_prob = 0.1
            
            segment = Segment(
                id=segment_id_start + idx,
                seek=api_seg.get("seek", 0),
                start=start,
                end=end,
                text=api_seg.get("text", ""),
                tokens=api_seg.get("tokens", []),
                avg_logprob=api_seg.get("avg_logprob", -0.5),
                compression_ratio=api_seg.get("compression_ratio", 1.0),
                no_speech_prob=clamped_prob,
                words=words,
                temperature=float(self.temperature),
            )
            segments.append(segment)
        
        return segments
    
    def transcribe(
        self,
        audio: Union[str, BinaryIO, np.ndarray],
        language: Optional[str] = None,
        task: str = "transcribe",
        log_progress: bool = False,
        beam_size: int = 1,
        best_of: int = 5,
        patience: float = 1,
        length_penalty: float = 1,
        repetition_penalty: float = 1,
        no_repeat_ngram_size: int = 0,
        temperature: Union[float, List[float], Tuple[float, ...]] = [0.0],
        compression_ratio_threshold: Optional[float] = 2.4,
        log_prob_threshold: Optional[float] = -1.0,
        no_speech_threshold: Optional[float] = 0.6,
        condition_on_previous_text: bool = True,
        prompt_reset_on_temperature: float = 0.5,
        initial_prompt: Optional[Union[str, Iterable[int]]] = None,
        prefix: Optional[str] = None,
        suppress_blank: bool = True,
        suppress_tokens: Optional[List[int]] = [-1],
        without_timestamps: bool = False,
        max_initial_timestamp: float = 1.0,
        word_timestamps: bool = False,
        prepend_punctuations: str = "\"'\"¿([{-",
        append_punctuations: str = "\"'.。,，!！?？:：\")]}、",
        multilingual: bool = False,
        vad_filter: bool = False,
        vad_parameters: Optional[Union[dict, VadOptions]] = None,
        max_new_tokens: Optional[int] = None,
        chunk_length: Optional[int] = None,
        clip_timestamps: Union[str, List[float]] = "0",
        hallucination_silence_threshold: Optional[float] = None,
        hotwords: Optional[str] = None,
        language_detection_threshold: Optional[float] = 0.5,
        language_detection_segments: int = 10,
    ) -> Tuple[Iterable[Segment], TranscriptionInfo]:
        """
        Transcribe audio using remote HTTP API.
        
        This method matches the signature of WhisperModel.transcribe() for compatibility.
        Many parameters are ignored as remote API handles them internally.
        
        Args:
            audio: Audio input (numpy array, file path, or file-like object).
            language: Language code (ISO-639-1) or None for auto-detect.
            task: "transcribe" or "translate".
            initial_prompt: Optional prompt for context/spelling.
            Other parameters: Ignored (kept for compatibility).
            
        Returns:
            Tuple of (segments list, TranscriptionInfo).
        """
        # Convert audio to numpy array if needed
        if isinstance(audio, np.ndarray):
            audio_array = audio
        elif isinstance(audio, str):
            # File path - read it (fallback for compatibility)
            try:
                import soundfile as sf
                audio_array, sr = sf.read(audio)
                if sr != self.sampling_rate:
                    try:
                        from scipy import signal
                        audio_array = signal.resample(audio_array, int(len(audio_array) * self.sampling_rate / sr))
                    except ImportError:
                        logger.warning("scipy not available for resampling. Audio may have wrong sample rate.")
            except ImportError:
                logger.error("soundfile not available. Cannot read audio file.")
                raise
        else:
            # File-like object (fallback for compatibility)
            try:
                import soundfile as sf
                audio_array, sr = sf.read(audio)
                if sr != self.sampling_rate:
                    try:
                        from scipy import signal
                        audio_array = signal.resample(audio_array, int(len(audio_array) * self.sampling_rate / sr))
                    except ImportError:
                        logger.warning("scipy not available for resampling. Audio may have wrong sample rate.")
            except ImportError:
                logger.error("soundfile not available. Cannot read audio file.")
                raise
        
        # Ensure mono
        if len(audio_array.shape) > 1:
            audio_array = np.mean(audio_array, axis=1)
        
        # Convert prompt
        prompt_str = None
        if initial_prompt:
            if isinstance(initial_prompt, str):
                prompt_str = initial_prompt
            elif isinstance(initial_prompt, Iterable):
                # Token IDs - can't use directly with remote API
                logger.warning("Token ID prompts not supported by remote API, ignoring")
        
        # Convert audio to WAV bytes in memory (no temp file I/O)
        audio_wav_bytes = self._numpy_to_wav_bytes(audio_array)
        
        # Normalize language code before API call
        normalized_language = normalize_language_code(language)
        
        # Call remote API with in-memory audio bytes
        api_response = self._call_remote_api(
            audio_bytes=audio_wav_bytes,
            language=normalized_language,
            prompt=prompt_str,
            task=task,
        )
        
        # Convert to segments
        segments = self._response_to_segments(api_response)
        
        # Extract language info and normalize to ISO code
        api_language = api_response.get("language")
        detected_language = normalize_language_code(language or api_language or "en")
        language_probability = 1.0  # Remote API may not provide probability
        
        # Calculate duration
        duration = len(audio_array) / self.sampling_rate
        duration_after_vad = duration  # VAD is handled client-side
        
        # Create TranscriptionInfo
        info = TranscriptionInfo(
            language=detected_language,
            language_probability=language_probability,
            duration=duration,
            duration_after_vad=duration_after_vad,
            all_language_probs=None,
            transcription_options=TranscriptionOptions(
                beam_size=beam_size,
                best_of=best_of,
                patience=patience,
                length_penalty=length_penalty,
                repetition_penalty=repetition_penalty,
                no_repeat_ngram_size=no_repeat_ngram_size,
                log_prob_threshold=log_prob_threshold,
                no_speech_threshold=no_speech_threshold,
                compression_ratio_threshold=compression_ratio_threshold,
                condition_on_previous_text=condition_on_previous_text,
                prompt_reset_on_temperature=prompt_reset_on_temperature,
                temperatures=[temperature] if isinstance(temperature, (int, float)) else list(temperature),
                initial_prompt=initial_prompt,
                prefix=prefix,
                suppress_blank=suppress_blank,
                suppress_tokens=suppress_tokens,
                without_timestamps=without_timestamps,
                max_initial_timestamp=max_initial_timestamp,
                word_timestamps=word_timestamps,
                prepend_punctuations=prepend_punctuations,
                append_punctuations=append_punctuations,
                multilingual=multilingual,
                max_new_tokens=max_new_tokens,
                clip_timestamps=clip_timestamps,
                hallucination_silence_threshold=hallucination_silence_threshold,
                hotwords=hotwords,
            ),
            vad_options=vad_parameters if isinstance(vad_parameters, VadOptions) else VadOptions() if vad_parameters is None else VadOptions(**vad_parameters),
        )
        
        # Return segments as a list (not iterator) to avoid len() issues
        return segments, info
    
    def __del__(self):
        """Clean up HTTP client on destruction."""
        if hasattr(self, 'http_client'):
            try:
                self.http_client.close()
            except Exception:
                pass
