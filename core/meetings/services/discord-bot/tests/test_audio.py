"""48kHz stereo -> 16kHz mono WAV downsample (audioop-backed)."""

import wave
from io import BytesIO

from discord_bot.audio import BYTES_PER_SEC, OUT_RATE, to_mono_wav


def test_to_mono_wav_produces_a_valid_16khz_mono_wav():
    # 0.1s of 48kHz stereo 16-bit silence.
    samples = int(0.1 * 48_000)
    pcm = b"\x00\x00" * samples * 2  # 2 channels
    wav_bytes = to_mono_wav(pcm)
    with wave.open(BytesIO(wav_bytes), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == OUT_RATE
        assert w.getsampwidth() == 2


def test_bytes_per_sec_matches_48khz_stereo_16bit():
    assert BYTES_PER_SEC == 48_000 * 2 * 2
