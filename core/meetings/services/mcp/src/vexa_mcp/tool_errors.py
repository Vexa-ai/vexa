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

#: HOW MUCH UPSTREAM BODY A TOOL RESULT MAY CARRY. Whatever the deciding service said goes to the
#: agent — but an upstream that answers an error with a stack trace, an HTML error page or a megabyte
#: of rows would put all of it in the agent's context window, on a call that FAILED, and it is the
#: first line of this block that the agent has to act on. Four kilobytes is more than any authored
#: refusal and less than any accident.
_MAX_BODY_CHARS = 4096

#: WHY A MARKER AND NOT A SILENT CUT. A truncated JSON document is invalid JSON, and a caller that
#: parses the body has to be able to tell "the decider sent this" from "we shortened it" — otherwise
#: a bounded body reads as a malformed upstream. The count is here so a reader knows what it costs
#: to go and look at the source.
_ELIDED = "… [{n} more characters elided by vexa-mcp]"


def _bounded(text: str) -> str:
    """`text`, or its first :data:`_MAX_BODY_CHARS` characters with what was dropped stated."""
    extra = len(text) - _MAX_BODY_CHARS
    if extra <= 0:
        return text
    # No newline in the marker: `notices.render_error` addresses this block by LINE, putting its
    # field on the last one, so bounding must not change how many lines there are.
    return text[:_MAX_BODY_CHARS] + _ELIDED.format(n=extra)


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

    That last line is the block's last line as this module writes it. ``notices`` may append standing
    sentences after the whole block, and puts its field on that body line when it is an object — the
    reason it can is that the body's position here is defined and stable.
    """
    raw = body_text or ""
    try:
        parsed: Any = json.loads(raw)
    except Exception:  # noqa: BLE001 — a non-JSON upstream body is still worth showing
        parsed = None

    if parsed is None:
        stripped = raw.strip()
        return f"HTTP {status_code}" + (f"\n{_bounded(stripped)}" if stripped else "")

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
    lines.append(_bounded(
        inner if isinstance(inner, str)
        else json.dumps(inner, separators=(",", ":"), ensure_ascii=False)
    ))
    return "\n".join(lines)


#: The pin this service's two library seams are true of. Spelled here as well as in `pyproject.toml`
#: so the boot message can name it.
FASTAPI_MCP_PIN = "fastapi-mcp==0.4.0"


def require_library_seam(mcp: Any, attribute: str) -> None:
    """Fail the boot, naming the attribute, when the pinned library stops carrying a seam we wrap.

    THIS SERVICE WRAPS TWO PRIVATE ATTRIBUTES of `fastapi-mcp` — `_request` here and
    `_execute_api_tool` in `notices` — because both are the only points where what an agent reads is
    decided, and wrapping them leaves the success path, the headers, the mount and the argument
    handling the library's. A private attribute carries no compatibility promise, so the dependency
    is PINNED to an exact version (see :data:`FASTAPI_MCP_PIN`), not to a range: a rename inside what
    a range would accept as a compatible release would otherwise
    change behaviour with nothing to notice it.

    A pin makes the rename impossible to arrive by accident; this makes it impossible to arrive
    quietly. Without it the failure is an `AttributeError` from the middle of `create_app`, naming
    a line rather than the contract — and if anyone ever softened that to a `getattr` default, the
    service would boot and simply stop rendering refusals structurally, which is the silent version
    of the same defect.
    """
    if not hasattr(mcp, attribute):
        raise RuntimeError(
            f"{type(mcp).__name__}.{attribute} is gone: this service wraps it to decide what an "
            f"agent reads on a tool call. The dependency is pinned to {FASTAPI_MCP_PIN} precisely "
            "because it is a private attribute — re-read the library's call path and re-point the "
            "wrapper before moving the pin.")


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
    require_library_seam(mcp, "_request")
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
