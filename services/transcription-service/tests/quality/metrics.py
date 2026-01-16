from __future__ import annotations

import re

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+", flags=re.UNICODE)


def normalize_text(text: str) -> str:
    """Normalize for WER/CER comparisons (simple, multilingual-friendly)."""
    text = (text or "").strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _edit_distance(a: list[str], b: list[str]) -> int:
    """Levenshtein distance between token lists."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # DP with O(min(n,m)) space
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, tok_a in enumerate(a, start=1):
        cur = [i]
        for j, tok_b in enumerate(b, start=1):
            cost = 0 if tok_a == tok_b else 1
            cur.append(min(
                prev[j] + 1,      # deletion
                cur[j - 1] + 1,   # insertion
                prev[j - 1] + cost,  # substitution
            ))
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    ref_words = ref.split() if ref else []
    hyp_words = hyp.split() if hyp else []
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _edit_distance(ref_words, hyp_words) / max(1, len(ref_words))


def cer(reference: str, hypothesis: str) -> float:
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    ref_chars = list(ref)
    hyp_chars = list(hyp)
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _edit_distance(ref_chars, hyp_chars) / max(1, len(ref_chars))











