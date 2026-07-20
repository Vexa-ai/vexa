"""Ports and value objects for the ZAKI Minutes control boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


Operation = Literal["ensure", "capture", "stop_capture", "erase_meeting", "erase_account"]


@dataclass(frozen=True)
class Subject:
    tenant_id: str
    user_id: str


@dataclass(frozen=True)
class Policy:
    capture_enabled: bool
    agent_read_enabled: bool
    policy_version: str
    audio_days: int
    transcript_days: int
    summary_days: int


@dataclass(frozen=True)
class Capture:
    capture_id: str
    subject: Subject
    operation_id: str
    reservation_id: str
    platform: str
    native_meeting_id: str
    meeting_id: str | None
    state: str
    failure_code: str | None = None
    captured_seconds_total: int = 0
    # The control store preserves enough non-content timing/cap evidence to settle a capture even
    # when erasure removes its meeting row immediately after withdrawal.
    max_capture_seconds: int = 0
    started_at: datetime | None = None


@dataclass(frozen=True)
class ErasureTarget:
    """A controlled meeting that must be stopped before its retained data is erased.

    ``capture_id`` is absent only for the narrow crash-recovery case where the meeting row was
    created but its control mapping was not yet committed.  It is still tenant-bound and carries
    enough identity to close the writer barrier before retention deletion.
    """

    meeting_id: str
    subject: Subject
    platform: str
    native_meeting_id: str
    state: str
    capture_id: str | None = None


@dataclass(frozen=True)
class OperationClaim:
    state: Literal["new", "retry", "replay", "conflict", "pending"]
    operation_id: str
    response: dict | None = None
    # A lease generation fences an executor which resumed after another worker reclaimed an
    # expired idempotency lease. Every durable completion/progress write must carry this value.
    fence: int = 0
    progress: dict | None = None


@dataclass(frozen=True)
class CallbackEvent:
    event_id: str
    body: dict
    subject: Subject | None = None
    capture_id: str | None = None
    terminal: bool = False


class ControlStore(Protocol):
    """Durable control state with a single-owner execution lease per idempotency key."""

    async def ensure_schema(self) -> None: ...

    async def claim_operation(
        self,
        *,
        subject: Subject,
        operation: Operation,
        idempotency_key: str,
        request_sha256: str,
        operation_id: str,
    ) -> OperationClaim: ...

    async def lookup_operation(
        self,
        *,
        subject: Subject,
        operation: Operation,
        idempotency_key: str,
        request_sha256: str,
    ) -> OperationClaim | None: ...

    async def complete_operation(
        self,
        *,
        subject: Subject,
        operation: Operation,
        idempotency_key: str,
        response: dict,
        fence: int,
    ) -> None: ...

    async def save_operation_progress(
        self,
        *,
        subject: Subject,
        operation: Operation,
        idempotency_key: str,
        fence: int,
        progress: dict,
    ) -> None: ...

    async def assert_operation_fence(
        self,
        *,
        subject: Subject,
        operation: Operation,
        idempotency_key: str,
        fence: int,
    ) -> None: ...

    async def get_policy(self, subject: Subject) -> Policy | None: ...

    async def put_policy(self, subject: Subject, policy: Policy) -> bool: ...

    async def subject_is_erasing(self, subject: Subject) -> bool: ...

    async def begin_subject_erasure(
        self, *, subject: Subject, operation_id: str, fence: int
    ) -> bool: ...

    async def finish_subject_erasure(
        self, *, subject: Subject, operation_id: str, fence: int
    ) -> None: ...

    async def create_capture(self, capture: Capture) -> None: ...

    async def bind_capture_meeting(self, *, capture_id: str, meeting_id: str) -> None: ...

    async def get_capture(self, *, subject: Subject, capture_id: str) -> Capture | None: ...

    async def get_capture_by_operation(
        self, *, subject: Subject, operation_id: str
    ) -> Capture | None: ...

    async def get_capture_for_meeting(self, meeting_id: str) -> Capture | None: ...

    async def capture_meetings_needing_reconciliation(self, *, limit: int) -> tuple[dict, ...]: ...

    async def mark_capture_state(
        self, *, capture_id: str, state: str, failure_code: str | None = None
    ) -> None: ...

    async def list_owned_erasure_targets(self, subject: Subject) -> tuple[ErasureTarget, ...]: ...

    async def get_erasure_target(
        self, *, subject: Subject, meeting_id: str
    ) -> ErasureTarget | None: ...

    async def erase_subject_control_data(self, subject: Subject) -> None: ...

    async def record_capture_transition(
        self,
        *,
        capture: Capture,
        state: str,
        failure_code: str | None,
        events: tuple[CallbackEvent, ...],
    ) -> None: ...

    async def pending_callbacks(
        self, *, limit: int, capture_id: str | None = None
    ) -> tuple[CallbackEvent, ...]: ...

    async def terminal_callbacks_delivered(self, capture_id: str) -> bool: ...

    async def finalize_erased_capture(
        self, *, subject: Subject, meeting_id: str
    ) -> None: ...

    async def mark_callback_delivered(self, event_id: str) -> None: ...

    async def mark_callback_failed(self, event_id: str) -> None: ...

