"""48 kHz stereo PCM -> 16 kHz mono WAV, matching the Whisper worker's expected input.

Ported from the bridge's ``bot.py`` ``to_mono_wav``. py-cord decodes Opus to 48 kHz 16-bit stereo
PCM; Vexa's transcription worker feeds WAV samples straight to Whisper assuming 16 kHz, so this
must downsample first — sending 48 kHz stretches speech 3x and the VAD discards it as non-speech.

Uses the stdlib ``audioop`` module — removed in Python 3.13, hence this service (like the bridge
and the rest of ``dave_voice``) is pinned to 3.11.
"""

from __future__ import annotations

import audioop  # stdlib on py3.11 (removed in 3.13 — keep this service's runtime at 3.11)
import io
import wave

SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2
BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
OUT_RATE = 16_000


def to_mono_wav(pcm_stereo: bytes, *, sample_rate: int = SAMPLE_RATE, out_rate: int = OUT_RATE) -> bytes:
    """Downmix 48 kHz stereo PCM to mono, resample to ``out_rate``, and wrap as a WAV file."""
    mono = audioop.tomono(pcm_stereo, SAMPLE_WIDTH, 0.5, 0.5)
    mono_out, _ = audioop.ratecv(mono, SAMPLE_WIDTH, 1, sample_rate, out_rate, None)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(out_rate)
        w.writeframes(mono_out)
    return buf.getvalue()
