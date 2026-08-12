"""Small, best-effort publishers for the gateway's authenticated user fan-out.

The gateway subscribes to ``u:{user_id}:*`` and forwards payloads unchanged.  This module keeps
the producer-side topic names and ``ws.v1`` frame construction in one place for the agent control
plane and worker.  Redis Pub/Sub is intentionally treated as a transient notification channel:
the REST APIs remain the source of truth and a disconnected client can rebuild its view.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def user_scope(subject: object) -> str:
    """Map an agent subject (normally ``u_42``) to the gateway's user-id scope (``42``)."""
    value = str(subject or "").strip()
    if value.startswith("u_") and value[2:]:
        return value[2:]
    return value


def _frame(*, frame_type: str, subject: object, **fields: Any) -> dict[str, Any]:
    frame = {
        "type": frame_type,
        "event_id": f"evt_{uuid.uuid4().hex}",
        "user_id": user_scope(subject),
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    frame.update(fields)
    return frame


def meetings_changed(*, subject: object, meeting_id: object, change: str,
                     meeting: dict[str, Any] | None = None, revision: object = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"meeting_id": meeting_id, "change": change}
    if meeting is not None:
        fields["meeting"] = meeting
    if revision is not None:
        fields["revision"] = revision
    return _frame(frame_type="meetings.changed", subject=subject, **fields)


def workspace_committed(*, subject: object, workspace_id: object, commit_sha: str,
                        message: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"workspace_id": workspace_id, "commit_sha": commit_sha}
    if message:
        fields["message"] = message
    return _frame(frame_type="workspace.committed", subject=subject, **fields)


def routine_status(*, subject: object, routine_id: str, status: str, job_id: str | None = None,
                   error: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"routine_id": routine_id, "status": status}
    if job_id:
        fields["job_id"] = job_id
    if error:
        fields["error"] = error
    return _frame(frame_type="routine.status", subject=subject, **fields)


class RedisUserEventPublisher:
    """Publish user events without making Redis availability a request/turn failure."""

    def __init__(self, redis_url: str | None) -> None:
        self._redis_url = (redis_url or "").strip()
        self._redis = None

    def _client(self):
        if not self._redis_url:
            return None
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._redis

    def publish(self, *, subject: object, suffix: str, frame: dict[str, Any]) -> bool:
        """Publish a frame to ``u:{scope}:{suffix}``; return False on disabled/unavailable Redis."""
        try:
            client = self._client()
        except Exception:  # noqa: BLE001 - missing Redis client/config is non-fatal to source writes
            return False
        if client is None:
            return False
        try:
            client.publish(f"u:{user_scope(subject)}:{suffix}", json.dumps(frame, separators=(",", ":")))
            return True
        except Exception:  # noqa: BLE001 - event fan-out must never break the source operation
            return False
