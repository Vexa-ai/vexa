"""
TensorRT utilities. Placeholder for future GPU-accelerated local transcription.
The gateway currently uses AWS Transcribe (cloud); TensorRT would apply to a local model path.
"""
import logging

logger = logging.getLogger(__name__)


def is_tensorrt_available() -> bool:
    """Placeholder: TensorRT not used by gateway (uses AWS Transcribe)."""
    return False
