"""The information measure that decides when the agent wakes. Pure functions, no I/O.

Every confirmed line is scored by the signal model: one 0..1 score per brief label. Lines arrive in
batches (what one read of the source returned). For a batch B and the brief's wake labels W:

    batch_score(B) = Σ_{w ∈ W}  max_{line ∈ B} score(line, w)

that is, the strongest evidence for each wake label in the batch, summed over the labels. A batch of
small talk adds ~0; one line that is plainly a decision adds ~1 however many filler lines surround
it; a batch holding a decision AND an action item adds ~2. Per-label max (not sum over lines) is what
keeps a long ramble from creeping over the threshold on many weak hits.

The running score is the sum of batch scores since the agent last woke. It wakes when:

* ``addressed`` — any line scores ``addressed_to_vexa`` ≥ 0.5 (at once, whatever the total);
* ``threshold`` — the running score reaches the brief's ``wake_at``;
* ``max_wait`` — the buffer has lines and ``max_wait_s`` passed since its first line;
* ``end`` — the source ended; the remainder goes to the recap.

A line the caps kept from the model carries no scores and adds 0, but still rides in the buffer.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .brief import ADDRESSED, ADDRESSED_AT, Brief


def batch_score(scores: Iterable[dict], wake_labels: Iterable[str]) -> float:
    rows = [s for s in scores if s]
    total = 0.0
    for label in wake_labels:
        total += max((float(s.get(label, 0.0)) for s in rows), default=0.0)
    return total


def addressed(scores: Iterable[dict]) -> bool:
    return any(float((s or {}).get(ADDRESSED, 0.0)) >= ADDRESSED_AT for s in scores)


def wake_reason(*, scores: list, running: float, brief: Brief, buffered: int,
                since: Optional[float], now: float) -> Optional[str]:
    """Why to wake now, or None. ``scores`` are the batch just scored (may be empty)."""
    if buffered and addressed(scores):
        return "addressed"
    if buffered and running >= brief.wake_at:
        return "threshold"
    if buffered and since is not None and now - since >= brief.max_wait_s:
        return "max_wait"
    return None
