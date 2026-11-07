"""meeting_link.py — a chat message that is only a meeting link sends the bot.

The person pastes ``https://meet.google.com/abc-defg-hij`` into the chat and nothing else. That is the
whole instruction, so the worker carries it out before the model is asked anything: it asks the vexa
MCP whether the text is a meeting link it supports (``parse_meeting_link``, the one parser), and if
it is, sends the bot through ``request_meeting_bot`` — the same tool, on the same MCP attachment, with
the same delegation token the model's own call would use. The person's identity and scopes are the
only ones in reach: the token is this unit's, minted for this person by agent-api, and nothing here
reads any other credential.

The turn then runs as usual, with the outcome stated at the top of its prompt, so the model answers
with what happened and the exchange lands in the chat's history like any other turn.

What counts as "only a meeting link": the person's half of the turn (the text after the control
plane's grounding sentinel) is one http(s) URL with no other words, and the turn carries no mark — a
pressed act, a background job, a flow step, a composed opening and a write-back phase are not
messages somebody typed. A link with words around it is an ordinary turn for the model.

Three outcomes, each told to the chat as a ``meeting-link`` event and to the model in the preamble:

* ``requested`` — a bot was asked for; it joins once someone in the meeting admits it.
* ``already_there`` — this person already has a bot in that meeting. ``request_meeting_bot`` answers
  a duplicate with ``already_exists`` (meeting-api refuses a second active bot for the same meeting
  and person), so pasting the link twice never sends a second bot.
* ``refused`` — the send was refused; the event carries the refusal typed by the deciding service's
  own ``code``/``reason`` and the HTTP status, and the model reports it verbatim.

A parse that fails for any reason other than "not a supported meeting link" is reported as
``unchecked`` with its error, and the turn runs as an ordinary one.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from llm.tool_events import _bot_artifact
from shared.marks import MACHINERY_MARK, PHASE_MARK, act_label, read_job_mark, turn_namespace
from shared.meeting_url_redaction import redact_url, redact_urls_in

log = logging.getLogger("agent_api.worker")

#: The tool the send goes through, under the name every harness gives it in a turn's events. Using
#: the harness's name is what lets the rest of the turn's machinery — the step line, the meeting
#: binding (``api_shared.meeting_binding``), the expired-token watch (``tool_access.watch``) — treat
#: this call exactly as it treats the model's.
SEND_TOOL = "mcp__vexa__request_meeting_bot"

#: A meeting row in one of these states has a bot in the room, or on its way in.
LIVE_STATUSES = frozenset({"requested", "joining", "awaiting_admission", "active",
                           "needs_help", "stopping"})

#: How long one MCP call may take. A send creates a meeting row and asks the runtime for a bot; the
#: bot's own join happens after the answer.
CALL_TIMEOUT_S = 30.0

_PLATFORM_NAMES = {"google_meet": "Google Meet", "teams": "Microsoft Teams", "zoom": "Zoom",
                   "jitsi": "Jitsi"}


def candidate(prompt: str, person: str) -> Optional[str]:
    """The URL when the person's half of this turn is one http(s) URL and nothing else, else None.

    ``prompt`` is the whole composed turn (read only for marks); ``person`` is the person's own words
    (``engine.human_half``). This only decides whether to ASK the parser — whether the URL is a
    meeting link is the parser's answer, never this function's."""
    if MACHINERY_MARK in prompt or PHASE_MARK in prompt:
        return None
    if read_job_mark(prompt) is not None or act_label(prompt) is not None or turn_namespace(prompt):
        return None
    text = (person or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    try:
        parts = urlparse(text)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return text


class ToolError(Exception):
    """A ``tools/call`` the server answered with ``isError``; ``str()`` is the tool's own text."""


