"""Join-retry as a control-plane re-spawn (P3d).

On a **TRANSIENT** join-failure the control plane schedules a FRESH bot as a NEW ``meeting_session``
via the runtime scheduler (reusing the proven exponential-backoff + bounded-attempt machinery), then
stops. On a **PERMANENT** reason there is NO retry — the meeting goes straight to ``failed``.

The taxonomy is DERIVED FROM the sealed lifecycle.v1 ``CompletionReason`` values (the plan's P3d
classification — NB: the PARENT meeting-api has no join-retry; this is NEW control-plane behaviour,
modelled on the parent's closest precedent, ``post_meeting.AggregationFailureClass``'s
transient/permanent split):

  * **TRANSIENT (a recoverable failure SHAPE)**: ``awaiting_admission_timeout``, ``join_failure``
    (network / transient error). NB since #1190 a transient shape is a *candidate* for retry, not a
    decision — see the two gates below.
  * **PERMANENT → no retry → failed**: ``awaiting_admission_rejected``, ``evicted``,
    ``validation_error``, ``max_bot_time_exceeded``, ``auth_session_missing`` (a re-spawn hits the
    same signed-out profile), and the user terminal ``stopped``.

Bounded to a few attempts (config, default 3). Each attempt is its OWN ``meeting_session`` (a fresh
``connectionId``) — the scheduler fires a ``POST /bots`` re-spawn request for the next attempt.

**Two things sit BETWEEN the sealed taxonomy above and an actual re-spawn** (#1190), because the
sealed label alone is not a retry decision:

  * **The redirect trap.** All 56 ``teams_auth_redirect`` failures in six weeks land as
    ``completion_reason = join_failure`` — TRANSIENT by the sealed taxonomy, and a hard tenant
    policy in fact. Retrying them 3× re-spawns against a wall that will never move. #1075's typed
    join evidence already separates them (``JoinFailureReason.NAVIGATION_FAILURE``: the bot never
    reached the meeting page), so the decision CONSULTS that evidence rather than re-deriving it —
    see :data:`PERMANENT_EVIDENCE_REASONS`.
  * **The admission-timeout arm.** ``awaiting_admission_timeout`` is a transient FAILURE SHAPE but a
    permanent POLICY: the bot already stood at the door for the whole lobby budget, and re-knocking
    doubles quota burn for nothing (founder ruling 2026-08-17). It is retried only when
    :attr:`RetryPolicy.retry_admission_timeout` is explicitly turned on — default OFF.

So ``classify_retry`` / ``is_transient`` stay what they always were, the FAILURE-SHAPE taxonomy, and
:func:`retry_decision` is the single retryability AUTHORITY the controller obeys: taxonomy, then the
policy arm, then the evidence guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional

from .join_evidence import JoinFailureReason
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


#: Typed #1075 join-evidence reasons that make a re-spawn POINTLESS whatever the sealed
#: ``CompletionReason`` says. The evidence axis is diagnostic by design, so this list is short and
#: each entry names a wall a fresh session hits identically:
#:
#:   * ``NAVIGATION_FAILURE`` — the bot never reached the meeting page at all. THE REDIRECT TRAP:
#:     ``teams_auth_redirect`` is one of ``join_evidence._NAVIGATION_MARKERS``, so every one of the
#:     56 six-week failures classifies here while carrying a transient sealed ``join_failure``.
#:   * ``PLATFORM_REJECTION`` — someone/something on the far side said no.
#:   * ``AUTH_SESSION_MISSING`` — the browser profile is signed out; a re-spawn restores the same one.
#:   * ``STOPPED_BEFORE_ADMISSION`` — the USER ended the run. Never auto-respawn a user's own stop.
#:
#: ``NEVER_REACHED_LOBBY`` and ``UNKNOWN`` are deliberately NOT here: they are the selector-rot and
#: genuinely-unknown populations a fresh session can still win, and they are why wiring the retry has
#: value at all.
PERMANENT_EVIDENCE_REASONS: frozenset[JoinFailureReason] = frozenset(
    {
        JoinFailureReason.NAVIGATION_FAILURE,
        JoinFailureReason.PLATFORM_REJECTION,
        JoinFailureReason.AUTH_SESSION_MISSING,
        JoinFailureReason.STOPPED_BEFORE_ADMISSION,
    }
)


def evidence_reason(evidence: Any) -> Optional[JoinFailureReason]:
    """The typed #1075 reason out of a persisted ``data['join_evidence']`` block (or a bare enum).

    Total: an absent block, a foreign shape, or a reason string this control plane does not know
    yields ``None`` — the decision then rests on the sealed taxonomy alone, exactly as before #1190.
    """
    if isinstance(evidence, JoinFailureReason):
        return evidence
    if isinstance(evidence, Mapping):
        raw = evidence.get("reason")
    elif isinstance(evidence, str):
        raw = evidence
    else:
        return None
    try:
        return JoinFailureReason(raw)
    except ValueError:
        return None


@dataclass
class RetryPolicy:
    """Bounded exponential backoff (mirrors the scheduler's Retry shape) + the policy arms."""

    max_attempts: int = 3
    backoff: List[float] = field(default_factory=lambda: [30.0, 120.0, 300.0])
    #: Founder ruling 2026-08-17 — a lobby expiry is NOT retried by default. The bot already spent
    #: the whole ``bot_spawn.service.LOBBY_BUDGET_MS`` standing at the door; re-knocking doubles the
    #: quota burn against a host who did not answer the first time. Turning it on is an explicit act
    #: (``JOIN_RETRY_ADMISSION_TIMEOUT=true``), never a default.
    retry_admission_timeout: bool = False

    def delay_for(self, attempt: int) -> float:
        """Backoff for the 1-indexed ``attempt`` (the last entry is reused past its length)."""
        idx = max(0, min(attempt - 1, len(self.backoff) - 1))
        return self.backoff[idx]


def retry_decision(
    reason: Optional[CompletionReason],
    *,
    evidence: Any = None,
    policy: Optional[RetryPolicy] = None,
) -> RetryClass:
    """THE retryability authority (#1190). Three gates, each of which can only say PERMANENT:

    1. **the sealed taxonomy** — :func:`classify_retry`; anything not positively transient stops here;
    2. **the admission-timeout arm** — a lobby expiry, named EITHER by the sealed reason or by the
       typed evidence (a ~13min ``join_failure`` whose evidence reads ``admission_timeout`` is the
       same event under a different label), is permanent unless the policy explicitly enables it;
    3. **the evidence guard** — :data:`PERMANENT_EVIDENCE_REASONS`, which is what closes the redirect
       trap.

    Fail-safe in the same direction as ``classify_retry``: every unusable input degrades to
    PERMANENT-or-unchanged, never to "retry harder".
    """
    policy = policy or RetryPolicy()
    if classify_retry(reason) is RetryClass.PERMANENT:
        return RetryClass.PERMANENT
    typed = evidence_reason(evidence)
    if not policy.retry_admission_timeout and (
        reason is CompletionReason.AWAITING_ADMISSION_TIMEOUT
        or typed is JoinFailureReason.ADMISSION_TIMEOUT
    ):
        return RetryClass.PERMANENT
    if typed in PERMANENT_EVIDENCE_REASONS:
        return RetryClass.PERMANENT
    return RetryClass.TRANSIENT


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
    with a join-failure reason. If :func:`retry_decision` says TRANSIENT and we are under the attempt
    cap, it schedules a fresh re-spawn (a new ``meeting_session``) at ``now + backoff`` and returns
    ``scheduled_retry``; if the cap is hit it returns ``exhausted``; a PERMANENT decision returns
    ``permanent`` (no schedule). The scheduler + clock are injected (FakeClock + capture in the eval).

    ``respawn_request`` may be left ``None`` when every call supplies its own ``request_builder``
    (the composition root's shape: the builder closes over the terminal meeting row).
    """

    def __init__(
        self,
        scheduler,
        respawn_request: Optional[RespawnRequestBuilder] = None,
        *,
        policy: Optional[RetryPolicy] = None,
    ) -> None:
        self._scheduler = scheduler
        self._respawn_request = respawn_request
        self.policy = policy or RetryPolicy()

    def on_join_failure(
        self,
        meeting_id: int,
        reason: Optional[CompletionReason],
        attempt: int,
        *,
        evidence: Any = None,
        request_builder: Optional[RespawnRequestBuilder] = None,
    ) -> RetryOutcome:
        """Decide + (if transient) schedule the next attempt.

        ``evidence`` is the meeting's persisted ``data['join_evidence']`` block (#1075) — the
        redirect trap is closed by consulting it here, see :func:`retry_decision`.
        ``request_builder`` overrides the constructor's builder for THIS call, so a composition root
        can build a re-spawn request out of the terminal meeting row it is holding rather than
        keeping per-meeting state on the controller.
        """
        reason_v = reason.value if reason is not None else None
        if retry_decision(reason, evidence=evidence, policy=self.policy) is RetryClass.PERMANENT:
            return RetryOutcome(action="permanent", attempt=attempt, reason=reason_v)

        next_attempt = attempt + 1
        if next_attempt >= self.policy.max_attempts:
            # The original spawn is attempt 0; we allow up to max_attempts total tries.
            return RetryOutcome(action="exhausted", attempt=attempt, reason=reason_v)

        delay = self.policy.delay_for(next_attempt)
        now = self._scheduler.clock.now()
        execute_at = now + delay
        build = request_builder or self._respawn_request
        if build is None:
            raise ValueError(
                "JoinRetryController needs a respawn_request builder (constructor or per-call)"
            )
        job = self._scheduler.schedule(
            {
                "execute_at": execute_at,
                "request": build(meeting_id, next_attempt),
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
