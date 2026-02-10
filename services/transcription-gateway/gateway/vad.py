"""
Voice Activity Detection (VAD). Placeholder for future VAD-based filtering or segmentation.
"""
import logging

logger = logging.getLogger(__name__)


def is_speech(_audio_chunk: bytes, _sample_rate: int = 16000) -> bool:
    """Placeholder: always returns True (no VAD filtering)."""
    return True
