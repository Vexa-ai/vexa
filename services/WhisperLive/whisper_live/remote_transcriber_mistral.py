"""
Remote API transcriber wrapper for Mistral Voxtral (WhisperLive).

This module provides a RemoteTranscriber class that wraps the Mistral audio transcription API
(https://api.mistral.ai/v1/audio/transcriptions), converting it to match the interface of the
local WhisperModel for seamless integration.

Uses httpx for direct HTTP calls to the Mistral transcription endpoint.
Configured via TRANSCRIBER_API_KEY and TRANSCRIBER_URL environment variables.
"""

import io
import logging
import os
import time
import wave
from typing import BinaryIO, Iterable, List, Optional, Tuple, Union

import httpx
import numpy as np

from .remote_transcriber import RemoteTranscriberOverloaded
from .transcriber import Segment, TranscriptionInfo, TranscriptionOptions, VadOptions

logger = logging.getLogger(__name__)


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


def normalize_language_code(language: Optional[str]) -> Optional[str]:
    """Convert language name (e.g. 'English') to ISO-639-1 code (e.g. 'en')."""
    if not language:
        return None
    language_lower = language.lower().strip()
    if len(language_lower) == 2 and language_lower.isalpha():
        return language_lower
    return LANGUAGE_NAME_TO_CODE.get(language_lower, language_lower)


