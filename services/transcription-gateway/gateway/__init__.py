"""
Transcription Gateway: WebSocket server that accepts bot audio and transcribes via AWS Transcribe Streaming.
Pushes segments to the same Redis stream consumed by transcription-collector (payload format compatible).
"""
from gateway.__version__ import __version__
from gateway.server import main

__all__ = ["__version__", "main"]
