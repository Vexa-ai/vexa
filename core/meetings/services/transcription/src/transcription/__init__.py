"""Vexa transcription service — faster-whisper behind an OpenAI-compatible audio API.

GPU inference is expensive, stateful, and hardware-specific. This brick isolates the
model + CUDA runtime behind `/v1/audio/transcriptions`, so the meeting pipeline (the bot's
whisper client) — and any OpenAI-audio-compatible client — sends audio and gets text back
without managing a GPU. It is deployed SEPARATELY from the main compose stack (the GPU
workload is carved out); the stack reaches it via `TRANSCRIPTION_SERVICE_URL`.
"""
from transcription.main import app

__all__ = ["app"]
