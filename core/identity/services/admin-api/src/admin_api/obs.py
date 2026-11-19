"""Structured, trace-correlated logging for identity (admin-api) — conforms to ``logevent.v1``.

Self-contained per-service emitter: the shared SSOT is the ``logevent.v1`` CONTRACT
(``core/gateway/contracts/logevent.v1``), not shared code, so the import-boundary gate stays clean.

* ``trace_id`` — read from inbound ``X-Trace-Id`` by ``TraceMiddleware`` (minted if absent), bound
  in a ContextVar, echoed on the response, and forwarded on outbound calls to other Vexa services
  via ``trace_headers()``.
* ``session`` — the agent unit that caused the line. Bound ONLY from a verified delegation token's
  ``unit`` claim (``set_session``), never from a request header.

Nothing here logs a query string, an Authorization value, a token, a key or an email.
"""
from __future__ import annotations

import contextvars
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

SERVICE = "identity"
TRACE_HEADER = "X-Trace-Id"

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_session: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("session", default=None)

# Test hook: when set, every envelope is also appended here (deterministic capture, no stdout).
_sink: Optional[list] = None


def capture(sink: Optional[list]) -> None:
    global _sink
    _sink = sink


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    return _trace_id.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    _trace_id.reset(token)


def get_session() -> Optional[str]:
    return _session.get()


def set_session(value: Optional[str]) -> contextvars.Token:
    """Bind the agent session (delegation ``unit`` claim) for the rest of this request.
    Call only after the delegation token has verified."""
    return _session.set(value or None)


def reset_session(token: contextvars.Token) -> None:
    _session.reset(token)


def trace_headers() -> dict:
    """``{"X-Trace-Id": …}`` for an outbound call to another Vexa service ({} when unbound).
    Never add this to a third-party provider call."""
    tid = _trace_id.get()
    return {TRACE_HEADER: tid} if tid else {}


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
    if user_id is not None:
        envelope["user_id"] = user_id
    if meeting_id is not None:
        envelope["meeting_id"] = meeting_id
    session = _session.get()
    if session:
        envelope["session"] = session
    if fields:
        envelope["fields"] = fields
    if _sink is not None:
        _sink.append(envelope)
    try:
        print(json.dumps(envelope, separators=(",", ":"), default=str),
              file=stream or sys.stdout, flush=True)
    except Exception:  # noqa: BLE001 — logging never breaks a request
        pass
    return envelope


class TraceMiddleware:
    """Pure-ASGI: read ``X-Trace-Id`` (mint if absent), bind it (and a clean session slot) for
    the request, echo it on the response, and log ``request_received`` / ``request_completed``
    with method, path (never the query string) and status."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for k, v in scope.get("headers") or ():
            if k == b"x-trace-id":
                incoming = v.decode("latin-1").strip() or None
                break
        trace_id = incoming or new_trace_id()
        t_tok = _trace_id.set(trace_id)
        s_tok = _session.set(None)
        method = scope.get("method", "")
        path = scope.get("path", "")
        status_holder = {"status": 500}
        started = time.monotonic()

        async def _send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-trace-id"]
                headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            log_event("request_received", audience="system", level="debug",
                      fields={"method": method, "path": path})
            try:
                await self.app(scope, receive, _send)
            except Exception as exc:
                log_event("request_failed", audience="system", level="error",
                          fields={"method": method, "path": path, "error": type(exc).__name__})
                raise
            status = status_holder["status"]
            log_event("request_completed", audience="system",
                      level="warning" if status >= 500 else "debug",
                      fields={"method": method, "path": path, "status": status,
                              "duration_ms": round((time.monotonic() - started) * 1000, 1)})
        finally:
            _session.reset(s_tok)
            _trace_id.reset(t_tok)
