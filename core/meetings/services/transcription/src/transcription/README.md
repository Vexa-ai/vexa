# transcription/ — the package

The FastAPI STT worker. `main.py` holds the app (`app`): env-driven config, lazy
faster-whisper model load, and the routes `/v1/audio/transcriptions`, `/health`, `/`, with
concurrency + backpressure guards. `__init__.py` re-exports `app`; `__main__.py` runs it under
uvicorn (`python -m transcription`). Third-party + own-module imports only.