class RemoteTranscriber:
    """
    Wrapper for Mistral audio transcription API that matches WhisperModel interface.

    Converts audio numpy arrays to WAV bytes and calls the Mistral
    /v1/audio/transcriptions endpoint via httpx with retry logic, then converts
    responses to Segment format expected by WhisperLive.
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[str] = None,
        vad_model: Optional[str] = None,
        timestamp_granularities: Optional[str] = None,
        sampling_rate: int = 16000,
    ):
        self.api_url = (
            api_url
            or os.getenv("TRANSCRIBER_URL")
            or os.getenv("REMOTE_TRANSCRIBER_URL")
        )
        if not self.api_url:
            raise ValueError(
                "Transcriber URL not provided. Set TRANSCRIBER_URL environment variable."
            )

        self.api_key = (
            api_key
            or os.getenv("TRANSCRIBER_API_KEY")
            or os.getenv("REMOTE_TRANSCRIBER_API_KEY")
            or ""
        ).strip()
        if not self.api_key:
            raise ValueError(
                "Transcriber API key not provided. Set TRANSCRIBER_API_KEY environment variable."
            )

        # Mistral transcription endpoint only supports voxtral-mini-latest.
        # Ignore the model parameter passed from server.py (often "default")
        # unless it's an actual voxtral model name.
        candidate = model or os.getenv("REMOTE_TRANSCRIBER_MODEL") or ""
        if candidate.startswith("voxtral"):
            self.model = candidate
        else:
            self.model = "voxtral-mini-latest"
        self.temperature = temperature or os.getenv(
            "REMOTE_TRANSCRIBER_TEMPERATURE", "0"
        )
        self.sampling_rate = sampling_rate

        # Retry configuration
        self.max_retries = 3
        self.initial_retry_delay = 1.0
        self.max_retry_delay = 10.0

        # HTTP client with connection pooling
        self.http_client = httpx.Client(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            http2=False,
        )

        api_key_masked = (
            f"{self.api_key[:4]}...{self.api_key[-4:]}"
            if len(self.api_key) > 8
            else "***"
        )
        logger.info(
            f"Mistral RemoteTranscriber initialized: url={self.api_url}, model={self.model}, key={api_key_masked}"
        )

    def _numpy_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """Convert numpy audio array to WAV file bytes in memory."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        audio = np.clip(audio, -1.0, 1.0)
        audio_int16 = (audio * 32767).astype(np.int16)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sampling_rate)
            wav_file.writeframes(audio_int16.tobytes())
        return wav_buffer.getvalue()

    def _call_remote_api(
        self,
        audio_bytes: bytes,
        language: Optional[str] = None,
    ) -> dict:
        """
        Call Mistral /v1/audio/transcriptions endpoint with retry logic.

        Args:
            audio_bytes: WAV file bytes.
            language: Optional ISO-639-1 language code.

        Returns:
            API response as dict with keys: model, text, language, segments, usage.
        """
        retry_count = 0
        last_exception = None

        headers = {"x-api-key": self.api_key}

        # Mistral transcription API uses multipart form data.
        # NOTE: timestamp_granularities is NOT compatible with language per Mistral docs.
        # NOTE: Mistral returns 500 for many language codes (pl, cs, hu, sv, da, fi, etc.)
        #       so we only send language if it's in their known-supported set.
        #       Otherwise we omit it and let Mistral auto-detect.
        MISTRAL_SUPPORTED_LANGUAGES = {
            "en",
            "es",
            "fr",
            "de",
            "it",
            "pt",
            "ru",
            "ja",
            "ko",
            "zh",
            "ar",
            "hi",
            "nl",
        }

        data = {
            "model": self.model,
        }

        if language and language in MISTRAL_SUPPORTED_LANGUAGES:
            data["language"] = language
        else:
            # Auto-detect language; safe to request segment timestamps
            data["timestamp_granularities"] = "segment"

        logger.debug(
            f"Mistral API request: url={self.api_url}, model={self.model}, "
            f"audio_size={len(audio_bytes)} bytes, form_data={data}"
        )

        while retry_count <= self.max_retries:
            try:
                files = {"file": ("audio.wav", audio_bytes, "audio/wav")}

                response = self.http_client.post(
                    self.api_url,
                    headers=headers,
                    files=files,
                    data=data,
                )

                # Handle overload/busy - bubble up for LIFO backpressure.
                # Mistral returns 500 with code "3000" for "Service unavailable" -
                # treat this the same as 429/503 (transient, not a format error).
                is_overloaded = response.status_code in (429, 503)
                if not is_overloaded and response.status_code == 500:
                    try:
                        err_body = response.json()
                        if (
                            err_body.get("code") == "3000"
                            or "unavailable" in err_body.get("message", "").lower()
                        ):
                            is_overloaded = True
                    except Exception:
                        pass

                if is_overloaded:
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

                if response.status_code >= 400:
                    # Log the response body for diagnostics before raise_for_status
                    logger.warning(
                        f"Mistral API HTTP {response.status_code}: {response.text[:500]}"
                    )

                response.raise_for_status()
                return response.json()

            except RemoteTranscriberOverloaded:
                raise
            except httpx.HTTPStatusError as e:
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
                    delay = min(
                        self.initial_retry_delay * (2 ** (retry_count - 1)),
                        self.max_retry_delay,
                    )
                    logger.warning(
                        f"Remote API call failed (attempt {retry_count}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Remote API call failed after {self.max_retries} retries: {e}"
                    )
                    raise
            except Exception as e:
                last_exception = e
                retry_count += 1
                if retry_count <= self.max_retries:
                    delay = min(
                        self.initial_retry_delay * (2 ** (retry_count - 1)),
                        self.max_retry_delay,
                    )
                    logger.warning(
                        f"Remote API call failed (attempt {retry_count}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Remote API call failed after {self.max_retries} retries: {e}"
                    )
                    raise

        raise last_exception or RuntimeError("Remote API call failed")

    def _response_to_segments(
        self,
        api_response: dict,
        audio_duration: float,
        segment_id_start: int = 0,
    ) -> List[Segment]:
        """
        Convert Mistral transcription response to Segment objects.

        Mistral response format:
            {
                "model": "voxtral-mini-...",
                "text": "full transcription text",
                "language": "en",
                "segments": [{"text": "...", "start": 0.8, "end": 8.1, ...}, ...],
                "usage": {...}
            }

        Segments from Mistral have: text, start, end (and optionally speaker_id).
        They do NOT have: no_speech_prob, avg_logprob, compression_ratio, tokens, seek.
        We fill in sensible defaults for WhisperLive compatibility.
        """
        segments = []
        api_segments = api_response.get("segments", [])

        if not api_segments:
            # No segments returned - check for text-only response
            text = api_response.get("text", "")
            if text.strip():
                segments.append(
                    Segment(
                        id=segment_id_start,
                        seek=0,
                        start=0.0,
                        end=audio_duration if audio_duration > 0 else len(text) * 0.1,
                        text=text,
                        tokens=[],
                        avg_logprob=-0.5,
                        compression_ratio=1.0,
                        no_speech_prob=0.0,
                        words=None,
                        temperature=float(self.temperature),
                    )
                )
            return segments

        for idx, api_seg in enumerate(api_segments):
            start = _to_float(api_seg.get("start"), default=0.0)
            end = _to_float(api_seg.get("end"), default=None)

            if end is None or end <= start:
                end = start + 0.5

            text = api_seg.get("text", "")

            segment = Segment(
                id=segment_id_start + idx,
                seek=0,
                start=start,
                end=end,
                text=text,
                tokens=[],
                avg_logprob=-0.5,
                compression_ratio=1.0,
                no_speech_prob=0.0,  # Mistral doesn't provide this; assume speech detected
                words=None,
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
        prepend_punctuations: str = '"\'"¿([{-',
        append_punctuations: str = '"\'.。,，!！?？:：")]}、',
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
        Transcribe audio using Mistral transcription API.

        Matches the WhisperModel.transcribe() signature for compatibility.
        Most parameters are ignored as Mistral handles them internally.
        """
        # Convert audio to numpy array
        if isinstance(audio, np.ndarray):
            audio_array = audio
        elif isinstance(audio, str):
            try:
                import soundfile as sf

                audio_array, sr = sf.read(audio)
                if sr != self.sampling_rate:
                    try:
                        from scipy import signal

                        audio_array = signal.resample(
                            audio_array, int(len(audio_array) * self.sampling_rate / sr)
                        )
                    except ImportError:
                        logger.warning("scipy not available for resampling.")
            except ImportError:
                logger.error("soundfile not available. Cannot read audio file.")
                raise
        else:
            try:
                import soundfile as sf

                audio_array, sr = sf.read(audio)
                if sr != self.sampling_rate:
                    try:
                        from scipy import signal

                        audio_array = signal.resample(
                            audio_array, int(len(audio_array) * self.sampling_rate / sr)
                        )
                    except ImportError:
                        logger.warning("scipy not available for resampling.")
            except ImportError:
                logger.error("soundfile not available. Cannot read audio file.")
                raise

        # Ensure mono
        if len(audio_array.shape) > 1:
            audio_array = np.mean(audio_array, axis=1)

        # Convert to WAV bytes
        audio_wav_bytes = self._numpy_to_wav_bytes(audio_array)

        # Normalize language code
        normalized_language = normalize_language_code(language)

        # Call Mistral API
        api_response = self._call_remote_api(
            audio_bytes=audio_wav_bytes,
            language=normalized_language,
        )

        # Calculate duration
        duration = len(audio_array) / self.sampling_rate

        # Convert to segments
        segments = self._response_to_segments(api_response, audio_duration=duration)

        # Extract language info
        api_language = api_response.get("language")
        detected_language = normalize_language_code(language or api_language or "en")

        # Create TranscriptionInfo
        info = TranscriptionInfo(
            language=detected_language,
            language_probability=1.0,
            duration=duration,
            duration_after_vad=duration,
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
                temperatures=[temperature]
                if isinstance(temperature, (int, float))
                else list(temperature),
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
            vad_options=vad_parameters
            if isinstance(vad_parameters, VadOptions)
            else VadOptions()
            if vad_parameters is None
            else VadOptions(**vad_parameters),
        )

        return segments, info

    def __del__(self):
        """Clean up HTTP client on destruction."""
        if hasattr(self, "http_client"):
            try:
                self.http_client.close()
            except Exception:
                pass
