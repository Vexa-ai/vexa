"""Structured logging for the credential broker — one JSON line per event, conforming to
``logevent.v1`` (core/gateway/contracts/logevent.v1). Self-contained per service, as every other
emitter in the tree is: the contract is the shared seam, not this code.

The rule that matters here: a line names WHAT happened (event, actor, connection, kind, status),
never a value. No credential, token, code, mail text or request body is ever passed to
``log_event`` — the callers hold that line, and the tests read every line the suite emits.
"""
from __future__ import annotations

import contextvars
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware

SERVICE = "credential-broker"
TRACE_HEADER = "x-trace-id"

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)


def log_event(event: str, *, level: str = "info", user_id: Any = None, fields: Optional[dict] = None,
              stream=None) -> dict:
    envelope: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": SERVICE,
        "trace_id": _trace_id.get() or uuid.uuid4().hex,
        "audience": "system",
        "event": event,
    }
    if user_id is not None:
        envelope["user_id"] = str(user_id)
    if fields:
        envelope["fields"] = fields
    print(json.dumps(envelope, separators=(",", ":")), file=stream or sys.stdout, flush=True)
    return envelope


class TraceMiddleware(BaseHTTPMiddleware):
    """Reuse the caller's X-Trace-Id (mint one if absent) for every line this request emits."""

    async def dispatch(self, request, call_next):
        trace_id = request.headers.get(TRACE_HEADER) or uuid.uuid4().hex
        token = _trace_id.set(trace_id)
        try:
            response = await call_next(request)
            response.headers[TRACE_HEADER] = trace_id
            return response
        finally:
            _trace_id.reset(token)
