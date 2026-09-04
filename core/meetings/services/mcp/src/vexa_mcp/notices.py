"""STANDING NOTICES — what stays true between calls, carried out on the results of ordinary work.

An agent reads a tool result. It does not read anything else unless something makes it. So a fact
that stays true between calls — one a person's agent should be acting on, whatever it happened to
be doing — reaches it reliably in exactly one place: attached to the result of the call it just
made. Anything else is a tool somebody has to remember to call.

That is the whole of this module, and none of it knows what a notice SAYS. The words are flows'
(`GET /queue/notices`, resolved from `behavior/queue/` files that opened with `notice: true`); the
decision about which situations are notices is an admin's file; this is the ride.

FOUR PROPERTIES, each of them a defect if it were absent:

  * **Never an error.** A notice is something extra. If flows is slow, unreachable, unconfigured,
    or answers something this module cannot read, the call the agent actually made answers exactly
    as it would have. There is one `except` here and it catches everything, on purpose.
  * **Bounded.** :data:`TIMEOUT_S` seconds, once per call. This rides on EVERY meeting tool call,
    so its worst case is the worst case of the product.
  * **Both channels.** The field for a caller that parses the body, and a trailing line for one
    that reads the text. Neither is reliably the one an agent looks at.
  * **Once.** A body that already carries the field is left alone, and duplicate sentences are
    dropped — one thing that is true twice reads as two things.

The seam is `FastApiMCP._execute_api_tool`, wrapped exactly the way `tool_errors` wraps `_request`
and for the same reason: the success path, the headers, the mount and the argument handling all
stay the library's, and this adds one thing to what comes out.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional

import httpx

from .discover import DOMAIN_URL_ENV

#: The route that answers with a subject's standing notices. Its auth is the caller's own
#: credential, so this hop forwards what the caller sent and holds nothing of its own.
NOTICES_PATH = "/queue/notices"

#: The field the answer carries them in, and the field a tool result carries them out in — the same
#: name on both sides, because they are the same thing.
FIELD = "notices"

#: Seconds. Small on purpose: see the module docstring.
TIMEOUT_S = 2.0

#: What a notice looks like in the text half of a result.
PREFIX = "Notice: "

#: THE TOOLS THAT CARRY THEM: the ones an agent calls while working on a person's meetings, which
#: is where a standing fact about that person's account is worth reading.
#:
#: `whats_waiting` is deliberately NOT here even though it is the queue tool: it already answers
#: with every waiting item, notices among them, so attaching them again would say the same sentence
#: twice in one result. Nor are the tools that touch no meeting (`parse_meeting_link`,
#: `report_issue`): a notice on a pure parse is noise on a call that reached nothing.
CARRIES_NOTICES = frozenset({
    "request_meeting_bot",
    "get_meeting_transcript",
    "list_meetings",
    "get_bot_status",
    "stop_bot",
})


def base_url(env: Optional[dict] = None) -> str:
    """Where to ask, or `""` when this deployment carries no such domain.

    Read from the SAME env key `discover` reads, imported rather than repeated: a second spelling of
    a deployment fact is a second thing to keep in step, and this one would fail silently — notices
    would simply never appear, on a deployment that has them.
    """
    import os
    env = os.environ if env is None else env
    return (env.get(DOMAIN_URL_ENV["flows"]) or "").strip().rstrip("/")


def caller_key(headers: Optional[dict]) -> str:
    """The credential the caller sent, adapted at this boundary exactly as `register._caller_key`
    does at the assembled-tool boundary: `x-api-key`, else the bearer the MCP transport contract
    uses. This module never holds a credential of its own and never falls back to one."""
    if not headers:
        return ""
    lower = {str(k).lower(): v for k, v in headers.items()}
    key = (lower.get("x-api-key") or "").strip()
    if key:
        return key
    auth = (lower.get("authorization") or "").strip()
    if not auth:
        return ""
    scheme, _, token = auth.partition(" ")
    return (token.strip() if scheme.lower() == "bearer" else auth) or ""


def clean(values: Any) -> List[str]:
    """The notices in an answer, as a list of non-empty strings, deduped in order.

    Defensive about the SHAPE rather than the CONTENT: a body that is not what this expects yields
    no notices, and never an exception. Nothing here inspects what a notice says.
    """
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, dict)):
        return []
    out: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text and text not in out:
            out.append(text)
    return out


async def fetch(api_key: str, *, base: str,
                transport: Optional[httpx.AsyncBaseTransport] = None,
                timeout: float = TIMEOUT_S) -> List[str]:
    """This caller's standing notices. `[]` for every reason there could be not to have any —
    including every reason it could have gone wrong."""
    if not base or not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(
                f"{base}{NOTICES_PATH}",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            )
            if response.status_code != 200 or not response.content:
                return []
            return clean((response.json() or {}).get(FIELD))
    except Exception:  # noqa: BLE001 — a notice is extra; the call the agent made must still answer
        return []


def render(text: str, notices: List[str]) -> str:
    """The tool result's text, with the notices in it — both halves, or unchanged when there are none.

    The body is re-serialised with the same `indent=2` the library used, so a result with no notices
    and a result with notices differ by the notices and by nothing else. A body that is not a JSON
    object (an array, a bare value, a non-JSON string) keeps its shape exactly: the field has nowhere
    to go there, and inventing a wrapper for it would change what every existing caller parses. The
    trailing lines are the reason that is safe — they carry the same words either way.
    """
    if not notices:
        return text
    body = text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and FIELD not in parsed:
            body = json.dumps({**parsed, FIELD: notices}, indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — a body this cannot parse still gets the trailing lines
        pass
    return "\n\n".join([body] + [f"{PREFIX}{n}" for n in notices])


def install(mcp: Any, *, transport: Optional[httpx.AsyncBaseTransport] = None,
            env: Optional[dict] = None) -> None:
    """Make the mounted MCP surface carry standing notices out on the meeting tools' results.

    Wraps `_execute_api_tool`, which is where a successful call becomes the content an agent reads.
    A raised call — every refusal, including the ones `tool_errors` renders — passes through
    untouched: a refusal is not the place to add something the caller did not ask for, and the
    result an agent must act on should carry one thing.
    """
    original = mcp._execute_api_tool

    async def _execute_api_tool(*, client, tool_name, arguments, operation_map,
                                http_request_info=None):  # noqa: ANN001 — library signature
        content = await original(
            client=client, tool_name=tool_name, arguments=arguments,
            operation_map=operation_map, http_request_info=http_request_info,
        )
        if tool_name not in CARRIES_NOTICES or not content:
            return content
        key = caller_key(getattr(http_request_info, "headers", None))
        notices = await fetch(key, base=base_url(env), transport=transport)
        if not notices:
            return content
        for item in content:
            if getattr(item, "type", None) == "text":
                item.text = render(item.text, notices)
                break                       # ONCE PER RESULT, on the body the agent reads first
        return content

    mcp._execute_api_tool = _execute_api_tool  # type: ignore[method-assign]
