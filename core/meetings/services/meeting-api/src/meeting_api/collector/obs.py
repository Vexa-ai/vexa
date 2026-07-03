"""Structured, trace-correlated logging for transcription-collector — conforms to ``logevent.v1``.

SELF-CONTAINED per-service setup (the shared SSOT is the ``logevent.v1`` CONTRACT, not shared
code — keeps the cross-domain import-boundary gate clean). ~The same ~50 lines live in the
gateway, meeting-api and runtime services, each bound to its own ``SERVICE`` name; the wire
format is identical because all conform to the one contract.

Provides a ``trace_id`` propagated via ``contextvars``, ``log_event(...)`` emitting one JSON
line conforming to ``logevent.v1``, and ``TraceMiddleware`` that reads/sets ``X-Trace-Id``
(reuse an incoming id forwarded by the gateway; mint only if absent) so this hop's logs share
the caller's trace_id.

The core is a tiny factory keyed by service name (``make_log_event`` / ``make_trace_middleware``)
so the in-process conformance chain can stand up a collector-bound emitter that shares the
gateway's MODULE-GLOBAL contextvars — modelling, in one process, the contextvar propagation
the real cross-process services get via ``X-Trace-Id``.
"""
from __future__ import annotations

import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware

SERVICE = "transcription-collector"
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


def make_log_event(service: str) -> Callable[..., dict]:
    """Build a ``log_event`` bound to ``service`` (the ``logevent.v1`` ``service`` field)."""

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
        """Emit one ``logevent.v1`` JSON line to stdout and return the envelope (for tests)."""
        envelope: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": level,
            "service": service,
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

    return log_event


def make_trace_middleware(log_event: Callable[..., dict]) -> type:
    """Build a TraceMiddleware class that uses ``log_event`` (and the SAME contextvars)."""

    class TraceMiddleware(BaseHTTPMiddleware):
        """Read ``X-Trace-Id`` (reuse the gateway's forwarded id; mint only if absent), bind it
        for the request, echo it on the response — so this hop's logs share the caller's trace."""

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

    return TraceMiddleware


# ---- This lane's binding: the collector hop (service = "transcription-collector") ----
log_event = make_log_event(SERVICE)
TraceMiddleware = make_trace_middleware(log_event)
