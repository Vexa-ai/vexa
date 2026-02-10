import struct
from typing import Optional

FLOAT32_BYTES_PER_SAMPLE = 4
PCM16_MAX_AMPLITUDE = 32767
FLOAT32_MIN_VALUE = -1.0
FLOAT32_MAX_VALUE = 1.0
ISO_LANGUAGE_CODE_LENGTH = 2
LOCALE_SEPARATOR = "-"


def float32_to_pcm16(f32_chunk: bytes) -> bytes:
    """Convert Float32 (-1..1) to PCM 16-bit little-endian."""
    n = len(f32_chunk) // FLOAT32_BYTES_PER_SAMPLE
    floats = struct.unpack(f"<{n}f", f32_chunk)
    pcm = []
    for f in floats:
        s = max(FLOAT32_MIN_VALUE, min(FLOAT32_MAX_VALUE, f))
        pcm.append(int(s * PCM16_MAX_AMPLITUDE))
    return struct.pack(f"<{n}h", *pcm)


def language_from_config(lang: Optional[str], default_language: str) -> str:
    """Normalize language code (e.g. 'en' -> 'en-US')."""
    if not lang or not lang.strip():
        return default_language
    lang = lang.strip()
    if len(lang) == ISO_LANGUAGE_CODE_LENGTH:
        return f"{lang}{LOCALE_SEPARATOR}{lang.upper()}"
    return lang if LOCALE_SEPARATOR in lang else f"{lang}{LOCALE_SEPARATOR}{lang.upper()}"
