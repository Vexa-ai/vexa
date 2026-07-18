"""Join-retry as a control-plane re-spawn (P3d).

On a **TRANSIENT** join-failure the control plane schedules a FRESH bot as a NEW ``meeting_session``
via the runtime scheduler (reusing the proven exponential-backoff + bounded-attempt machinery), then
stops. On a **PERMANENT** reason there is NO retry — the meeting goes straight to ``failed``.

The taxonomy is DERIVED FROM the sealed lifecycle.v1 ``CompletionReason`` values (the plan's P3d
classification — NB: the PARENT meeting-api has no join-retry; this is NEW control-plane behaviour,
modelled on the parent's closest precedent, ``post_meeting.AggregationFailureClass``'s
transient/permanent split):

  * **TRANSIENT → retry**: ``awaiting_admission_timeout``, ``join_failure`` (network / transient error).
  * **PERMANENT → no retry → failed**: ``awaiting_admission_rejected``, ``evicted``,
    ``validation_error``, ``max_bot_time_exceeded``, ``auth_session_missing`` (a re-spawn hits the
    same signed-out profile), and the user terminal ``stopped``.

Bounded to a few attempts (config, default 3). Each attempt is its OWN ``meeting_session`` (a fresh
``connectionId``) — the scheduler fires a ``POST /bots`` re-spawn request for the next attempt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .machine import CompletionReason


class RetryClass(str, Enum):
    """How a join-failure reason is treated: retry (transient) or fail (permanent)."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"


# The P3d taxonomy, keyed by the sealed lifecycle.v1 CompletionReason.
_TRANSIENT: frozenset[CompletionReason] = frozenset(
    {
        CompletionReason.AWAITING_ADMISSION_TIMEOUT,
        CompletionReason.JOIN_FAILURE,
    }
)
_PERMANENT: frozenset[CompletionReason] = frozenset(
    {
        CompletionReason.AWAITING_ADMISSION_REJECTED,
        CompletionReason.EVICTED,
        CompletionReason.VALIDATION_ERROR,
        CompletionReason.MAX_BOT_TIME_EXCEEDED,
        CompletionReason.STOPPED,        # user stop is terminal — never retried
        CompletionReason.AUTH_SESSION_MISSING,  # signed-out profile — a re-spawn hits the same dead profile
        CompletionReason.STARTUP_ALONE,  # alone-on-start is a real outcome, not a transient fault
        CompletionReason.LEFT_ALONE,     # a normal completion, not a failure
    }
)


def classify_retry(reason: Optional[CompletionReason]) -> RetryClass:
    """Map a CompletionReason to its retry class. Unknown / None → PERMANENT (fail-safe: never
    retry something we cannot positively class as transient)."""
    if reason in _TRANSIENT:
        return RetryClass.TRANSIENT
    return RetryClass.PERMANENT


def is_transient(reason: Optional[CompletionReason]) -> bool:
    return classify_retry(reason) is RetryClass.TRANSIENT


@dataclass
class RetryPolicy:
    """Bounded exponential backoff (mirrors the scheduler's Retry shape)."""

    max_attempts: int = 3
    backoff: List[float] = field(default_factory=lambda: [30.0, 120.0, 300.0])

    def delay_for(self, attempt: int) -> float:
        """Backoff for the 1-indexed ``attempt`` (the last entry is reused past its length)."""
        idx = max(0, min(attempt - 1, len(self.backoff) - 1))
        return self.backoff[idx]


@dataclass
class RetryOutcome:
    """The result of handling one terminal join-failure for a meeting."""

    action: str           # "scheduled_retry" | "exhausted" | "permanent"
    attempt: int          # the attempt number just consumed (0 = the original spawn)
    reason: Optional[str]
    next_at: Optional[float] = None   # when the scheduled retry will fire (clock seconds)
    job_id: Optional[str] = None


# A re-spawn request builder: given (meeting_id, attempt) returns the schedule.v1 `request` dict the
# scheduler fires (a POST /bots re-spawn that mints a NEW session). Injected so the eval captures it.
RespawnRequestBuilder = Callable[[int, int], Dict[str, Any]]


class JoinRetryController:
    """Drive bounded join-retries through the runtime scheduler.

    ``on_join_failure(meeting_id, reason, attempt)`` is called when a meeting terminates ``failed``
    with a join-failure reason. If the reason is TRANSIENT and we are under the attempt cap, it
    schedules a fresh re-spawn (a new ``meeting_session``) at ``now + backoff`` and returns
    ``scheduled_retry``; if the cap is hit it returns ``exhausted``; a PERMANENT reason returns
    ``permanent`` (no schedule). The scheduler + clock are injected (FakeClock + capture in the eval).
    """

    def __init__(
        self,
        scheduler,
        respawn_request: RespawnRequestBuilder,
        *,
        policy: Optional[RetryPolicy] = None,
    ) -> None:
        self._scheduler = scheduler
        self._respawn_request = respawn_request
        self.policy = policy or RetryPolicy()

    def on_join_failure(
        self, meeting_id: int, reason: Optional[CompletionReason], attempt: int
    ) -> RetryOutcome:
        reason_v = reason.value if reason is not None else None
        if not is_transient(reason):
            return RetryOutcome(action="permanent", attempt=attempt, reason=reason_v)

        next_attempt = attempt + 1
        if next_attempt >= self.policy.max_attempts:
            # The original spawn is attempt 0; we allow up to max_attempts total tries.
            return RetryOutcome(action="exhausted", attempt=attempt, reason=reason_v)

        delay = self.policy.delay_for(next_attempt)
        now = self._scheduler.clock.now()
        execute_at = now + delay
        job = self._scheduler.schedule(
            {
                "execute_at": execute_at,
                "request": self._respawn_request(meeting_id, next_attempt),
                "metadata": {
                    "kind": "join_retry",
                    "meeting_id": meeting_id,
                    "attempt": next_attempt,
                    "reason": reason_v,
                },
                "idempotency_key": f"join_retry:{meeting_id}:{next_attempt}",
            }
        )
        return RetryOutcome(
            action="scheduled_retry",
            attempt=attempt,
            reason=reason_v,
            next_at=execute_at,
            job_id=job["job_id"],
        )
