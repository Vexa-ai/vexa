"""Entrypoint: `python -m transcription` → the uvicorn-served faster-whisper worker (port 8000)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "transcription.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info"),
    )
