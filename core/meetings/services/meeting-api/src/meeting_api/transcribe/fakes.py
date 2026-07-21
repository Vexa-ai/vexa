"""In-process fakes satisfying the transcribe ports: offline drivers for the app factory
and the conformance harness (no STT service, no network), like every sibling module's fakes."""
from __future__ import annotations

from typing import Optional


class FakeSttTranscriber:
    """A canned verbose_json responder. ``calls`` records (audio, language) per request;
    set ``fault`` to raise a typed fault instead."""

    def __init__(self, result: Optional[dict] = None, fault: Optional[Exception] = None):
        self.result = result if result is not None else {
            "text": "", "language": "en", "duration": 0.0, "segments": [],
        }
        self.fault = fault
        self.calls: list[tuple[bytes, Optional[str]]] = []

    async def transcribe(self, audio: bytes, *, language: Optional[str] = None) -> dict:
        self.calls.append((audio, language))
        if self.fault is not None:
            raise self.fault
        return self.result