class McpCaller:
    """One streamable-HTTP MCP conversation on the turn's own attachment: ``initialize``, then calls.

    ``url`` and ``headers`` are read from the attachment the harness is handed (``engine._mcp_endpoint``),
    so the credential is exactly the one this unit's turn holds."""

    def __init__(self, url: str, headers: dict, *, client: Optional[httpx.Client] = None,
                 timeout: float = CALL_TIMEOUT_S) -> None:
        self._url = url
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._client = client or httpx.Client(timeout=timeout)
        self._owns = client is None
        self._session: dict[str, str] = {}
        self._id = 0
        self._ready = False

    def _rpc(self, method: str, params: dict, *, notify: bool = False) -> dict:
        body: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self._id += 1
            body["id"] = self._id
        r = self._client.post(self._url, json=body, headers={
            "content-type": "application/json", "accept": "application/json, text/event-stream",
            **self._headers, **self._session})
        sid = r.headers.get("mcp-session-id")
        if sid:
            self._session["Mcp-Session-Id"] = sid
        if r.status_code >= 400:
            raise ToolError(f"HTTP {r.status_code} from the vexa MCP: {r.text[:300]}")
        if notify or r.status_code == 202 or not (r.content or b"").strip():
            return {}
        payload: dict = {}
        if "text/event-stream" in (r.headers.get("content-type") or "").lower():
            for line in r.text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == body.get("id"):
                    payload = msg
        else:
            payload = r.json()
        if not isinstance(payload, dict):
            raise ToolError("the vexa MCP answered with something that is not JSON-RPC")
        if payload.get("error"):
            raise ToolError(f"vexa MCP error: {payload['error']}")
        return payload.get("result") or {}

    def call(self, tool: str, args: dict) -> str:
        """The tool's text answer. Raises :class:`ToolError` when the tool refused."""
        if not self._ready:
            self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "vexa-agent-worker", "version": "1"}})
            self._rpc("notifications/initialized", {}, notify=True)
            self._ready = True
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(b.get("text", "") for b in (res.get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "text")
        if not text and res.get("structuredContent") is not None:
            text = json.dumps(res["structuredContent"])
        if res.get("isError"):
            raise ToolError(text or "the tool reported an error and said nothing else")
        return text

    def close(self) -> None:
        if self._owns:
            self._client.close()


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def _is_not_a_link(error: str) -> bool:
    """The parser's own answer that this is not a meeting link it supports: HTTP 422."""
    return _first_line(error).startswith("HTTP 422")


def refusal(error: str) -> dict:
    """A refused send, typed by what the deciding service said (``vexa_mcp.tool_errors`` renders it:
    the readable line first, the unwrapped body last)."""
    line = _first_line(error)
    body: dict = {}
    last = (error or "").strip().splitlines()[-1] if (error or "").strip() else ""
    try:
        parsed = json.loads(last)
        body = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        body = {}
    status = None
    if line.startswith("HTTP "):
        head = line.split()[1] if len(line.split()) > 1 else ""
        status = int(head) if head.isdigit() else None
    code = str(body.get("code") or "").strip()
    reason = str(body.get("reason") or "").strip()
    return {"source": "request_meeting_bot",
            "kind": code or reason or (f"http_{status}" if status else "refused"),
            "status": status, "detail": line or "the request was refused"}


def _platform(name: str) -> str:
    return _PLATFORM_NAMES.get(name, name or "meeting")


def outcome_of(answer: str) -> dict:
    """What a successful ``request_meeting_bot`` answer means: a new bot, the bot already there, or a
    conflict that sent nothing (a meeting row for this link exists but no bot is in it)."""
    try:
        obj = json.loads(answer)
    except ValueError:
        obj = {}
    obj = obj if isinstance(obj, dict) else {}
    if obj.get("status") == "already_exists":
        meeting = obj.get("meeting") if isinstance(obj.get("meeting"), dict) else {}
        status = str(meeting.get("status") or "").lower()
        if status in LIVE_STATUSES:
            return {"outcome": "already_there", "meeting": meeting}
        detail = obj.get("detail")
        return {"outcome": "refused", "meeting": meeting, "refusal": {
            "source": "request_meeting_bot", "kind": "conflict", "status": 409,
            "detail": (str(detail) if isinstance(detail, str) and detail else
                       f"a meeting for this link already exists ({status or 'state unknown'}) and "
                       f"no new bot was started for it")}}
    return {"outcome": "requested", "meeting": obj}


def act(link: str, caller: McpCaller, *, new_id: Callable[[], str] = lambda: uuid.uuid4().hex[:12]
        ) -> "tuple[list[dict], Optional[dict]]":
    """Run the link through the parser and, if it is a meeting link, send the bot.

    Returns ``(events, result)``. ``result`` is None when the link is not a supported meeting link
    (the turn is an ordinary one and nothing is emitted); otherwise it is the ``meeting-link`` event,
    also the last item of ``events``."""
    try:
        parsed_text = caller.call("parse_meeting_link", {"meeting_url": link})
    except ToolError as e:
        if _is_not_a_link(str(e)):
            return [], None
        log.warning("meeting link %s not checked: %s", redact_url(link), redact_urls_in(_first_line(str(e))))
        ev = {"type": "meeting-link", "outcome": "unchecked", "url": link,
              "refusal": {"source": "parse_meeting_link", "kind": "unavailable", "status": None,
                          "detail": _first_line(str(e)) or "the link could not be checked"}}
        return [ev], ev
    except (httpx.HTTPError, OSError, ValueError) as e:
        log.warning("meeting link %s not checked: %s", redact_url(link), redact_urls_in(e))
        ev = {"type": "meeting-link", "outcome": "unchecked", "url": link,
              "refusal": {"source": "parse_meeting_link", "kind": "unreachable", "status": None,
                          "detail": f"{type(e).__name__}: {e}"}}
        return [ev], ev
    try:
        parsed = json.loads(parsed_text)
    except ValueError:
        parsed = {}
    parsed = parsed if isinstance(parsed, dict) else {}
    call_id = f"meeting-link-{new_id()}"
    events: list[dict] = [{"type": "tool-call", "tool": SEND_TOOL, "args": {"meeting_url": link},
                           "callId": call_id}]
    try:
        answer = caller.call("request_meeting_bot", {"meeting_url": link})
    except ToolError as e:
        why = refusal(str(e))
        result = {"outcome": "refused", "meeting": {}, "refusal": why}
        events.append({"type": "tool-result", "callId": call_id, "tool": SEND_TOOL, "ok": False,
                       "summary": why["detail"]})
    except (httpx.HTTPError, OSError, ValueError) as e:
        why = {"source": "request_meeting_bot", "kind": "unreachable", "status": None,
               "detail": f"{type(e).__name__}: {e}"}
        result = {"outcome": "refused", "meeting": {}, "refusal": why}
        events.append({"type": "tool-result", "callId": call_id, "tool": SEND_TOOL, "ok": False,
                       "summary": why["detail"]})
    else:
        result = outcome_of(answer)
        events.append({"type": "tool-result", "callId": call_id, "tool": SEND_TOOL,
                       "ok": result["outcome"] != "refused",
                       "summary": answer[:200] if result["outcome"] != "refused"
                       else result["refusal"]["detail"]})
        if result["outcome"] != "refused":
            artifact = _bot_artifact(answer)
            if artifact:
                events.append(artifact)
    meeting = result.get("meeting") or {}
    ev = {"type": "meeting-link", "outcome": result["outcome"], "url": link,
          "platform": str(meeting.get("platform") or parsed.get("platform") or ""),
          "native_meeting_id": str(meeting.get("native_meeting_id")
                                   or parsed.get("native_meeting_id") or "")}
    if meeting.get("id") is not None:
        ev["meeting_id"] = str(meeting.get("id"))
    if meeting.get("status"):
        ev["status"] = str(meeting.get("status"))
    if result.get("refusal"):
        ev["refusal"] = result["refusal"]
        log.warning("bot for %s refused: %s/%s %s", redact_url(link), ev["refusal"]["source"],
                    ev["refusal"]["kind"], redact_urls_in(ev["refusal"]["detail"]))
    events.append(ev)
    return events, ev


def preamble(ev: Optional[dict]) -> str:
    """What the model is told at the top of the turn: the person's message was a meeting link and the
    platform has already acted on it. Empty when nothing was sent."""
    if not ev or ev.get("outcome") == "unchecked":
        return ""
    where = f"the {_platform(ev.get('platform', ''))} meeting {ev.get('native_meeting_id') or ''}".strip()
    if ev["outcome"] == "requested":
        what = (f"a bot was requested for {where}. It joins once someone in the meeting admits it.")
    elif ev["outcome"] == "already_there":
        what = (f"a bot is already in {where} (status: {ev.get('status') or 'active'}), so no "
                f"second bot was sent.")
    else:
        r = ev.get("refusal") or {}
        what = (f"the request for {where} was refused ({r.get('kind')}): {r.get('detail')}. Report "
                f"that reason as given and do not retry it another way.")
    return ("## The person's message is a meeting link — the platform already acted on it\n\n"
            "Their whole message is one meeting link, which means: send the bot now. The platform "
            "sent that request itself, through `request_meeting_bot` on your own toolbelt, before "
            f"this turn started: {what}\n\n"
            "Tell them that in one short sentence, in their language. Do not call "
            "`request_meeting_bot` again for this link, do not ask whether to send it, and do not "
            "give them a link into the app.\n\n")


def send(link: str, endpoint: "tuple[str, dict]") -> "tuple[list[dict], Optional[dict]]":
    """:func:`act` over the MCP at ``endpoint`` — the ``(url, headers)`` of the turn's attachment."""
    url, headers = endpoint
    caller = McpCaller(url, headers)
    try:
        return act(link, caller)
    finally:
        caller.close()
