"""
Silero VAD integration for silence detection in quality tests.

Uses Silero VAD to label audio as "speech" or "silence", which helps
validate that the transcription service doesn't hallucinate on silence.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch

# Suppress warnings from Silero VAD
warnings.filterwarnings("ignore", category=UserWarning)

# Silero VAD model cache
_vad_model: Optional[torch.nn.Module] = None
_vad_utils: Optional[object] = None


def _load_silero_vad() -> tuple[torch.nn.Module, object]:
    """Load Silero VAD model and utils (lazy, cached)."""
    global _vad_model, _vad_utils
    
    if _vad_model is not None:
        return _vad_model, _vad_utils
    
    try:
        import silero_vad
    except ImportError as e:
        raise ImportError(
            "silero-vad not installed. Install with: pip install silero-vad torch"
        ) from e
    
    model, utils = silero_vad.load_vad_model()
    _vad_model = model
    _vad_utils = utils
    return model, utils


def detect_speech_presence(
    audio_path: Path,
    *,
    sample_rate: int = 16000,
    threshold: float = 0.5,
) -> tuple[bool, float]:
    """
    Detect if audio contains speech using Silero VAD.
    
    Args:
        audio_path: Path to audio file (WAV, 16kHz mono expected)
        sample_rate: Expected sample rate (default 16000)
        threshold: VAD threshold (0-1, higher = less sensitive)
    
    Returns:
        (has_speech: bool, speech_ratio: float)
        - has_speech: True if any speech detected above threshold
        - speech_ratio: Fraction of audio frames classified as speech (0-1)
    """
    model, utils = _load_silero_vad()
    get_speech_timestamps = utils[0]
    
    # Load audio
    audio, sr = sf.read(str(audio_path), dtype=np.float32)
    
    # Ensure mono
    if len(audio.shape) > 1:
        audio = np.mean(audio, axis=1)
    
    # Resample if needed (Silero expects 8kHz or 16kHz)
    if sr != sample_rate:
        # Simple resampling (for quality tests, this is fine)
        if sr > sample_rate:
            # Downsample
            step = sr // sample_rate
            audio = audio[::step]
        else:
            # Upsample (repeat samples)
            ratio = sample_rate // sr
            audio = np.repeat(audio, ratio)
        sr = sample_rate
    
    # Convert to torch tensor
    audio_tensor = torch.from_numpy(audio)
    
    # Run VAD
    speech_timestamps = get_speech_timestamps(
        audio_tensor,
        model,
        threshold=threshold,
        sampling_rate=sr,
    )
    
    # Calculate speech ratio
    total_duration = len(audio) / sr
    if not speech_timestamps:
        return False, 0.0
    
    speech_duration = sum(
        (ts["end"] - ts["start"]) for ts in speech_timestamps
    )
    speech_ratio = speech_duration / total_duration if total_duration > 0 else 0.0
    
    # Has speech if any timestamps found
    has_speech = len(speech_timestamps) > 0
    
    return has_speech, speech_ratio


def is_likely_silence(
    audio_path: Path,
    *,
    sample_rate: int = 16000,
    threshold: float = 0.5,
    min_speech_ratio: float = 0.1,
) -> bool:
    """
    Check if audio is likely silence (no speech detected).
    
    Args:
        audio_path: Path to audio file
        sample_rate: Expected sample rate
        threshold: VAD threshold
        min_speech_ratio: Minimum speech ratio to consider "not silence"
    
    Returns:
        True if audio appears to be silence
    """
    has_speech, speech_ratio = detect_speech_presence(
        audio_path,
        sample_rate=sample_rate,
        threshold=threshold,
    )
    
    # Consider silence if no speech detected OR speech ratio is very low
    return not has_speech or speech_ratio < min_speech_ratio










