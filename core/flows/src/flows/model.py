"""Typed rows + step results. Pure data — no I/O, no domain knowledge."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

STATUSES = ("admitted", "running", "blocked", "retrying", "failed", "cancelled", "done")


@dataclass
class Reaction:
    reaction_id: str
    source_event_id: str
    event_type: str
    subject_refs: dict
    flow: str
    flow_version: int
    step: str
    status: str
    attempt: int
    next_run_at: float
    blocked_deadline: Optional[float]
    lease_until: Optional[float]
    reason: Optional[str]


@dataclass
class Receipt:
    effect_key: str
    reaction_id: str
    step: str
    state: str                       # reserved | confirmed | failed
    provider_ref: Optional[str]
    result: dict


# ── step results — what a step implementation may answer ─────────────────────────
@dataclass
class Done:
    result: dict = field(default_factory=dict)
    provider_ref: Optional[str] = None


@dataclass
class Wait:
    """Not ready — come back. Costs nothing while parked; burns NO attempt."""
    seconds: Optional[float] = None
    until: Optional[float] = None


@dataclass
class Block:
    """Needs a human/external signal, or an ambiguous effect. Never guessed past."""
    reason: str
    deadline_s: Optional[float] = None    # relative escalation window → failed


class StepError(Exception):
    """A retryable step failure: backoff via next_run_at, bounded by max attempts."""

    def __init__(self, detail: str, *, retryable: bool = True) -> None:
        super().__init__(detail)
        self.retryable = retryable


@dataclass
class StepCtx:
    """Everything a step sees: the reaction's refs, its effect key, prior step results,
    a DURABLE scratch dict (persisted after every step — survives worker restarts), and
    emit() to publish a new fact (sub-flow composition)."""
    reaction: Reaction
    effect_key: str
    prior: dict[str, dict]
    clock_now: float
    scratch: dict = field(default_factory=dict)
    emit: Any = None                      # (event_type, source_id, refs) -> int reactions created
    flow: Any = None                      # the governing Flow (params via ctx.flow.param(key))

    @property
    def refs(self) -> dict:
        return self.subject_refs

    @property
    def subject_refs(self) -> dict:
        return self.reaction.subject_refs
