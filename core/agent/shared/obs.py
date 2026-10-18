"""Structured, trace-correlated logging for the agent domain — conforms to ``logevent.v1``.

One emitter for agent-api (service ``agent-api``) and its workers (``agent-worker``); each process
names itself once with :func:`configure`. The shared seam is the ``logevent.v1`` CONTRACT
(``core/gateway/contracts/logevent.v1``), not code: every other domain carries its own emitter of
the same shape.

Two ids ride every line (Vexa-ai/vexa#1783):

* ``trace_id`` — one request or one turn. Read from ``X-Trace-Id`` (minted when absent), bound in a
  contextvar, echoed on the response and forwarded on every outbound call (:func:`trace_headers`).
* ``session`` — the agent unit (a chat thread or a background job) the line belongs to. agent-api
  binds it from ``x-user-delegation-unit``, which ``IdentityGuard`` rebuilds from the gateway's
  signed identity (a client cannot set it), or from its own dispatch; a worker binds its own
  ``VEXA_UNIT_ID``. The credential broker records the same value as ``audit.session``.

A line names WHAT happened — event, ids, outcome. Never a token, a credential, a prompt, mail or
message text: callers pass ids and outcomes only.
"""
from __future__ import annotations

import contextvars
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

TRACE_HEADER = "X-Trace-Id"
#: Rebuilt by IdentityGuard from the gateway's signed delegation (gateway-identity.v1 delegation.unit).
SESSION_HEADER = "x-user-delegation-unit"

#: What a trace id may look like when it arrives from elsewhere (a header, a stream entry, the env):
#: short and header-safe. Anything else is replaced by a fresh one rather than echoed into a header.
_TRACE_FORM = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_service = "agent-api"
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("agent_trace_id", default=None)
_session: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("agent_session", default=None)
_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("agent_user_id", default=None)
_sink: Optional[list] = None


def configure(service: str) -> None:
    """Name this process (the ``service`` field of every line)."""
    global _service
    _service = service


def capture(sink: Optional[list]) -> None:
    """Install (or clear, with ``None``) a list that receives every emitted envelope — for tests."""
    global _sink
    _sink = sink


def new_trace_id() -> str:
    return uuid.uuid4().hex


def clean_trace_id(value: Any) -> Optional[str]:
    """``value`` as a trace id when it has the form of one, else None."""
    v = str(value or "").strip()
    return v if _TRACE_FORM.fullmatch(v) else None


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def set_trace_id(trace_id: Optional[str]) -> contextvars.Token:
    return _trace_id.set(clean_trace_id(trace_id))


def reset_trace_id(token: contextvars.Token) -> None:
    _trace_id.reset(token)


def get_session() -> Optional[str]:
    return _session.get()


def set_session(session: Optional[str]) -> contextvars.Token:
    v = str(session or "").strip()
    return _session.set(v or None)


def reset_session(token: contextvars.Token) -> None:
    _session.reset(token)


def set_user_id(user_id: Any) -> contextvars.Token:
    return _user_id.set(str(user_id) if user_id not in (None, "") else None)


def trace_headers() -> dict:
    """The header that continues this trace on an outbound call to another Vexa service. Empty
    outside a trace — never a header to a third party."""
    tid = _trace_id.get()
    return {TRACE_HEADER: tid} if tid else {}


def log_event(event: str, *, audience: str = "system", level: str = "info", span: Optional[str] = None,
              user_id: Any = None, meeting_id: Optional[str] = None, session: Optional[str] = None,
              fields: Optional[dict] = None, stream=None) -> dict:
    """Emit one ``logevent.v1`` JSON line (stderr — a worker's stdout is its harness's channel) and
    return the envelope. ``session`` defaults to the bound one."""
    envelope: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": _service,
        "trace_id": _trace_id.get() or new_trace_id(),
        "audience": audience,
        "event": event,
    }
    if span:
        envelope["span"] = span
    uid = user_id if user_id not in (None, "") else _user_id.get()
    if uid not in (None, ""):
        envelope["user_id"] = str(uid)
    if meeting_id:
        envelope["meeting_id"] = str(meeting_id)
    sess = session or _session.get()
    if sess:
        envelope["session"] = str(sess)
    if fields:
        envelope["fields"] = fields
    try:
        print(json.dumps(envelope, separators=(",", ":"), default=str), file=stream or sys.stderr,
              flush=True)
    except Exception:  # noqa: BLE001 — a log line is never worth a request
        pass
    if _sink is not None:
        _sink.append(envelope)
    return envelope


class TraceMiddleware:
    """ASGI: bind the request's trace (``X-Trace-Id``, minted when absent) and, when the gateway's
    signed identity named one, its session; echo the trace on the response.

    Pure ASGI rather than ``BaseHTTPMiddleware`` so the chat's SSE stream is not buffered. Installed
    INSIDE ``IdentityGuard``: the ``x-user-delegation-unit`` it reads is the one the guard rebuilt
    from verified claims (or the internal tier asserted), never a client's."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        incoming = session = user = ""
        for name, value in scope.get("headers") or ():
            n = name.lower()
            if n == b"x-trace-id":
                incoming = value.decode("latin-1")
            elif n == SESSION_HEADER.encode():
                session = value.decode("latin-1").strip()
            elif n == b"x-user-id":
                user = value.decode("latin-1").strip()
        trace_id = clean_trace_id(incoming) or new_trace_id()
        tokens = (_trace_id.set(trace_id), _session.set(session or None), _user_id.set(user or None))
        started = time.monotonic()
        status = {"code": 0}
        path = scope.get("path", "")
        quiet = path.startswith("/health")   # probes: bound and echoed, not logged
        if not quiet:
            log_event("request_received", level="debug", span="edge",
                      fields={"method": scope.get("method", "WS"), "path": path, "minted": not incoming})

        async def _send(message):
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status", 0)
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-trace-id"]
                headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            if not quiet:
                log_event("request_completed", level="debug", span="edge",
                          fields={"method": scope.get("method", "WS"), "path": path,
                                  "status": status["code"],
                                  "ms": int((time.monotonic() - started) * 1000)})
            _user_id.reset(tokens[2])
            _session.reset(tokens[1])
            _trace_id.reset(tokens[0])
