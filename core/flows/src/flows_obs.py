"""Structured, trace-correlated logging for the flows domain — one JSON line per event, conforming
to ``logevent.v1`` (core/gateway/contracts/logevent.v1). Self-contained, as every emitter in the
tree is: the CONTRACT is the shared seam, not this code.

One module for the domain's three processes, told apart by ``service``: flows-api logs as
``flows-api``, the worker as ``flows-worker``, the mailbox as ``mailbox``. The service rides on the
bound scope — ``TraceMiddleware(app, service=…)`` for a request, ``unit(service)`` for a unit of
work — so the three never borrow each other's name inside one process (the test suite imports all
three); ``set_service`` names lines emitted outside any scope.

WHERE THE TRACE COMES FROM
  * flows-api — ``TraceMiddleware`` reads inbound ``X-Trace-Id`` (mints one if absent), binds it for
    the request and echoes it on the response.
  * the worker and the mailbox — ``unit()`` mints one per unit of work (one claimed step, one
    processed message) and binds it for the duration.
  * outbound — ``flows_steps.common.http``, the one door to Vexa services, adds ``X-Trace-Id`` from
    ``get_trace_id()``. Third-party wires (Microsoft Graph, IMAP, mailpit, SMTP) have their own
    openers and never see it.

THE SESSION is the agent unit that caused a line. It is bound ONLY from identity's validate answer
(``delegation.unit``, which identity verified inside the delegation token's signature) — never from
a request header a client can set.

The binding is ONE mutable per-unit scope held in a ContextVar, not a ContextVar per field: FastAPI
runs sync dependencies in a worker thread on a COPY of the context, so a value ``set`` there would
not reach lines emitted afterwards. Mutating the scope the middleware bound does.

A line names WHAT happened — event, ids, outcome — never a value: no token, secret, mail address,
subject or body is passed to ``log_event``.

Stdlib only, so the worker and the mailbox import it without the web stack.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

TRACE_HEADER = "x-trace-id"
#: The header name as sent on outbound calls.
TRACE_HEADER_OUT = "X-Trace-Id"

_service = "flows"
_scope: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("flows_obs_scope", default=None)
_sink: Optional[list] = None


def set_service(name: str) -> None:
    """Name this process (`flows-api` · `flows-worker` · `mailbox`) — the logevent `service`."""
    global _service
    _service = name


def capture(sink: Optional[list]) -> None:
    """Install (or clear, with ``None``) a list that receives every emitted envelope (tests)."""
    global _sink
    _sink = sink


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> Optional[str]:
    s = _scope.get()
    return s["trace_id"] if s else None


def set_trace_id(trace_id: str, *, service: Optional[str] = None) -> contextvars.Token:
    """Bind a fresh scope carrying ``trace_id`` (and the emitting ``service``, when the scope's
    owner knows it). Returns the token for ``reset``."""
    return _scope.set({"trace_id": trace_id, "session": None, "user_id": None, "service": service})


def reset(token: contextvars.Token) -> None:
    _scope.reset(token)


def get_session() -> Optional[str]:
    s = _scope.get()
    return s["session"] if s else None


def set_session(session: Optional[str]) -> None:
    """Bind the agent unit for the current scope. Callers pass ONLY a verified value (identity's
    `delegation.unit`), never one read off a request header. Outside a bound scope (a request, a
    unit of work) it does nothing: a session must not outlive the unit that proved it."""
    s = _scope.get()
    if s is not None:
        s["session"] = str(session) if session else None


def set_user_id(user_id: Any) -> None:
    s = _scope.get()
    if s is not None:
        s["user_id"] = user_id


@contextlib.contextmanager
def unit(service: Optional[str] = None, trace_id: Optional[str] = None) -> Iterator[str]:
    """One unit of work (a worker step, a mailbox message): mint (or adopt) a trace and bind it,
    with the process that owns the unit as every line's ``service``."""
    tid = trace_id or new_trace_id()
    token = set_trace_id(tid, service=service)
    try:
        yield tid
    finally:
        _scope.reset(token)


def trace_headers() -> dict:
    """``{"X-Trace-Id": …}`` when a trace is bound, else ``{}`` — for outbound Vexa calls."""
    tid = get_trace_id()
    return {TRACE_HEADER_OUT: tid} if tid else {}


def log_event(event: str, *, audience: str = "system", level: str = "info",
              span: Optional[str] = None, user_id: Any = None, meeting_id: Any = None,
              fields: Optional[dict] = None, service: Optional[str] = None, stream=None) -> dict:
    """Emit one ``logevent.v1`` line to stdout and return the envelope."""
    s = _scope.get() or {}
    envelope: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": service or s.get("service") or _service,
        "trace_id": s.get("trace_id") or new_trace_id(),
        "audience": audience,
        "event": event,
    }
    if span:
        envelope["span"] = span
    uid = user_id if user_id is not None else s.get("user_id")
    if uid not in (None, ""):
        envelope["user_id"] = uid if isinstance(uid, int) else str(uid)
    if meeting_id not in (None, ""):
        envelope["meeting_id"] = str(meeting_id)
    if s.get("session"):
        envelope["session"] = s["session"]
    if fields:
        envelope["fields"] = fields
    try:
        print(json.dumps(envelope, separators=(",", ":"), default=str), file=stream or sys.stdout,
              flush=True)
    except Exception:  # noqa: BLE001 — a log line never takes the process down
        pass
    if _sink is not None:
        _sink.append(envelope)
    return envelope


def _acceptable(trace_id: str) -> Optional[str]:
    """An inbound id is adopted verbatim if it is a short printable token; anything else is
    replaced by a minted one rather than written into every line of the request."""
    if trace_id and len(trace_id) <= 128 and trace_id.isprintable() and " " not in trace_id:
        return trace_id
    return None


class TraceMiddleware:
    """Pure-ASGI: read ``X-Trace-Id`` (mint if absent), bind it for the request, log
    ``request_received``, echo the id on the response."""

    def __init__(self, app, service: str = "flows-api") -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for k, v in scope.get("headers") or ():
            if k.decode("latin-1").lower() == TRACE_HEADER:
                incoming = _acceptable(v.decode("latin-1").strip())
                break
        trace_id = incoming or new_trace_id()
        token = set_trace_id(trace_id, service=self.service)

        async def _send(message):
            if message.get("type") == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers") or ()
                           if k.decode("latin-1").lower() != TRACE_HEADER]
                headers.append((TRACE_HEADER.encode(), trace_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            log_event("request_received", level="debug", span="edge",
                      fields={"method": scope.get("method"), "path": scope.get("path"),
                              "minted": incoming is None})
            await self.app(scope, receive, _send)
        finally:
            _scope.reset(token)
