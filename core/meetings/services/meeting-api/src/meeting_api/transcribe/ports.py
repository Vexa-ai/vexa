"""Ports of the transcribe module: the typing.Protocol seams the service flow composes."""
from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol


class SttTranscriber(Protocol):
    """One audio buffer in, one parsed verbose_json dict out; typed TranscribeFault on failure."""

    async def transcribe(self, audio: bytes, *, language: Optional[str] = None) -> dict: ...


#: Resolve a meeting's finalized master audio to raw bytes (None: nothing to transcribe).
MasterResolver = Callable[[int], Awaitable[Optional[bytes]]]
