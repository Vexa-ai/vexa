"""
Groq API transcriber wrapper for WhisperLive.

This module provides a GroqTranscriber class that wraps Groq's speech-to-text API,
converting it to match the interface of the local WhisperModel for seamless integration.
"""

import os
import tempfile
import wave
import logging
import time
from typing import BinaryIO, Iterable, List, Optional, Tuple, Union
import numpy as np

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logging.warning("Groq package not available. Install with: pip install groq")

from .transcriber import Segment, TranscriptionInfo, TranscriptionOptions, VadOptions

logger = logging.getLogger(__name__)

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


class GroqTranscriber:
    """
    Wrapper for Groq API transcription that matches WhisperModel interface.
    
    Converts audio numpy arrays to temporary WAV files and calls Groq API
    with retry logic, then converts responses to Segment format.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "whisper-large-v3-turbo",
        sampling_rate: int = 16000,
    ):
        """
        Initialize Groq transcriber.
        
        Args:
            api_key: Groq API key. If None, reads from GROQ_API_KEY env var.
            model: Groq model to use (whisper-large-v3-turbo or whisper-large-v3).
            sampling_rate: Audio sampling rate (default 16000 Hz).
        """
        if not GROQ_AVAILABLE:
            raise RuntimeError(
                "Groq package is not installed. Install with: pip install groq"
            )
        
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Groq API key not provided. Set GROQ_API_KEY environment variable "
                "or pass api_key parameter."
            )
        
        self.model = model
        self.sampling_rate = sampling_rate
        self.client = Groq(api_key=self.api_key)
        
        # Retry configuration
        self.max_retries = 3
        self.initial_retry_delay = 1.0  # seconds
        self.max_retry_delay = 10.0  # seconds
        
        logger.info(f"Initialized GroqTranscriber with model={model}")
    
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
    
    def _call_groq_api(
        self,
        audio_file_path: str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        task: str = "transcribe",
    ) -> dict:
        """
        Call Groq API with retry logic.
        
        Args:
            audio_file_path: Path to audio file.
            language: Language code (ISO-639-1) or None for auto-detect.
            prompt: Optional prompt for context/spelling.
            task: "transcribe" or "translate" (translate only supports 'en').
            
        Returns:
            Groq API response as dict.
        """
        retry_count = 0
        last_exception = None
        
        while retry_count <= self.max_retries:
            try:
                with open(audio_file_path, "rb") as audio_file:
                    response = self.client.audio.transcriptions.create(
                        file=audio_file,
                        model=self.model,
                        language=language,
                        prompt=prompt,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        temperature=0.0,
                    )
                
                # Convert response to dict if needed
                if hasattr(response, 'model_dump'):
                    return response.model_dump()
                elif hasattr(response, 'dict'):
                    return response.dict()
                else:
                    # Assume it's already a dict or has __dict__
                    return dict(response) if not isinstance(response, dict) else response
                    
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
                        f"Groq API call failed (attempt {retry_count}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Groq API call failed after {self.max_retries} retries: {e}")
                    raise
        
        # Should not reach here, but just in case
        raise last_exception or RuntimeError("Groq API call failed")
    
    def _groq_response_to_segments(
        self,
        groq_response: dict,
        segment_id_start: int = 0,
    ) -> List[Segment]:
        """
        Convert Groq API response to Segment objects.
        
        Args:
            groq_response: Groq API response dict.
            segment_id_start: Starting ID for segments.
            
        Returns:
            List of Segment objects.
        """
        segments = []
        
        # Groq returns segments in the response
        groq_segments = groq_response.get("segments", [])
        
        if not groq_segments:
            # If no segments, check if there's just text
            text = groq_response.get("text", "")
            if text.strip():
                # Create a single segment
                duration = groq_response.get("duration", 0.0)
                segments.append(Segment(
                    id=segment_id_start,
                    seek=0,
                    start=0.0,
                    end=duration,
                    text=text,
                    tokens=groq_response.get("tokens", []),
                    avg_logprob=groq_response.get("avg_logprob", -0.5),
                    compression_ratio=groq_response.get("compression_ratio", 1.0),
                    no_speech_prob=groq_response.get("no_speech_prob", 0.0),
                    words=None,  # Will be populated if word timestamps available
                    temperature=0.0,
                ))
            return segments
        
        # Process each segment
        for idx, groq_seg in enumerate(groq_segments):
            # Extract word timestamps if available
            words = None
            if "words" in groq_seg:
                from .transcriber import Word
                words = [
                    Word(
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        word=w.get("word", ""),
                        probability=w.get("probability", 0.0),
                    )
                    for w in groq_seg["words"]
                ]
            
            segment = Segment(
                id=segment_id_start + idx,
                seek=groq_seg.get("seek", 0),
                start=groq_seg.get("start", 0.0),
                end=groq_seg.get("end", 0.0),
                text=groq_seg.get("text", ""),
                tokens=groq_seg.get("tokens", []),
                avg_logprob=groq_seg.get("avg_logprob", -0.5),
                compression_ratio=groq_seg.get("compression_ratio", 1.0),
                no_speech_prob=groq_seg.get("no_speech_prob", 0.0),
                words=words,
                temperature=0.0,
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
        Transcribe audio using Groq API.
        
        This method matches the signature of WhisperModel.transcribe() for compatibility.
        Many parameters are ignored as Groq API handles them internally.
        
        Args:
            audio: Audio input (numpy array, file path, or file-like object).
            language: Language code (ISO-639-1) or None for auto-detect.
            task: "transcribe" or "translate".
            initial_prompt: Optional prompt for context/spelling.
            Other parameters: Ignored (kept for compatibility).
            
        Returns:
            Tuple of (segments generator, TranscriptionInfo).
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
                # Token IDs - can't use directly with Groq
                logger.warning("Token ID prompts not supported by Groq API, ignoring")
        
        # Create temporary WAV file
        temp_file = None
        try:
            temp_file = self._numpy_to_wav_file(audio_array)
            
            # Normalize language code before API call
            normalized_language = normalize_language_code(language)
            
            # Call Groq API
            groq_response = self._call_groq_api(
                audio_file_path=temp_file,
                language=normalized_language,
                prompt=prompt_str,
                task=task,
            )
            
            # Convert to segments
            segments = self._groq_response_to_segments(groq_response)
            
            # Extract language info and normalize to ISO code
            groq_language = groq_response.get("language")
            detected_language = normalize_language_code(language or groq_language or "en")
            language_probability = 1.0  # Groq doesn't provide probability
            
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
            
        finally:
            # Clean up temporary file
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logger.warning(f"Failed to remove temporary file {temp_file}: {e}")

