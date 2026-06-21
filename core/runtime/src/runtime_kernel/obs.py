"""Structured, trace-correlated logging for the runtime kernel — conforms to ``logevent.v1``.

SELF-CONTAINED per-service setup (the shared SSOT is the ``logevent.v1`` CONTRACT, not shared
code — keeps the cross-domain import-boundary gate clean). ~The same ~50 lines live in the
gateway and meeting-api services, each bound to its own ``SERVICE`` name; the wire format is
identical because all three conform to the one contract.

Provides a ``trace_id`` propagated via ``contextvars``, ``log_event(...)`` emitting one JSON
line conforming to ``logevent.v1``, and ``TraceMiddleware`` that reads/sets ``X-Trace-Id``
(reuse an incoming id from the control-plane caller; mint only if absent) so this hop's logs
share the caller's trace_id. The runtime is downstream of meeting-api/agent-api on the
workload-spawn path; this threads the trace one more hop toward the bot leg (the remaining
bot/pipeline hop is owned by the telemetry stream — a follow-on).
"""
from __future__ import annotations

import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware

SERVICE = "runtime"
TRACE_HEADER = "x-trace-id"

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_user_id: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("user_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    return _trace_id.set(trace_id)


def set_user_id(user_id: Any) -> contextvars.Token:
    return _user_id.set(user_id)


def log_event(
    event: str,
    *,
    audience: str,
    level: str = "info",
    span: Optional[str] = None,
    user_id: Any = None,
    meeting_id: Optional[str] = None,
    fields: Optional[dict] = None,
    stream=None,
) -> dict:
    """Emit one ``logevent.v1`` JSON line to stdout and return the envelope."""
    envelope: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": SERVICE,
        "trace_id": _trace_id.get() or new_trace_id(),
        "audience": audience,
        "event": event,
    }
    if span is not None:
        envelope["span"] = span
    uid = user_id if user_id is not None else _user_id.get()
    if uid is not None:
        envelope["user_id"] = uid
    if meeting_id is not None:
        envelope["meeting_id"] = meeting_id
    if fields:
        envelope["fields"] = fields
    print(json.dumps(envelope, separators=(",", ":")), file=stream or sys.stdout, flush=True)
    return envelope


class TraceMiddleware(BaseHTTPMiddleware):
    """Read ``X-Trace-Id`` (reuse an incoming id from the control-plane caller; mint only if
    absent), bind it for the request, echo it on the response."""

    async def dispatch(self, request, call_next):
        incoming = request.headers.get(TRACE_HEADER)
        trace_id = incoming or new_trace_id()
        token = _trace_id.set(trace_id)
        try:
            request.state.trace_id = trace_id
            response = await call_next(request)
            response.headers[TRACE_HEADER] = trace_id
            return response
        finally:
            _trace_id.reset(token)
