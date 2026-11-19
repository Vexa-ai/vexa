"""Structured, trace-correlated logging for the MCP edge — conforms to ``logevent.v1``.

Self-contained per service (the shared SSOT is the ``logevent.v1`` CONTRACT, not shared code), the
same shape as the gateway's and meeting-api's ``obs.py``, bound to ``SERVICE = "mcp"``.

The trace id arrives on ``X-Trace-Id`` from the gateway (minted here only if absent), is bound for
the request, echoed on the response, and handed back to the gateway on every tool's call
(:func:`headers`), so the gateway's line for the `/mcp` request and its line for the tool call it
caused share one trace.

Two things are particular to this edge:

* A tool call is a SECOND, in-process request (``FastApiMCP`` replays the `/mcp` request onto the
  tool's route with an allowlist of headers). ``X-Trace-Id`` is on that allowlist, and a minted id is
  written into the scope's headers, so the inner request carries the outer request's id rather than
  minting its own.
* The MCP session's server runs in a task started by the request that opened the session, so the
  contextvar a later `/mcp` request binds is NOT visible where the tool executes. :func:`install`
  binds the id the tool call itself carries instead.

No ``session`` field: this edge does not verify the gateway's signed identity, and a header is a
value a client can set. The session is joined through the gateway's lines with the same trace_id.
"""
from __future__ import annotations

import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

SERVICE = "mcp"
TRACE_HEADER = "x-trace-id"
OUTBOUND_HEADER = "X-Trace-Id"
_TRACE = TRACE_HEADER.encode("latin-1")

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("vexa_mcp_trace_id",
                                                                          default=None)
_sink: Optional[list] = None


def capture(sink: Optional[list]) -> None:
    """Test hook: install (or clear, with ``None``) a list that receives every emitted envelope."""
    global _sink
    _sink = sink


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    return _trace_id.set(trace_id)


def headers() -> Dict[str, str]:
    """The trace header for an outbound call, or nothing outside a request."""
    tid = _trace_id.get()
    return {OUTBOUND_HEADER: tid} if tid else {}


def log_event(event: str, *, audience: str = "system", level: str = "info",
              span: Optional[str] = None, user_id: Any = None,
              meeting_id: Optional[str] = None, fields: Optional[dict] = None) -> dict:
    """Emit one ``logevent.v1`` JSON line to stdout and return the envelope."""
    envelope: Dict[str, Any] = {
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
    if fields:
        envelope["fields"] = fields
    if _sink is not None:
        _sink.append(envelope)
    try:
        print(json.dumps(envelope, separators=(",", ":"), default=str), file=sys.stdout, flush=True)
    except Exception:  # noqa: BLE001 — a log line must never fail the request it describes
        pass
    return envelope


class TraceMiddleware:
    """ASGI (not ``BaseHTTPMiddleware``, which would buffer the `/mcp` SSE stream): read
    ``X-Trace-Id`` or mint one, write a minted id into the scope so everything downstream — the
    tool call ``FastApiMCP`` replays included — sees it, bind it, echo it on the response."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        trace_id = ""
        for name, value in scope.get("headers") or ():
            if name.lower() == _TRACE:
                trace_id = value.decode("latin-1").strip()
                break
        if not trace_id:
            trace_id = new_trace_id()
            scope = dict(scope)
            scope["headers"] = [(n, v) for n, v in (scope.get("headers") or ())
                                if n.lower() != _TRACE] + [(_TRACE, trace_id.encode("latin-1"))]
        raw = trace_id.encode("latin-1")

        async def _send(message):
            if message.get("type") == "http.response.start":
                kept = [(n, v) for n, v in (message.get("headers") or ()) if n.lower() != _TRACE]
                message = {**message, "headers": kept + [(_TRACE, raw)]}
            await send(message)

        token = _trace_id.set(trace_id)
        try:
            return await self.app(scope, receive, _send)
        finally:
            _trace_id.reset(token)


def _trace_of(http_request_info: Any) -> str:
    raw = getattr(http_request_info, "headers", None) or {}
    for name, value in raw.items():
        if str(name).lower() == TRACE_HEADER and value:
            return str(value).strip()
    return ""


def install(mcp: Any) -> None:
    """Log ``tool_called`` (tool name and outcome only — never arguments, results or credentials)
    for every tool call on the mounted surface, under the trace id the call carries.

    Wraps the same private seam ``notices`` wraps, outermost, so the notices hop it makes also runs
    under the call's trace id."""
    from .tool_errors import UpstreamToolError, require_library_seam

    require_library_seam(mcp, "_execute_api_tool")
    original = mcp._execute_api_tool

    async def _execute_api_tool(*, client, tool_name, arguments, operation_map,
                                http_request_info=None):  # noqa: ANN001 — library signature
        tid = _trace_of(http_request_info)
        token = _trace_id.set(tid) if tid else None
        outcome: Dict[str, Any] = {"tool": tool_name}
        try:
            content = await original(client=client, tool_name=tool_name, arguments=arguments,
                                     operation_map=operation_map,
                                     http_request_info=http_request_info)
            outcome["outcome"] = "ok"
            return content
        except UpstreamToolError as refusal:
            outcome.update(outcome="refused", status=refusal.status_code)
            raise
        except Exception as exc:  # noqa: BLE001 — logged, then re-raised untouched
            outcome.update(outcome="error", error=type(exc).__name__)
            raise
        finally:
            log_event("tool_called", level="info" if outcome.get("outcome") == "ok" else "warning",
                      fields=outcome)
            if token is not None:
                _trace_id.reset(token)

    mcp._execute_api_tool = _execute_api_tool  # type: ignore[method-assign]
