"""paths.py — what a caller's path parameters and catch-all tails may become on a downstream hop.

Path params reach a handler URL-DECODED (Starlette resolves %3F/%23/%2E before the route sees
them), so interpolating one raw into a downstream URL lets a caller graft a query string, a
fragment or a dot-segment onto the hop — ``%2E%2E`` walks /user/calendars/{id} back up to
admin-api's /user. Every param is re-encoded as ONE opaque segment before it is interpolated.
Control characters (NUL, CR, LF) are refused here instead: httpx raises ``InvalidURL`` for them,
which is NOT a ``RequestError`` and would escape the 502/504 mapping as a gateway 500.

A CATCH-ALL TAIL (`/agent/{path:path}`, `/mcp/{path:path}`) is the same hazard several segments at
a time: Starlette hands it over decoded, and httpx resolves a dot segment against the downstream
base — so a tail is only ever a path UNDER the prefix it was matched on. A `.` or `..` segment
(written plainly or percent-encoded) and an encoded slash or backslash are refused outright (400):
no client of these routes needs either, and refusing is clearer than re-encoding something that was
asking to change the path's shape. Every other segment goes through `path_segment`, so whatever
survives is data in exactly the segment it was sent in.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple
from urllib.parse import quote

from fastapi import Request, Response

_ENCODED_SEPARATOR = re.compile(rb"%(?:2f|5c)", re.IGNORECASE)


def invalid_path_param_response() -> Response:
    return Response(
        content=json.dumps({"detail": "invalid path parameter"}),
        status_code=400,
        media_type="application/json",
    )


def path_segment(value: str) -> Tuple[Optional[str], Optional[Response]]:
    """One path parameter as one opaque, percent-encoded segment — or the 400 that refuses it."""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None, invalid_path_param_response()
    segment = quote(value, safe="")
    # ``quote`` leaves "." alone (it is unreserved), but httpx RESOLVES a dot-only segment against
    # the base path — "/user/calendars/.." becomes admin-api's "/user". Percent-encode it so the
    # id stays data; "%2E%2E" survives httpx untouched and decodes back to ".." downstream.
    if segment and set(segment) == {"."}:
        segment = segment.replace(".", "%2E")
    return segment, None


def tail_path(path: str, request: Request) -> Tuple[Optional[str], Optional[Response]]:
    """A catch-all tail, re-encoded segment by segment — or the 400 that refuses its shape."""
    raw = request.scope.get("raw_path") or b""
    if _ENCODED_SEPARATOR.search(raw):
        return None, invalid_path_param_response()
    out: List[str] = []
    for segment in path.split("/"):
        if segment in (".", ".."):
            return None, invalid_path_param_response()
        encoded, error = path_segment(segment)
        if error is not None:
            return None, error
        out.append(encoded or "")
    return "/".join(out), None
