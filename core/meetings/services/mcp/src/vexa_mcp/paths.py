"""paths.py — what a caller's value may become in the path of a URL this service sends.

Every tool here addresses the gateway (or a domain's door) by a path, and some of that path comes
from the caller: a meeting's `platform` and `native_meeting_id`, a row id, an assembled tool's path
parameter. Each such value is ONE segment of the route the tool calls and nothing else, so it goes
through `path_segment` — the one place that decides what it becomes — before it is interpolated:

  * every character that is not unreserved is percent-encoded, `/` included, so a value can neither
    end its segment nor graft a query string (`?`) or fragment (`#`) onto the hop; an already
    encoded `%2F` is encoded again (`%252F`) and stays data;
  * a dot-only segment (`.`, `..`) is percent-encoded too. `quote` leaves `.` alone — it is
    unreserved — and httpx RESOLVES a dot segment against the path before the request goes out, so
    `/bots/google_meet/..` would otherwise leave as `/bots`. Encoded, it reaches the gateway inside
    the route the tool named.

This is the MCP's half, on the hop that reaches the gateway. The gateway decodes the value and holds
it to the forwarded-row rule on every meetings row and every forwarded domain's row
(`gateway/paths.py` `forwarded_param`): a `.`/`..` value, or an encoded `/` or `\`, is refused there
with a 400, and any other value is re-encoded into the one segment it arrived in on the gateway's own
hop. A `platform` outside the meeting platforms is refused at both doors.
"""
from __future__ import annotations

from urllib.parse import quote


def path_segment(value: object) -> str:
    """One caller-supplied value as one opaque, percent-encoded path segment."""
    segment = quote(str(value), safe="")
    if segment and set(segment) == {"."}:
        segment = segment.replace(".", "%2E")
    return segment
