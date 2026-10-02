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


@dataclass
class NotPresent:
    """A DOMAIN THIS STEP NEEDS IS NOT DEPLOYED — terminal, and not a failure (PRD decision 40.7).

    *"We want agents service be optional, all domains must work independently and in any
    configuration… meetings, agents and flows — independently and together in any configuration."*
    A `no-agents` deployment (decision 40.6: gateway + meetings + flows + identity) still receives
    meeting-completed facts and still runs the post-meeting flow; the steps in it that would
    dispatch an agent turn have nothing to dispatch to.

    Why it is its own result rather than any of the three that existed:

    * not `StepError` — nothing is broken, and a retryable error would knock on the missing door
      every ten minutes forever, which is the exact shape 40.7 exists to remove;
    * not `Block` — a block waits for a human or an external signal to arrive, and no signal is
      coming: the domain is absent by deployment, not by timing;
    * not a bare `Done` — an outcome that reads as success is a SILENT SKIP, and an operator
      looking at a completed reaction could not tell that the report was never written.

    So: the reaction reaches `done` (nothing retries, nothing is left leased), and it carries
    `reason` saying which domain was absent — which is what puts it in front of a person through
    the same projection every other reason travels on."""

    domain: str
    detail: str = ""
    result: dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        base = f"{self.domain}:not_present"
        return f"{base} — {self.detail}" if self.detail else base

    def receipt(self) -> dict:
        """What the effect receipt records: the outcome is data, not prose to be re-parsed."""
        return {"outcome": "not_present", "domain": self.domain,
                **({"detail": self.detail} if self.detail else {}), **self.result}


class StepError(Exception):
    """A retryable step failure: backoff via next_run_at, bounded by max attempts."""

    def __init__(self, detail: str, *, retryable: bool = True) -> None:
        super().__init__(detail)
        self.retryable = retryable


def _no_checkpoint() -> None:
    """The default `checkpoint`: a step called OUTSIDE the loop — which every unit test does — has
    no lease to renew and no row to save into, and must still run unchanged."""
    return None


@dataclass
class StepCtx:
    """Everything a step sees: the reaction's refs, its effect key, prior step results,
    a DURABLE scratch dict (persisted after every step — survives worker restarts),
    emit() to publish a new fact (sub-flow composition), and checkpoint() for the steps whose
    body outlives one lease."""
    reaction: Reaction
    effect_key: str
    prior: dict[str, dict]
    clock_now: float
    scratch: dict = field(default_factory=dict)
    emit: Any = None                      # (event_type, source_id, refs) -> int reactions created
    flow: Any = None                      # the governing Flow (params via ctx.flow.param(key))
    checkpoint: Any = _no_checkpoint      # renew the lease + persist scratch, MID-step

    @property
    def reaction_id(self) -> str:
        """THE REACTION THIS STEP IS RUNNING FOR — read by every `mint_scaffold` call site to
        stamp `provenance.reaction_id`, so a scaffold can be traced back to the run that minted
        it.

        It did not exist. All five call sites read it as `getattr(ctx, "reaction_id", "")`, and a
        getattr WITH A DEFAULT cannot fail: every scaffold this system has ever minted carried an
        empty provenance and nobody could see that from the code, because the expression looks
        exactly like a correct one. `Reaction` spells the field `reaction_id` rather than `id`,
        which is the near-miss the default was quietly absorbing."""
        return str(getattr(self.reaction, "reaction_id", "") or "")

    @property
    def refs(self) -> dict:
        return self.subject_refs

    @property
    def subject_refs(self) -> dict:
        return self.reaction.subject_refs
