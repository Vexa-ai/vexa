"""In-process durable-shape fake for the control-router conformance tests."""
from __future__ import annotations

from dataclasses import replace

from .ports import CallbackEvent, Capture, ControlStore, ErasureTarget, OperationClaim, Policy, Subject


class InMemoryControlStore(ControlStore):
    def __init__(self):
        self.policies: dict[tuple[str, str], Policy] = {}
        self.operations: dict[tuple[str, str, str, str], dict] = {}
        self.captures: dict[str, Capture] = {}
        self.callbacks: dict[str, CallbackEvent] = {}
        self.subject_meetings: dict[tuple[str, str], set[str]] = {}
        self.subject_states: dict[tuple[str, str], dict[str, object]] = {}
        self.recovery_targets: dict[tuple[str, str, str], ErasureTarget] = {}
        self._delivered_callbacks: set[str] = set()
        self._terminal_callback_ids: dict[str, set[str]] = {}
        self.reconcile_meetings: list[dict] = []

    async def ensure_schema(self) -> None:
        return None

    async def claim_operation(self, *, subject, operation, idempotency_key, request_sha256, operation_id):
        key = (subject.tenant_id, subject.user_id, operation, idempotency_key)
        prior = self.operations.get(key)
        if prior is None:
            self.operations[key] = {
                "hash": request_sha256, "operation_id": operation_id, "response": None,
                "fence": 1, "progress": None,
            }
            return OperationClaim("new", operation_id, fence=1)
        if prior["hash"] != request_sha256:
            return OperationClaim("conflict", prior["operation_id"], fence=int(prior["fence"]))
        if prior["response"] is None:
            # Tests are serial, so a subsequent call represents recovery after the production
            # store's execution lease has expired rather than a concurrent runner.
            prior["fence"] = int(prior["fence"]) + 1
            return OperationClaim(
                "retry", prior["operation_id"], fence=int(prior["fence"]),
                progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
            )
        return OperationClaim(
            "replay", prior["operation_id"], dict(prior["response"]),
            fence=int(prior["fence"]),
            progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
        )

    async def lookup_operation(self, *, subject, operation, idempotency_key, request_sha256):
        key = (subject.tenant_id, subject.user_id, operation, idempotency_key)
        prior = self.operations.get(key)
        if prior is None:
            return None
        if prior["hash"] != request_sha256:
            return OperationClaim("conflict", prior["operation_id"], fence=int(prior["fence"]))
        if isinstance(prior["response"], dict):
            return OperationClaim(
                "replay", prior["operation_id"], dict(prior["response"]),
                fence=int(prior["fence"]),
                progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
            )
        return OperationClaim(
            "pending", prior["operation_id"], fence=int(prior["fence"]),
            progress=dict(prior["progress"]) if isinstance(prior["progress"], dict) else None,
        )

    async def complete_operation(self, *, subject, operation, idempotency_key, response, fence):
        key = (subject.tenant_id, subject.user_id, operation, idempotency_key)
        if self.operations[key]["fence"] != fence:
            raise RuntimeError("stale control operation fence")
        self.operations[key]["response"] = dict(response)

    async def save_operation_progress(self, *, subject, operation, idempotency_key, fence, progress):
        key = (subject.tenant_id, subject.user_id, operation, idempotency_key)
        if self.operations[key]["fence"] != fence:
            raise RuntimeError("stale control operation fence")
        self.operations[key]["progress"] = dict(progress)

    async def assert_operation_fence(self, *, subject, operation, idempotency_key, fence):
        key = (subject.tenant_id, subject.user_id, operation, idempotency_key)
        if self.operations[key]["fence"] != fence or self.operations[key]["response"] is not None:
            raise RuntimeError("stale control operation fence")

    async def get_policy(self, subject):
        return self.policies.get((subject.tenant_id, subject.user_id))

    async def put_policy(self, subject, policy):
        state = self.subject_states.get((subject.tenant_id, subject.user_id), {}).get("state")
        if state == "erasing":
            return False
        self.policies[(subject.tenant_id, subject.user_id)] = policy
        self.subject_states[(subject.tenant_id, subject.user_id)] = {"state": "active"}
        return True

    async def subject_is_erasing(self, subject):
        return self.subject_states.get((subject.tenant_id, subject.user_id), {}).get("state") == "erasing"

    async def begin_subject_erasure(self, *, subject, operation_id, fence):
        key = (subject.tenant_id, subject.user_id)
        state = self.subject_states.get(key)
        if state and state.get("state") == "erasing" and state.get("operation_id") != operation_id:
            return False
        # A recovered operation owns a newer lease generation.  Move the subject barrier to that
        # generation so the crashed executor cannot finish a deletion after recovery took over.
        self.subject_states[key] = {"state": "erasing", "operation_id": operation_id, "fence": fence}
        return True

    async def finish_subject_erasure(self, *, subject, operation_id, fence):
        key = (subject.tenant_id, subject.user_id)
        state = self.subject_states.get(key)
        if not state or state.get("state") != "erasing" or state.get("operation_id") != operation_id or state.get("fence") != fence:
            raise RuntimeError("stale subject erasure fence")
        self.subject_states[key] = {"state": "erased"}

    async def create_capture(self, capture):
        if await self.subject_is_erasing(capture.subject):
            raise RuntimeError("subject erasure is in progress")
        if capture.capture_id in self.captures:
            raise RuntimeError("capture already exists")
        self.captures[capture.capture_id] = capture

    async def bind_capture_meeting(self, *, capture_id, meeting_id):
        capture = self.captures[capture_id]
        self.captures[capture_id] = replace(capture, meeting_id=str(meeting_id), state="requested")
        self.subject_meetings.setdefault(
            (capture.subject.tenant_id, capture.subject.user_id), set()
        ).add(str(meeting_id))

    async def get_capture(self, *, subject, capture_id):
        capture = self.captures.get(capture_id)
        if capture is None or capture.subject != subject:
            return None
        return capture

    async def get_capture_by_operation(self, *, subject, operation_id):
        for capture in self.captures.values():
            if capture.subject == subject and capture.operation_id == operation_id:
                return capture
        return None

    async def get_capture_for_meeting(self, meeting_id):
        for capture in self.captures.values():
            if capture.meeting_id == str(meeting_id):
                return capture
        return None

    async def capture_meetings_needing_reconciliation(self, *, limit):
        # Same row shape as the SQL projection: `end_time` is present even when a
        # test seeds a minimal dict, because terminal settlement reads the meeting's
        # END from this row rather than the reconcile tick's clock.
        return tuple(
            {
                "id": row.get("id"),
                "status": row.get("status"),
                "data": row.get("data"),
                "end_time": row.get("end_time"),
            }
            for row in self.reconcile_meetings[:limit]
        )

    async def mark_capture_state(self, *, capture_id, state, failure_code=None):
        capture = self.captures[capture_id]
        self.captures[capture_id] = replace(capture, state=state, failure_code=failure_code)

    async def list_owned_erasure_targets(self, subject):
        targets = []
        for capture in self.captures.values():
            if capture.subject == subject and capture.meeting_id is not None:
                targets.append(ErasureTarget(
                    meeting_id=capture.meeting_id,
                    subject=subject,
                    platform=capture.platform,
                    native_meeting_id=capture.native_meeting_id,
                    state=capture.state,
                    capture_id=capture.capture_id,
                ))
        return tuple(sorted(targets, key=lambda target: int(target.meeting_id)))

    async def get_erasure_target(self, *, subject, meeting_id):
        capture = await self.get_capture_for_meeting(meeting_id)
        if capture is not None and capture.subject == subject:
            return ErasureTarget(
                meeting_id=str(meeting_id), subject=subject, platform=capture.platform,
                native_meeting_id=capture.native_meeting_id, state=capture.state,
                capture_id=capture.capture_id,
            )
        return self.recovery_targets.get((subject.tenant_id, subject.user_id, str(meeting_id)))


    async def erase_subject_control_data(self, subject):
        self.policies.pop((subject.tenant_id, subject.user_id), None)
        self.subject_meetings.pop((subject.tenant_id, subject.user_id), None)
        self.captures = {
            capture_id: capture
            for capture_id, capture in self.captures.items()
            if capture.subject != subject
        }
        self.callbacks = {
            event_id: event for event_id, event in self.callbacks.items()
            if event.subject != subject
        }

    async def record_capture_transition(self, *, capture, state, failure_code, events):
        current = self.captures.get(capture.capture_id)
        if current is None:
            return
        self.captures[capture.capture_id] = replace(
            current,
            state=state,
            failure_code=failure_code,
            captured_seconds_total=max(current.captured_seconds_total, capture.captured_seconds_total),
        )
        for event in events:
            self.callbacks.setdefault(event.event_id, event)
            if event.terminal and event.capture_id:
                self._terminal_callback_ids.setdefault(event.capture_id, set()).add(event.event_id)


    async def pending_callbacks(self, *, limit, capture_id=None):
        events = self.callbacks.values()
        if capture_id is not None:
            events = (event for event in events if event.capture_id == capture_id)
        return tuple(list(events)[:limit])

    async def mark_callback_delivered(self, event_id):
        self.callbacks.pop(event_id, None)
        self._delivered_callbacks.add(event_id)

    async def mark_callback_failed(self, event_id):
        return None

    async def terminal_callbacks_delivered(self, capture_id):
        ids = self._terminal_callback_ids.get(capture_id, set())
        return bool(ids) and ids.issubset(self._delivered_callbacks)

    async def finalize_erased_capture(self, *, subject, meeting_id):
        capture = await self.get_capture_for_meeting(meeting_id)
        if capture is None or capture.subject != subject:
            return
        if not await self.terminal_callbacks_delivered(capture.capture_id):
            raise RuntimeError("terminal settlement is not delivered")
        self.captures.pop(capture.capture_id, None)
        self.subject_meetings.setdefault((subject.tenant_id, subject.user_id), set()).discard(str(meeting_id))
        self.callbacks = {
            event_id: event for event_id, event in self.callbacks.items()
            if event.capture_id != capture.capture_id
        }
