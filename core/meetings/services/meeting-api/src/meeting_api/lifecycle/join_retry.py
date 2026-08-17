"""Join-retry, WIRED (#1190) — the glue between a terminal ``failed`` meeting and a real re-spawn.

``retry.py`` has shipped a tested :class:`~meeting_api.lifecycle.retry.JoinRetryController` since
P3d and it has had **zero instantiations outside its own module and tests**. Prod corroborates the
consequence: every transient ``join_failure`` is one attempt, terminal. This module is the missing
half — the two seams the controller needs to actually run:

  * **the TRIGGER** — :class:`JoinRetryCoordinator`, called by the lifecycle callback the moment the
    FSM lands a pre-active ``failed`` terminal. It reads the decision inputs off the DURABLE meeting
    row (sealed ``completion_reason``, #1075's ``join_evidence``, the attempt counter), asks the
    controller, and persists the new attempt number so the NEXT terminal knows which attempt it was.
  * **the DISPATCH** — :func:`build_respawn_request`, the ``schedule.v1`` request one attempt fires:
    a ``POST /bots`` with ``continue_meeting`` set, which reuses the SAME meeting row (transcripts
    and recordings stay keyed to it) while minting a FRESH ``meeting_session``. That is what "each
    retry is a fresh re-spawn" means concretely, and it is why the attempt counter can live in
    ``meeting.data``: the row survives the retry.

Everything here is offline-drivable: the coordinator takes the controller (itself over an injected
``Scheduler`` + ``Clock``) and the meeting repo as ports, so the composition tests drive the whole
path with a ``FakeClock`` and a capturing dispatch — no bot, no docker, no network.

**Pre-active only.** A meeting that reached ``active`` was ADMITTED; whatever killed it afterwards is
not a join failure and the join taxonomy has nothing truthful to say about it (the same carve
``machine._capture_join_evidence`` makes). The coordinator refuses those rows outright.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..obs import log_event
from .machine import CompletionReason
from .retry import JoinRetryController, RetryOutcome

__all__ = [
    "JOIN_RETRY_DATA_KEY",
    "PRE_ACTIVE_FAILURE_STAGES",
    "JoinRetryCoordinator",
    "build_respawn_request",
]

#: Where the attempt counter lives on the meeting row. It has to be DURABLE and it has to survive
#: the re-spawn, which is exactly what ``continue_meeting``'s row reuse buys: ``reopen_meeting``
#: clears the terminal attribution and keeps everything else, so attempt N is still readable when
#: attempt N's own terminal lands.
JOIN_RETRY_DATA_KEY = "join_retry"

#: Failure stages at which the bot had not yet been admitted — the only rows a JOIN retry is about.
PRE_ACTIVE_FAILURE_STAGES = frozenset({"requested", "joining", "awaiting_admission"})


def build_respawn_request(
    row: Dict[str, Any], attempt: int, *, meeting_api_url: str
) -> Dict[str, Any]:
    """The ``schedule.v1`` ``request`` for one retry attempt: a fresh ``POST /bots`` for this row.

    The URL is the canonical ``POST /bots`` edge so the job is a conformant ``schedule.v1`` request
    and reads truthfully in the job log — but the production dispatch calls ``request_bot`` IN
    PROCESS rather than looping back over HTTP, the same call the reconcile sweep makes for the same
    reason (``app._apply_lifecycle_event``: a 127.0.0.1 self-POST is a fragile hop that buys
    nothing). ``body`` therefore carries every argument that call needs.
    """
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return {
        "method": "POST",
        "url": f"{(meeting_api_url or '').rstrip('/')}/bots",
        "headers": {"x-user-id": str(row.get("user_id"))},
        "body": {
            "meeting_id": row.get("id"),
            "attempt": attempt,
            "user_id": row.get("user_id"),
            "platform": row.get("platform"),
            "native_meeting_id": row.get("native_meeting_id"),
            "meeting_url": (
                row.get("constructed_meeting_url") or data.get("constructed_meeting_url")
            ),
            # THE point of the retry shape: reuse the terminal row, mint a new session.
            "continue_meeting": True,
        },
        "timeout": 30,
    }


def _completion_reason(raw: Any) -> Optional[CompletionReason]:
    """The sealed reason off the row, or ``None`` for anything unrecognized (which classifies
    PERMANENT — never retry what we cannot positively class)."""
    try:
        return CompletionReason(raw)
    except (ValueError, TypeError):
        return None


def _attempt(data: Dict[str, Any]) -> int:
    block = data.get(JOIN_RETRY_DATA_KEY)
    if isinstance(block, dict):
        value = block.get("attempt")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


class JoinRetryCoordinator:
    """Terminal ``failed`` meeting row → at most ``policy.max_attempts`` bounded re-spawns.

    One instance per process, built by the composition root and handed to ``create_app`` as the
    ``join_retry`` port. ``enabled=False`` (``JOIN_RETRY_ENABLED=false``) makes every call a no-op
    that still returns ``None`` — the kill switch is one boolean, not a code path.
    """

    def __init__(
        self,
        controller: JoinRetryController,
        repo: Any,
        *,
        meeting_api_url: str = "http://meeting-api:8080",
        enabled: bool = True,
    ) -> None:
        self._controller = controller
        self._repo = repo
        self._meeting_api_url = meeting_api_url
        self.enabled = enabled

    @property
    def policy(self):
        return self._controller.policy

    async def on_terminal_failed(self, meeting_row: Any) -> Optional[RetryOutcome]:
        """Called once per REAL ``failed`` terminal advance (never on an idempotent replay).

        Returns the :class:`RetryOutcome` when a decision was made, ``None`` when the row is not a
        join failure at all (disabled, post-admission, unknown row). Never raises: a retry is an
        optimization on a run that has already ended, and it must not be able to fail the bot's
        lifecycle callback — the caller wraps it too (belt and braces).
        """
        if not self.enabled or not isinstance(meeting_row, dict):
            return None
        meeting_id = meeting_row.get("id")
        if not isinstance(meeting_id, int):
            return None
        data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
        if data.get("failure_stage") not in PRE_ACTIVE_FAILURE_STAGES:
            return None  # admitted (or unattributed) — not a join failure
        if data.get("stop_requested"):
            return None  # the user ended this run; never auto-respawn their own stop
        reason = _completion_reason(data.get("completion_reason"))
        attempt = _attempt(data)

        def _builder(_meeting_id: int, next_attempt: int) -> Dict[str, Any]:
            return build_respawn_request(
                meeting_row, next_attempt, meeting_api_url=self._meeting_api_url
            )

        outcome = self._controller.on_join_failure(
            meeting_id,
            reason,
            attempt,
            evidence=data.get("join_evidence"),
            request_builder=_builder,
        )
        if outcome.action == "scheduled_retry":
            # Durable, because the NEXT terminal has to know which attempt it was — and because the
            # row is the only thing that survives a re-spawn (the FSM record is per-session).
            await self._repo.merge_meeting_data(
                meeting_id,
                {
                    JOIN_RETRY_DATA_KEY: {
                        "attempt": attempt + 1,
                        "max_attempts": self.policy.max_attempts,
                        "job_id": outcome.job_id,
                        "next_at": outcome.next_at,
                        "reason": outcome.reason,
                    }
                },
            )
        log_event(
            "join_retry_decision",
            audience="system",
            level="info" if outcome.action == "scheduled_retry" else "warning",
            span="lifecycle.join_retry",
            meeting_id=str(meeting_id),
            fields={
                "action": outcome.action,
                "attempt": attempt,
                "completion_reason": outcome.reason,
                "join_evidence_reason": (
                    data.get("join_evidence", {}).get("reason")
                    if isinstance(data.get("join_evidence"), dict)
                    else None
                ),
                "next_at": outcome.next_at,
                "job_id": outcome.job_id,
            },
        )
        return outcome


#: The production dispatch's shape: given the job's ``request``, fire the re-spawn. The scheduler
#: calls it SYNCHRONOUSLY from inside the tick, so the production implementation hands the actual
#: (async) ``request_bot`` call to the running event loop — see ``__main__._attach_background_loops``.
Dispatch = Callable[[Dict[str, Any]], Dict[str, Any]]
