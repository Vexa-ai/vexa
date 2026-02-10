"""
TensorRT-based transcriber. Placeholder for future local GPU transcription.
The gateway currently uses AWS Transcribe Streaming only.
"""
import logging

logger = logging.getLogger(__name__)


async def run_tensorrt_session(*args, **kwargs):
    """Placeholder: not implemented; gateway uses AWS Transcribe."""
    raise NotImplementedError("TensorRT transcriber not used; gateway uses AWS Transcribe Streaming.")
