"""Structured, trace-correlated logging — conforms to ``logevent.v1``.

This is the gateway lane's PRODUCTION emitter (promoted out of the conformance harness).
The SHARED SSOT is the ``logevent.v1`` CONTRACT, not shared code: the real ``meeting-api``
and ``runtime`` services each carry their OWN equivalent emitter bound to their own service
name — duplicated ~by design so no cross-domain Python import crosses the ``services/`` seam.
The wire format (the JSON envelope) is identical because all conform to the one contract.

It provides:
  * a ``trace_id`` propagated via ``contextvars`` (one ContextVar per process),
  * ``log_event(...)`` which emits ONE JSON line conforming to ``logevent.v1``,
  * ``TraceMiddleware`` — a FastAPI/Starlette middleware that reads/sets the ``X-Trace-Id``
    header: MINTS a trace_id at the edge when absent, binds it for the request, echoes it on
    the response. Downstream hops forward the SAME id so every hop's logs share it.

The core is a tiny factory keyed by service name (``make_log_event`` / ``make_trace_middleware``)
so the in-process conformance chain can stand up a second emitter for the downstream hop
(service ``"meeting-api"``) that shares these MODULE-GLOBAL contextvars — modelling, in one
process, the contextvar propagation the real cross-process services get via ``X-Trace-Id``.
"""
from __future__ import annotations

import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware

TRACE_HEADER = "x-trace-id"

# Process-wide context: the current request's trace_id + user_id (None outside a request).
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_user_id: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("user_id", default=None)

# Optional test sink: when a list is installed, every emitted envelope is also appended to it
# (an eval captures lines deterministically without relying on stdout capture across threads).
_sink: Optional[list] = None


def capture(sink: Optional[list]) -> None:
    """Install (or clear, with ``None``) a list that receives every emitted envelope."""
    global _sink
    _sink = sink


def new_trace_id() -> str:
    """Mint a fresh trace id (hex, no dashes)."""
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
        """Emit one ``logevent.v1`` JSON line to stdout and return the envelope (for tests).

        ``trace_id`` is read from the contextvar (or minted if somehow unset, so a line is
        never contract-invalid). ``audience`` ∈ {user, system}. ``user_id`` falls back to the
        bound request user when not passed explicitly.
        """
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
        if _sink is not None:
            _sink.append(envelope)
        return envelope

    return log_event


def make_trace_middleware(log_event: Callable[..., dict]) -> type:
    """Build a TraceMiddleware class that logs via ``log_event``."""

    class TraceMiddleware(BaseHTTPMiddleware):
        """Read ``X-Trace-Id`` (mint at the edge if absent), bind it for the request, echo it
        on the response. Downstream hops forward the same header so the trace is continuous."""

        async def dispatch(self, request, call_next):
            incoming = request.headers.get(TRACE_HEADER)
            trace_id = incoming or new_trace_id()
            token = _trace_id.set(trace_id)
            try:
                request.state.trace_id = trace_id
                log_event(
                    "request_received",
                    audience="system",
                    level="debug",
                    span="edge",
                    fields={
                        "method": request.method,
                        "path": request.url.path,
                        "minted": incoming is None,
                    },
                )
                response = await call_next(request)
                response.headers[TRACE_HEADER] = trace_id
                return response
            finally:
                _trace_id.reset(token)

    return TraceMiddleware


# ---- This lane's binding: the gateway edge (service = "gateway") ----
SERVICE = "gateway"
log_event = make_log_event(SERVICE)
TraceMiddleware = make_trace_middleware(log_event)
