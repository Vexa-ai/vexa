"""
Shared hallucination filter for post-transcription filtering.

Used by both WhisperLive (standalone) and the bot (per-speaker pipeline).
Loads known phrases from text files in this directory.

Usage (Python — WhisperLive):
    from hallucinations.filter import is_hallucination
    if is_hallucination(text, compression_ratio=1.9):
        # drop the segment

Usage (TypeScript — bot):
    Import filter.ts from this directory (or copy the logic).
    The phrase files are shared.
"""

import os
import re
from pathlib import Path
from typing import Optional, Set

_hallucinations: Optional[Set[str]] = None


def _load_phrases() -> Set[str]:
    global _hallucinations
    if _hallucinations is not None:
        return _hallucinations

    _hallucinations = set()
    phrases_dir = Path(__file__).parent

    for txt_file in phrases_dir.glob("*.txt"):
        try:
            for line in txt_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    _hallucinations.add(stripped.lower())
        except Exception:
            pass

    return _hallucinations


def is_hallucination(
    text: str,
    compression_ratio: Optional[float] = None,
    no_speech_prob: Optional[float] = None,
    avg_logprob: Optional[float] = None,
) -> bool:
    """
    Check if a transcript is a Whisper hallucination.
    Returns True if the text should be DROPPED.

    Checks:
    1. Known hallucination phrases (exact match, case-insensitive)
    2. Too short to be useful (single word < 10 chars)
    3. Repetition (same 3-6 word phrase repeated 3+ times)
    4. High compression ratio (> 2.0)
    5. No speech + low confidence
    """
    if not text or not text.strip():
        return True

    trimmed = text.strip()
    lower = trimmed.lower()

    # 1. Known phrase
    phrases = _load_phrases()
    if lower in phrases:
        return True

    # 2. Too short
    words = trimmed.split()
    if len(words) <= 1 and len(trimmed) < 10:
        return True

    # 3. Repetition detection
    if len(words) >= 9:
        for phrase_len in range(3, 7):
            phrase = " ".join(words[:phrase_len]).lower()
            count = 0
            for i in range(0, len(words) - phrase_len + 1, phrase_len):
                if " ".join(words[i : i + phrase_len]).lower() == phrase:
                    count += 1
            if count >= 3:
                return True

    # 4. Compression ratio
    if compression_ratio is not None and compression_ratio > 2.0:
        return True

    # 5. No speech + low confidence
    if no_speech_prob is not None and avg_logprob is not None:
        if no_speech_prob > 0.6 and avg_logprob < -1.0:
            return True

    return False
