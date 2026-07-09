# src — the transcription worker

`transcription/` is the package (on `PYTHONPATH=src`):

| Module | Role |
|---|---|
| `main.py` | the FastAPI app (`app`) — config from env, lazy faster-whisper model load, `/v1/audio/transcriptions` + `/health` + `/` , concurrency/backpressure guards |
| `__init__.py` | re-exports `app` |
| `__main__.py` | `python -m transcription` → uvicorn on `:8000` |

Self-contained: third-party imports + own module only (no cross-brick edges).
