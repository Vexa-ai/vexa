"""What an AGENT reads when a call is refused.

An upstream refusal is authored as one object — `{"code", "reason", "decision_id", "message"?,
"action_url"?}` — and by the time it reaches the tool caller it has been wrapped twice and stringified
once:

    Error calling request_meeting_bot. Status code: 403.
    Response: {"detail":{"detail":{"code":"…","reason":"…","decision_id":"…"}}}

Every field an agent could act on is intact and none of it is reachable: it is JSON text inside prose,
under an envelope whose depth is an accident of how many services re-raised it. Two things happen here
and neither knows any vocabulary:

  * ``unwrap_detail`` peels ``{"detail": …}`` envelopes to the innermost object. It peels only when
    ``detail`` is the SOLE key, so a body that carries siblings is never truncated.
  * ``render_tool_error`` puts the words first and the machine-readable body last, on its own lines.

No reason, code or product noun appears in this module. Whatever the deciding service said is what the
agent sees; a reason this build has never heard of renders exactly as well as one it has.
"""
from __future__ import annotations

import json
from typing import Any

#: Envelopes are pathological long before this; the bound just stops a cyclic structure from spinning.
_MAX_UNWRAP = 10


def unwrap_detail(body: Any) -> Any:
    """Peel nested ``{"detail": …}`` envelopes down to the innermost value."""
    depth = 0
    while isinstance(body, dict) and len(body) == 1 and "detail" in body and depth < _MAX_UNWRAP:
        body = body["detail"]
        depth += 1
    return body


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def render_tool_error(status_code: int, body_text: str) -> str:
    """Render an upstream HTTP error as the block a tool result should carry.

    Line 1 is what a human or an agent can act on — ``<reason>: <message>`` when the decider authored
    a message, else ``HTTP <status> <code>``. Line 2 is ``action_url: …`` when there is somewhere to
    go. The last line is the unwrapped body, compact, exactly once.
    """
    raw = body_text or ""
    try:
        parsed: Any = json.loads(raw)
    except Exception:  # noqa: BLE001 — a non-JSON upstream body is still worth showing
        parsed = None

    if parsed is None:
        stripped = raw.strip()
        return f"HTTP {status_code}" + (f"\n{stripped}" if stripped else "")

    inner = unwrap_detail(parsed)
    fields = inner if isinstance(inner, dict) else {}
    reason = _text(fields.get("reason"))
    message = _text(fields.get("message"))
    code = _text(fields.get("code"))
    action_url = _text(fields.get("action_url"))

    lines = []
    if message:
        lines.append(f"{reason}: {message}" if reason else message)
    else:
        lines.append(" ".join(p for p in (f"HTTP {status_code}", code or reason) if p))
    if action_url:
        lines.append(f"action_url: {action_url}")
    lines.append(
        inner if isinstance(inner, str)
        else json.dumps(inner, separators=(",", ":"), ensure_ascii=False)
    )
    return "\n".join(lines)


class UpstreamToolError(Exception):
    """An upstream 4xx/5xx, carrying the rendered block as its ``str()``.

    The MCP lowlevel server turns an exception raised by a tool handler into a ``CallToolResult`` with
    ``isError: true`` and ``str(exc)`` as the text — so this class IS the tool result the agent reads.
    """

    def __init__(self, text: str, *, status_code: int, body: str) -> None:
        super().__init__(text)
        self.status_code = status_code
        self.body = body


def install_structured_tool_errors(mcp: Any) -> None:
    """Make the mounted MCP surface render upstream errors structurally.

    ``fastapi-mcp`` renders a failed call by interpolating the raw response body into a sentence
    (``Error calling <tool>. Status code: <n>. Response: <body>``), which is prose an agent has to
    parse a JSON document out of. The refusal fields survive but are not usable.

    Fix at the point of introduction: ``_execute_api_tool`` fetches through ``_request`` inside its own
    try-block and re-raises whatever comes out of it, so raising here replaces the sentence and nothing
    else — success paths, headers, path/query handling and the mount all stay the library's.
    """
    original = mcp._request

    async def _request(client, method, path, query, headers, body):  # noqa: ANN001 — library signature
        response = await original(client, method, path, query, headers, body)
        status = getattr(response, "status_code", None)
        if isinstance(status, int) and 400 <= status < 600:
            text = getattr(response, "text", "") or ""
            raise UpstreamToolError(
                render_tool_error(status, text), status_code=status, body=text,
            )
        return response

    mcp._request = _request  # type: ignore[method-assign]
