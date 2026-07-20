"""Durable, signed engine-to-Hub lifecycle and metering callbacks."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Callable
from urllib.parse import urlparse

from .ports import CallbackEvent, Capture, ControlStore


_TERMINAL = {"completed", "failed"}
_FAILURE_CODES = {
    "join_denied", "kicked", "meeting_ended_early", "quota_exhausted",
    "invalid_meeting", "capture_timeout", "upstream_unavailable", "internal_failure",
}
# The sealed zaki-control.v1 lifecycle graph (contract README, "Capture and consent").  This is
# the ONE authority for legal successors: skipped joins and post-terminal moves are rejected here
# rather than being silently written by a store UPDATE.
_ADJACENCY: dict[str, tuple[str, ...]] = {
    "requested": ("joining", "failed"),
    "joining": ("awaiting_admission", "active", "failed"),
    "awaiting_admission": ("active", "failed"),
    "active": ("stopping", "completed", "failed"),
    "stopping": ("completed", "failed"),
    "completed": (),
    "failed": (),
}
_LIFECYCLE_STATES = frozenset(_ADJACENCY)


def _legal_path(current: str, target: str) -> tuple[str, ...] | None:
    """Shortest legal walk from ``current`` to ``target``, excluding ``current``.

    Breadth-first over ``_ADJACENCY`` with successors visited in contract order, so the path a
    recovery materializes is deterministic. Returns ``()`` when already at the target and ``None``
    when the target is unreachable — which is exactly the post-terminal and skipped-join cases.
    """
    if current == target:
        return ()
    if current not in _ADJACENCY or target not in _ADJACENCY:
        return None
    frontier: list[tuple[str, tuple[str, ...]]] = [(current, ())]
    seen = {current}
    while frontier:
        state, path = frontier.pop(0)
        for successor in _ADJACENCY[state]:
            if successor in seen:
                continue
            walked = path + (successor,)
            if successor == target:
                return walked
            seen.add(successor)
            frontier.append((successor, walked))
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _callback_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/api/minutes/callback/v1"
    ):
        raise ValueError("Minutes callback URL must be an exact HTTP(S) callback endpoint")
    return value


class ControlCallbackDispatcher:
    """Outbox-backed callback dispatcher; duplicate delivery is safe by contract event ID."""

    def __init__(
        self,
        store: ControlStore,
        *,
        callback_url: str,
        hmac_key: str,
        now: Callable[[], datetime] = _utc_now,
    ):
        if not isinstance(hmac_key, str) or len(hmac_key) < 32:
            raise ValueError("Minutes callback HMAC key is not configured")
        self._store = store
        self._callback_url = _callback_url(callback_url)
        self._key = hmac_key.encode()
        self._now = now

    def _envelope(
        self,
        *,
        event_id: str,
        event_type: str,
        data: dict,
        capture: Capture,
        terminal: bool = False,
    ) -> CallbackEvent:
        return CallbackEvent(
            event_id=event_id,
            body={
                "event_id": event_id,
                "event_type": event_type,
                "api_version": "zaki-control.v1",
                "created_at": self._now().astimezone(timezone.utc).isoformat(),
                "data": data,
            },
            subject=capture.subject,
            capture_id=capture.capture_id,
            terminal=terminal,
        )

    async def record_capture_status(
        self,
        capture: Capture,
        *,
        state: str,
        failure_code: str | None = None,
    ) -> None:
        """Record ONE lifecycle advance that is adjacent to the capture's current state.

        A non-adjacent target — a skipped join, a backwards move, or any successor of a terminal
        state — is refused here.  The store's UPDATE is unconditional by design, so this guard is
        what keeps an out-of-contract transition from ever being written.
        """
        if state not in _LIFECYCLE_STATES:
            return
        if capture.meeting_id is None:
            return
        if state != capture.state and state not in _ADJACENCY.get(capture.state, ()):
            return
        await self._emit_capture_status(capture, state=state, failure_code=failure_code)

    async def _emit_capture_status(
        self,
        capture: Capture,
        *,
        state: str,
        failure_code: str | None = None,
    ) -> None:
        """Write one transition whose legality the caller has already established."""
        if state == "failed":
            failure_code = failure_code if failure_code in _FAILURE_CODES else "internal_failure"
        else:
            failure_code = None
        status_data = {
            "subject": {"tenant_id": capture.subject.tenant_id, "user_id": capture.subject.user_id},
            "operation_id": capture.operation_id,
            "capture_id": capture.capture_id,
            "meeting_id": capture.meeting_id,
            "state": state,
        }
        if failure_code is not None:
            status_data["failure_code"] = failure_code
        events = [
            self._envelope(
                event_id=f"status-{capture.capture_id}-{state}",
                event_type="minutes.capture.status",
                data=status_data,
                capture=capture,
                terminal=state in _TERMINAL,
            )
        ]
        if state in _TERMINAL:
            # ponytail: one terminal usage event per capture, sequence fixed at 1. The sealed
            # settlement rules are cumulative-total based, so a single terminal event carrying the
            # final total is a valid stream and the Hub settles it exactly once. Mid-capture
            # progress billing would need a periodic emitter: give the store a per-capture usage
            # counter and emit non-terminal events from the TTL worker tick, keeping `sequence`
            # monotonic and `captured_seconds_total` non-decreasing.
            events.append(
                self._envelope(
                    event_id=f"usage-{capture.capture_id}-1",
                    event_type="minutes.capture.usage",
                    data={
                        "subject": {"tenant_id": capture.subject.tenant_id, "user_id": capture.subject.user_id},
                        "operation_id": capture.operation_id,
                        "capture_id": capture.capture_id,
                        "meeting_id": capture.meeting_id,
                        "metering": {
                            "reservation_id": capture.reservation_id,
                            "sequence": 1,
                            "captured_seconds_total": max(0, capture.captured_seconds_total),
                            "terminal": True,
                        },
                    },
                    capture=capture,
                    terminal=True,
                )
            )
        # The status transition and all callbacks that make it externally observable are one
        # transaction. A crash either leaves both for reconciliation or neither; it can no longer
        # ACK a bot lifecycle event after only half a terminal settlement was persisted.
        await self._store.record_capture_transition(
            capture=capture,
            state=state,
            failure_code=failure_code,
            events=tuple(events),
        )

    async def record_lifecycle(self, meeting_row: dict, *, state: str) -> None:
        meeting_id = meeting_row.get("id") if isinstance(meeting_row, dict) else None
        if meeting_id is None:
            return
        capture = await self._store.get_capture_for_meeting(str(meeting_id))
        if capture is None:
            return
        data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
        failure = data.get("failure_code") or data.get("failure_stage")
        await self.record_capture_status(capture, state=state, failure_code=failure)

    async def reconcile_capture_lifecycle(self, meeting_row: dict) -> None:
        """Backfill missed lifecycle callbacks after recovering a pre-mapping crash.

        Hub initializes a newly persisted capture at ``requested``.  If a bot advanced while the
        engine's control mapping was unavailable, replay the minimal valid path from that state
        rather than sending a late state that Hub correctly rejects as a transition skip.  Event
        IDs are deterministic, so already-delivered lifecycle steps stay idempotent.
        """
        meeting_id = meeting_row.get("id") if isinstance(meeting_row, dict) else None
        if meeting_id is None:
            return
        capture = await self._store.get_capture_for_meeting(str(meeting_id))
        if capture is None:
            return
        state = meeting_row.get("status")
        if not isinstance(state, str) or state == "requested":
            return
        data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
        failure = data.get("failure_code") or data.get("failure_stage")
        for step in _RECOVERY_STEPS.get(state, ()):
            await self.record_capture_status(
                capture,
                state=step,
                failure_code=failure if step == "failed" else None,
            )

    async def reconcile_once(self, *, limit: int = 50) -> int:
        """Restart-safe reconciliation for a bot callback that beat control-map binding."""
        meetings = await self._store.capture_meetings_needing_reconciliation(limit=limit)
        for meeting in meetings:
            await self.reconcile_capture_lifecycle(meeting)
        return len(meetings)

    async def record_capture_timeline(
        self,
        capture: Capture,
        *,
        state: str,
        failure_code: str | None = None,
    ) -> None:
        """Materialize the minimal legal Hub path from the capture's CURRENT state to ``state``.

        This is used for pre-bind reconciliation and erasure withdrawal alike. It is deterministic
        by event ID, so a retry only fills a missing state/outbox pair and never double-settles.

        The walk starts where the capture actually is rather than from a fixed prefix: replaying a
        hardcoded head would drive an already-`stopping` capture back through `joining`, an edge the
        sealed graph forbids.  An unreachable target (post-terminal, or a state the graph cannot
        arrive at) records nothing.
        """
        if capture.meeting_id is None or state not in _LIFECYCLE_STATES:
            return
        path = _legal_path(capture.state, state)
        if path is None:
            return
        for step in path:
            await self._emit_capture_status(
                capture,
                state=step,
                failure_code=failure_code if step == "failed" else None,
            )

    async def drain_once(self, *, limit: int = 50, capture_id: str | None = None) -> int:
        """Deliver a bounded outbox batch.  A non-acknowledging 2xx is retryable, not success."""
        import httpx

        delivered = 0
        for event in await self._store.pending_callbacks(limit=limit, capture_id=capture_id):
            raw = json.dumps(event.body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            timestamp = str(int(self._now().timestamp()))
            signature = hmac.new(self._key, f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                    response = await client.post(
                        self._callback_url,
                        content=raw,
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Timestamp": timestamp,
                            "X-Webhook-Signature": f"sha256={signature}",
                        },
                    )
                body = response.json() if 200 <= response.status_code < 300 else None
                if not (
                    isinstance(body, dict)
                    and body.get("api_version") == "zaki-control.v1"
                    and body.get("event_id") == event.event_id
                    and body.get("status") in {"accepted", "duplicate"}
                ):
                    raise RuntimeError("Hub did not acknowledge the sealed callback event")
            except Exception:
                await self._store.mark_callback_failed(event.event_id)
                continue
            await self._store.mark_callback_delivered(event.event_id)
            delivered += 1
        return delivered

    async def drain_capture_terminal(self, capture_id: str) -> bool:
        """Try the bounded terminal settlement batch before irreversible erasure proceeds."""
        await self.drain_once(limit=50, capture_id=capture_id)
        return await self._store.terminal_callbacks_delivered(capture_id)
