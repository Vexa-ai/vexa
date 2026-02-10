import struct
from typing import Optional


def float32_to_pcm16(f32_chunk: bytes) -> bytes:
    """Convert Float32 (-1..1) to PCM 16-bit little-endian."""
    n = len(f32_chunk) // 4
    floats = struct.unpack(f"<{n}f", f32_chunk)
    pcm = []
    for f in floats:
        s = max(-1.0, min(1.0, f))
        pcm.append(int(s * 32767))
    return struct.pack(f"<{n}h", *pcm)


def language_from_config(lang: Optional[str], default_language: str) -> str:
    """Normalize language code (e.g. 'en' -> 'en-US')."""
    if not lang or not lang.strip():
        return default_language
    lang = lang.strip()
    if len(lang) == 2:
        return f"{lang}-{lang.upper()}"
    return lang if "-" in lang else f"{lang}-{lang.upper()}"
