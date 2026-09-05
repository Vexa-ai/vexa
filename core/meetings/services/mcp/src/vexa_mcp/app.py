"""``create_app(gateway_url, ...) -> FastAPI`` — the Vexa MCP service (v0.12).

Port of 0.10.6 ``services/mcp/main.py`` reduced to the tools whose REST routes EXIST on
the v0.12 public API (the gateway — ``core/gateway/services/gateway/src/gateway/app.py``).
Every tool is a thin FastAPI route; ``FastApiMCP`` derives the MCP tool surface from them
and mounts the streamable-HTTP MCP transport at ``/mcp``. The mount uses an ASGI
passthrough so a sessioned ``GET /mcp`` can start its SSE response immediately
(#921) — fastapi-mcp's buffered adapter never completes for an open EventSource.

Auth: the caller's credential (``Authorization: Bearer <key>`` / raw ``Authorization`` /
``X-API-Key``) is treated as the Vexa API key and forwarded to the gateway as ``X-API-Key``
— the gateway (not this service) resolves it to a user and enforces scopes. Stateless:
no DB, no redis, never reaches past the gateway.

The gateway transport is injectable (``transport=httpx.MockTransport`` in the tests) so the
conformance tests drive the SHIPPED app in-process with a fake gateway — the repo's test idiom.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import mcp.types as mcp_types
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from . import bind as bind_mod
from . import discover as discover_mod
from . import notices as notices_mod
from . import register as register_mod
from .link_parser import ParseMeetingLinkResponse, parse_meeting_url
from .prompts import PROMPTS, get_prompt_result
from .streamable_http import install_streaming_http_transport
from .tool_errors import install_structured_tool_errors, unwrap_detail

_DEFAULT_GATEWAY_URL = "http://gateway:8000"

# --- report_issue (agent-filed tickets) -------------------------------------
# Caps are defensive, not cosmetic: this route forwards caller-supplied text to an
# operator webhook, so every field is bounded before it leaves the process.
_MAX_TEXT_CHARS = 2000
_MAX_LOGS_CHARS = 4000
# Linode's ticket shape (POST /v4/support/tickets): summary 1-64, description 1-65,000. We store
# the same canonical pair so the MCP tool, the future API endpoint and the docs form all land one
# shape in the sink — the agent-facing arguments below are composed into it server-side.
_MAX_SUMMARY_CHARS = 64
# Whole-body ceiling. The handler caps every field, but a caller can still push megabytes at the
# JSON parser; this refuses before parsing. NOTE this is the HANDLER's cap — the public
# (key-less) door must ALSO carry a body cap + per-IP limit at the GATEWAY layer (see README).
_MAX_BODY_BYTES = 64 * 1024
_FINGERPRINT_SAMPLE_CHARS = 200
# Default salt for the caller fingerprint. A deployment SHOULD set
# VEXA_TICKET_FINGERPRINT_SALT so fingerprints are not comparable across deployments.
_DEFAULT_CALLER_SALT = "vexa-mcp-report-issue"


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Trim + hard-cap a caller-supplied string. Returns None for empty/blank input."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _fingerprint(deployment: str, what_happened: str) -> str:
    """Stable dedupe key: deployment + the first 200 chars of what_happened."""
    material = f"{deployment.strip().lower()}|{what_happened.strip()[:_FINGERPRINT_SAMPLE_CHARS]}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _summary_of(what_happened: str) -> str:
    """The Linode-shaped `summary` (1-64 chars): the first line/clause of what happened."""
    first_line = what_happened.strip().splitlines()[0].strip()
    if len(first_line) <= _MAX_SUMMARY_CHARS:
        return first_line
    return first_line[: _MAX_SUMMARY_CHARS - 1].rstrip() + "\u2026"


def _description_of(data: "ReportIssue") -> str:
    """The Linode-shaped `description`: the whole story, composed from the agent's answers."""
    parts = [f"What I tried:\n{data.what_i_tried}", f"What happened:\n{data.what_happened}"]
    where = data.deployment + (f" {data.version}" if data.version else "")
    parts.append(f"Deployment: {where}")
    if data.native_meeting_id:
        parts.append(f"Meeting: {data.platform or 'unknown platform'} / {data.native_meeting_id}")
    if data.logs:
        parts.append(f"Logs:\n{data.logs}")
    return "\n\n".join(parts)


# --- ticket sink adapters ----------------------------------------------------
# The sink is an OPERATOR surface. `raw` (the default) posts the canonical ticket payload to an
# opaque webhook — byte-for-byte what self-hosters already get. `github` maps the same payload
# onto GitHub's issue API so a deployment can use an issue tracker it already runs as the sink,
# with no new infrastructure. Nothing about the payload's construction changes between the two;
# only the wire shape of this one hop does.
_SINK_FORMAT_RAW = "raw"
_SINK_FORMAT_GITHUB = "github"
_DEFAULT_SINK_LABELS = "state: incoming"
_GITHUB_API_VERSION = "2022-11-28"


def _sink_format() -> str:
    """Operator-selected wire shape for the sink hop. Unknown/unset → `raw` (today's behaviour)."""
    value = (os.getenv("VEXA_TICKET_SINK_FORMAT") or "").strip().lower()
    return _SINK_FORMAT_GITHUB if value == _SINK_FORMAT_GITHUB else _SINK_FORMAT_RAW


def _sink_labels() -> List[str]:
    """Labels applied to a github-format ticket. Comma-separated; default `state: incoming`."""
    raw = os.getenv("VEXA_TICKET_SINK_LABELS")
    if raw is None or not raw.strip():
        raw = _DEFAULT_SINK_LABELS
    return [label.strip() for label in raw.split(",") if label.strip()]


def _github_issue_body(payload: Dict[str, Any]) -> str:
    """Render the canonical ticket payload as the markdown body of a GitHub issue.

    Every field the sink would have received in `raw` appears here — nothing is dropped, because
    the issue IS the ticket on this deployment. The meeting join key gets its own heading: it is
    what lines the reporter's account up against our own record of the same meeting.
    """
    lines: List[str] = []
    lines.append("_Filed by a calling agent through the Vexa MCP `report_issue` tool._")
    lines.append("")
    lines.append("### What I tried")
    lines.append(str(payload.get("what_i_tried") or "—"))
    lines.append("")
    lines.append("### What happened")
    lines.append(str(payload.get("what_happened") or "—"))
    lines.append("")
    lines.append("### Join key")
    if payload.get("native_meeting_id"):
        lines.append(f"- **native_meeting_id:** `{payload['native_meeting_id']}`")
        lines.append(f"- **platform:** `{payload.get('platform') or 'unknown'}`")
        entity = payload.get("entity")
        if isinstance(entity, dict):
            lines.append(f"- **resolved entity:** `{entity.get('type')}` → `{entity.get('url')}`")
        else:
            lines.append("- **resolved entity:** none (not owned by the calling key, or not found)")
    else:
        lines.append("- none supplied — this ticket is not bound to a meeting.")
    lines.append("")
    lines.append("### Deployment")
    lines.append(f"- **deployment:** {payload.get('deployment')}")
    lines.append(f"- **version:** {payload.get('version') or 'not stated'}")
    lines.append(f"- **severity:** {payload.get('severity') if payload.get('severity') is not None else 'not stated'}")
    lines.append("")
    if payload.get("logs"):
        lines.append("### Logs")
        truncated = " (truncated server-side)" if payload.get("logs_truncated") else ""
        lines.append(f"Pasted by the reporting agent{truncated}:")
        lines.append("")
        lines.append("```")
        lines.append(str(payload["logs"]))
        lines.append("```")
        lines.append("")
    lines.append("### Provenance")
    lines.append(f"- **source:** `{payload.get('source')}` · **tool:** `{payload.get('tool')}`")
    lines.append(f"- **reported_at:** `{payload.get('reported_at')}`")
    lines.append(f"- **fingerprint:** `{payload.get('fingerprint')}` (content-derived, for dedupe)")
    lines.append(
        f"- **caller_fingerprint:** `{payload.get('caller_fingerprint')}` "
        "(salted hash of the calling key — never the key itself)"
    )
    return "\n".join(lines)


def _sink_request(payload: Dict[str, Any], sink_token: str) -> tuple:
    """(headers, json_body) for the sink hop, per VEXA_TICKET_SINK_FORMAT.

    `raw` is byte-unchanged from before the switch existed: the canonical payload, with an
    optional bearer token. `github` maps it onto `{title, body, labels}` with GitHub's headers.
    """
    if _sink_format() == _SINK_FORMAT_GITHUB:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }
        if sink_token:
            headers["Authorization"] = f"Bearer {sink_token}"
        body: Dict[str, Any] = {
            "title": payload.get("summary"),
            "body": _github_issue_body(payload),
        }
        labels = _sink_labels()
        if labels:
            body["labels"] = labels
        return headers, body

    headers = {"Content-Type": "application/json"}
    if sink_token:
        headers["Authorization"] = f"Bearer {sink_token}"
    return headers, payload


def _caller_fingerprint(api_key: str) -> str:
    """A pseudonymous, stable handle for the caller — NEVER the API key itself.

    Deliberate choice: the ticket sink is an operator surface, not an auth boundary, so it
    receives a salted keyed FINGERPRINT of the key instead of the credential. That is enough to
    join two tickets from the same account (and, with the same salt, to match an account
    server-side) while a leak of the sink or its logs leaks no usable Vexa credential.
    The raw key is forwarded to the GATEWAY only, exactly as every other tool does. The raw key
    is never stored, logged, or returned.

    ``blake2b`` keyed with the deployment salt, not a bare SHA-256: this is a keyed fingerprint,
    not password storage (there is nothing here to verify a secret AGAINST), and the keyed
    construction is the right primitive for it — a plain digest of a low-entropy input is
    guessable by whoever holds the salt-free hash.
    """
    salt = os.getenv("VEXA_TICKET_FINGERPRINT_SALT") or _DEFAULT_CALLER_SALT
    # codeql[py/weak-sensitive-data-hashing] lgtm[py/weak-sensitive-data-hashing]: this is a keyed
    # fingerprint of a credential used as a sink handle, never password storage or verification —
    # the key is the secret and the digest is never compared against user input; a slow hash here
    # would only slow every ticket. Reviewed 2026-09-05 (v0.12.27 car 11).
    return hashlib.blake2b(  # codeql[py/weak-sensitive-data-hashing] lgtm[py/weak-sensitive-data-hashing]
        api_key.encode("utf-8"),
        key=salt.encode("utf-8")[:64],   # blake2b keys are capped at 64 bytes
        digest_size=8,                   # 8 bytes → the same 16 hex chars this has always emitted
    ).hexdigest()

# Standard bearer-token auth parsing. We treat the token value as the Vexa API key.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """Extract the API key from standard HTTP auth (0.10.6-compatible).

    Preferred: ``Authorization: Bearer <token>``. Back-compat: raw ``Authorization``
    or ``X-API-Key``. The token is forwarded to the gateway as ``X-API-Key``.
    """
    token: Optional[str] = None

    if creds and (creds.credentials or "").strip():
        token = creds.credentials.strip()
    elif authorization and authorization.strip():
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        else:
            token = authorization.strip()
    elif x_api_key and x_api_key.strip():
        token = x_api_key.strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing credentials (send Authorization: Bearer <VEXA_API_KEY>).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


# ---------------------------
# Request models
# ---------------------------
class RequestMeetingBot(BaseModel):
    meeting_url: Optional[str] = Field(
        None,
        description=(
            "Full meeting URL. If provided, Vexa will parse it and extract platform/native_meeting_id/passcode.\n"
            "Example (Teams Free): https://teams.live.com/meet/9361792952021?p=IXw5JhZRdoBvKnUXPy"
        ),
    )
    native_meeting_id: Optional[str] = Field(
        None,
        description=(
            "The meeting identifier.\n"
            "- Google Meet: meeting code like 'abc-defg-hij'\n"
            "- Microsoft Teams: numeric meeting ID only (10-15 digits) from teams.live.com/meet/<id>\n"
            "- Zoom: numeric meeting ID only (10-11 digits)\n"
            "- Jitsi: ALWAYS pass meeting_url (the full room URL) — a jitsi room is deployment-scoped,\n"
            "  so a bare room name is rejected (422); the id is derived from the URL"
        ),
    )
    language: Optional[str] = Field(None, description="Optional language code for transcription (e.g., 'en', 'es'). If not specified, auto-detected")
    bot_name: Optional[str] = Field(None, description="Optional custom name for the bot in the meeting")
    platform: str = Field("google_meet", description="The meeting platform (e.g., 'google_meet', 'teams', 'zoom', 'jitsi'). Default is 'google_meet'.")
    passcode: Optional[str] = Field(
        None,
        description=(
            "Meeting passcode.\n"
            "- Teams: passcode is the value of the `?p=` parameter in your Teams meeting link.\n"
            "- Zoom: passcode is the value of the `?pwd=` parameter (optional).\n"
            "- Jitsi: the room password, when the room is protected (optional)."
        ),
    )

    @model_validator(mode="after")
    def validate_meeting_identity(self):
        if (self.meeting_url and self.meeting_url.strip()) and (self.native_meeting_id and self.native_meeting_id.strip()):
            raise ValueError("Provide either meeting_url OR native_meeting_id, not both.")
        if not (self.meeting_url and self.meeting_url.strip()) and not (self.native_meeting_id and self.native_meeting_id.strip()):
            raise ValueError("Missing meeting identifier: provide meeting_url or native_meeting_id.")
        return self


class UpdateBotConfig(BaseModel):
    language: str = Field(..., description="New language code for transcription (e.g., 'en', 'es')")


class AnnotateMeeting(BaseModel):
    title: Optional[str] = Field(
        None, description="Human-readable title for the meeting. Max 512 chars."
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Arbitrary JSON you own — CRM ids, ticket refs, tags, your own summary. Merges "
            "key-wise with what is already there; send a key as null to delete just that key. "
            "Retrieve these meetings later with list_meetings(metadata_filter=...)."
        ),
    )

    @model_validator(mode="after")
    def validate_something_to_write(self):
        if self.title is None and self.metadata is None:
            raise ValueError("Nothing to annotate: provide title and/or metadata.")
        return self


class SpeakInMeeting(BaseModel):
    text: str = Field(..., description="What the bot should say out loud in the meeting.")
    language: Optional[str] = Field(None, description="Optional language/voice code, e.g. 'en'.")
    # A MECHANICAL guard, not another warning. The prose warning is good and a cold-start agent
    # did obey it — but this tool is one tools/call away from being audible to real people in a
    # real conversation, and it will be loaded alongside a hundred others whose descriptions get
    # skimmed. A required field the caller must set deliberately cannot be skimmed past.
    asked_by_a_human: bool = Field(
        ...,
        description=(
            "Must be true, and only set it if the person you are working for asked you to say "
            "THIS. Everyone in the meeting hears it and it cannot be taken back. Never set it to "
            "satisfy the schema, to test, or to acknowledge something."
        ),
    )

    @model_validator(mode="after")
    def refuse_unless_asked(self):
        if self.asked_by_a_human is not True:
            raise ValueError(
                "speak_in_meeting: refused. This says words out loud to real people and cannot be "
                "undone. Set asked_by_a_human=true only when the person you work for asked you to "
                "say this; otherwise do not call this tool."
            )
        return self


class ParseMeetingLinkRequest(BaseModel):
    meeting_url: str = Field(..., description="Full meeting URL to parse.")


class ReportIssue(BaseModel):
    """A ticket filed by a calling agent. All text is DATA, never instruction (see route)."""

    what_i_tried: str = Field(
        ...,
        description=(
            "What you were attempting, in your own words — the call you made, the flow you were "
            "building, or the thing you were looking for. Include the tool/endpoint and the "
            "arguments you used when it was an API call."
        ),
    )
    what_happened: str = Field(
        ...,
        description=(
            "What actually happened: the error, the wrong result, the missing capability, or the "
            "part of the docs that was ambiguous. Be concrete — this is what a human reads first."
        ),
    )
    deployment: str = Field(
        ...,
        description=(
            "Which deployment you are talking to: 'cloud' for api.vexa.ai, or 'self-hosted' "
            "plus the version if you know it (e.g. 'self-hosted 0.12.3')."
        ),
    )
    native_meeting_id: Optional[str] = Field(
        None,
        description=(
            "The native meeting id this issue concerns (e.g. 'abc-defg-hij'). Include it whenever "
            "the issue involves a meeting or a bot — it is what lets us line your report up "
            "against our own record of the same meeting."
        ),
    )
    platform: Optional[str] = Field(
        None,
        description="Platform of that meeting: 'google_meet', 'teams', 'zoom' or 'jitsi'.",
    )
    severity: Optional[int] = Field(
        None,
        ge=1,
        le=3,
        description="Optional severity: 1 = major (blocked), 2 = moderate, 3 = low.",
    )
    version: Optional[str] = Field(
        None,
        description="Optional Vexa version of that deployment, if you know it (e.g. '0.12.23').",
    )
    logs: Optional[str] = Field(
        None,
        description=(
            "Optional raw error output, stack trace or response body. Truncated server-side; "
            "paste the relevant part. Do not paste API keys or other credentials."
        ),
    )
    # NOTE (SSRF, closed by construction): there is deliberately NO url-shaped field here and no
    # field this service ever dereferences. A reporter who wants to point at something puts it in
    # the text, where it is stored and shown to a human and never fetched. The only URL this route
    # ever opens is VEXA_TICKET_SINK_URL, which comes from the operator's env, never from a caller.

    # Set server-side when `logs` was longer than the cap; forwarded so the reader knows
    # the paste is partial. Private so it never appears in the agent-facing tool schema.
    _logs_truncated: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def validate_and_clip(self):
        required = {
            "what_i_tried": self.what_i_tried,
            "what_happened": self.what_happened,
            "deployment": self.deployment,
        }
        missing = [name for name, value in required.items() if not (value or "").strip()]
        if missing:
            raise ValueError(f"Empty required field(s): {', '.join(missing)}.")
        self.what_i_tried = _clip(self.what_i_tried, _MAX_TEXT_CHARS)
        self.what_happened = _clip(self.what_happened, _MAX_TEXT_CHARS)
        self.deployment = _clip(self.deployment, 200)
        self.native_meeting_id = _clip(self.native_meeting_id, 200)
        self.platform = _clip(self.platform, 50)
        self.version = _clip(self.version, 100)
        self._logs_truncated = bool(self.logs and len(self.logs.strip()) > _MAX_LOGS_CHARS)
        self.logs = _clip(self.logs, _MAX_LOGS_CHARS)
        return self


# ---------------------------
# Meeting identity — ONE vocabulary across the whole surface
# ---------------------------
# Every tool that RETURNS a meeting returns ``platform`` + ``native_meeting_id`` (so does the
# public REST API: ``/transcripts/{platform}/{native_meeting_id}``). Three tools used to ACCEPT
# ``meeting_platform`` + ``meeting_id`` instead, so no tool's output could be fed to the next
# tool's input without a rename nothing documented. A model chaining calls reads
# ``native_meeting_id`` off one result and passes it to the next — the correct inference, and it
# failed. The canonical names below are the ones every tool returns; the old spellings stay as
# deprecated aliases so no existing client breaks.
_PLATFORM_DESC = (
    "Meeting platform: google_meet, teams, zoom, jitsi. Same value that request_meeting_bot, "
    "list_meetings and parse_meeting_link return as `platform`. Defaults to google_meet."
)
_ID_DESC = (
    "The meeting's native id — exactly the `native_meeting_id` returned by request_meeting_bot, "
    "list_meetings or parse_meeting_link (e.g. 'abc-defg-hij' for Google Meet)."
)
_LEGACY_PLATFORM_DESC = "DEPRECATED alias for `platform`. Use `platform`."
_LEGACY_ID_DESC = "DEPRECATED alias for `native_meeting_id`. Use `native_meeting_id`."
# A room code is not a meeting. `platform` + `native_meeting_id` names the ROOM, and a Google Meet
# link is reused for every session of a recurring call, so the pair resolves to the caller's NEWEST
# row on that link — the only session of a recurring meeting an agent could reach was the latest
# one. It could read an older transcript (list_meetings returns every row) and then had nowhere to
# write what it learned back to (fr_b6340167da32b8b6). The row id is the identity a meeting always
# has, every tool already returns it, and it never moves.
_DB_ID_DESC = (
    "The meeting's exact database row id — the `meeting_db_id` (or `id`) that list_meetings, "
    "request_meeting_bot, annotate_meeting and search_transcripts return. Takes precedence over "
    "`platform` + `native_meeting_id`, which name the ROOM and therefore resolve to the NEWEST "
    "meeting held on that link. Use it whenever you mean one specific past meeting."
)


#: The platforms the link parser can actually produce. A value outside this set is a caller
#: mistake, and answering it with an empty result set is the worst possible reply: a cold-start
#: agent reported that `platform="webex"` returned a confident `count: 0`, indistinguishable from
#: "nobody said that". Silence that looks like an answer is worse than an error.
VALID_PLATFORMS = ("google_meet", "teams", "zoom", "jitsi")


def _validate_platform(tool: str, platform: Optional[str]) -> Optional[str]:
    """Reject an unknown platform loudly rather than filtering everything away."""
    if platform is None or platform in VALID_PLATFORMS:
        return platform
    raise HTTPException(
        status_code=422,
        detail=(
            f"{tool}: unknown platform {platform!r}. Valid values: "
            f"{', '.join(VALID_PLATFORMS)}. Omit `platform` to search every platform."
        ),
    )


async def reject_unknown_arguments(request: Request) -> None:
    """Refuse query parameters the route does not declare.

    FastAPI ignores extras, so `get_meeting_transcript(limit=2)` — no such parameter — returned
    200 with the argument silently dropped. For an agent that is the worst failure mode available:
    a typo'd or hallucinated parameter looks like it worked, and the wrong answer is indistinguishable
    from the right one. Named near-misses are called out, because the commonest cause is a caller
    reaching for another tool's parameter name.
    """
    route = request.scope.get("route")
    if route is None or not getattr(route, "dependant", None):
        return
    known = {p.alias or p.name for p in route.dependant.query_params}
    unknown = sorted(set(request.query_params.keys()) - known)
    if not unknown:
        return
    # Substring matching alone misses the commonest real case — a caller reaching for another
    # tool's parameter name and getting the word order wrong (`meeting_id_native`). difflib
    # catches transpositions and typos that `in` does not.
    import difflib

    hints = []
    for got in unknown:
        near = difflib.get_close_matches(got, sorted(known), n=1, cutoff=0.5)
        if not near:
            near = [k for k in sorted(known) if k != got and (got in k or k in got)][:1]
        if near:
            hints.append(f"`{got}` — did you mean `{near[0]}`?")
    tool = getattr(route, "operation_id", None) or getattr(route, "name", "this tool")
    detail = {
        "tool": tool,
        "message": f"{tool}: unknown argument(s) {unknown}. This tool accepts {sorted(known)}.",
        "unknown": unknown,
        "accepted": sorted(known),
    }
    if hints:
        detail["hint"] = "; ".join(hints)
        detail["message"] = f"{tool}: unknown argument(s) — {detail['hint']}"
    raise HTTPException(status_code=422, detail=detail)


def _resolve_identity(
    tool: str,
    platform: Optional[str],
    native_meeting_id: Optional[str],
    legacy_platform: Optional[str],
    legacy_id: Optional[str],
    *,
    accepts_db_id: bool = False,
) -> tuple[str, str]:
    """Accept the canonical names or the deprecated aliases; fail with a message you can act on.

    Only reached when no `meeting_db_id` was supplied, so on the tools that take one the refusal
    names it too — a caller looking at a row they already hold should be told the shortest way in.
    """
    mid = (native_meeting_id or legacy_id or "").strip()
    plat = (platform or legacy_platform or "google_meet").strip()
    if not mid:
        also = (" Or pass `meeting_db_id` — the exact row id every tool returns, which addresses "
                "ONE meeting rather than the newest one in a room." if accepts_db_id else "")
        raise HTTPException(
            status_code=422,
            detail=(
                f"{tool}: missing the meeting id. Pass `native_meeting_id` (the field "
                f"request_meeting_bot / list_meetings / parse_meeting_link return), optionally "
                f"with `platform`.{also} Received: native_meeting_id=None, meeting_id=None."
            ),
        )
    return plat, mid


# What a client is told the moment it connects. Per-tool descriptions cannot carry orientation —
# this is the map: what Vexa is, and the one sequence that matters.
VEXA_INSTRUCTIONS = """\
Start every session with whats_waiting: it is the queue of what your person's Vexa needs right now, \
and for a new person it holds their first step. Follow what it says before anything else.

Vexa puts a transcription bot into a live meeting (Google Meet, Microsoft Teams, Zoom, Jitsi) and \
gives you the transcript while the meeting is still running.

The canonical flow:
  1. parse_meeting_link(meeting_url)  → platform + native_meeting_id (pure; no side effects)
  2. request_meeting_bot(meeting_url) → sends the bot. A human in the meeting must ADMIT it, so
     expect a delay between `requested` and `active`.
  3. get_meeting_transcript(platform, native_meeting_id) → segments, WHILE the meeting runs.
     Poll it to follow a live meeting; pass `since_index` to get only what is new since your last
     call instead of re-reading the whole transcript.
  4. stop_bot(platform, native_meeting_id) when you are done.

Identity: every tool returns `platform` + `native_meeting_id`, and every tool accepts those same \
two names. Feed one tool's output straight into the next.

Make the data yours: annotate_meeting attaches a title and arbitrary metadata — a CRM id, a \
ticket, tags, your own summary — to a meeting, DURING it or after it ended. Anything you put \
there you can find again with list_meetings(metadata_filter={...}), searched in the database \
rather than on one page. If you learn something durable about a meeting, write it back; that is \
what lets the next agent (or the next you) pick it up.

The bot is a PARTICIPANT, not just a recorder: get_meeting_chat reads the in-call chat, and \
speak_in_meeting says something out loud to everyone present. Speaking is irreversible and \
visible to real people — do it only when the person you work for asked you to say that thing, \
never to test and never on your own initiative.

A meeting with status `active` and zero segments usually means the bot has not been admitted yet, \
or nobody has spoken — it does not mean transcription is broken.
"""


def create_app(
    gateway_url: Optional[str] = None,
    *,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    assembly_env: Optional[dict] = None,
    assembly_transport: Optional[httpx.BaseTransport] = None,
) -> FastAPI:
    """Build the MCP service app.

    ``gateway_url`` — the PUBLIC API base (env ``GATEWAY_URL``, compose ``http://gateway:8000``).
    ``transport``   — optional httpx transport override; the tests inject ``httpx.MockTransport``
                      so the shipped forwarding path runs with no network.
    ``assembly_env`` / ``assembly_transport`` — the same seam for the DISCOVERY hop: which domains
                      this deployment carries, and how their manifests are fetched. `VEXA_MCP_ASSEMBLY_OFF`
                      skips assembly entirely, which is what the fourteen-tool surface tests want.
    """
    # REFUSE TO BOOT MISCONFIGURED (ADR-0026), the same first move every other adopted service
    # makes. This service shipped with eleven env keys, no declaration and no preflight — the one
    # service whose whole job is to be the product's front door was the one nothing checked.
    from .config_preflight import preflight
    preflight()

    base_url = (gateway_url or os.getenv("GATEWAY_URL") or _DEFAULT_GATEWAY_URL).rstrip("/")

    _vexa_env = os.getenv("VEXA_ENV", "development")
    _public_docs = _vexa_env != "production"
    app = FastAPI(
        title="Vexa MCP Service (v0.12)",
        # Applied to every route at once rather than injected per-signature: a tool that silently
        # ignores an argument it does not know is the worst reply available to an agent, so this
        # must hold for ALL of them, including any added later. The /mcp transport is an ASGI
        # mount rather than a route, so it is unaffected.
        dependencies=[Depends(reject_unknown_arguments)],
        docs_url="/docs" if _public_docs else None,
        redoc_url="/redoc" if _public_docs else None,
        openapi_url="/openapi.json" if _public_docs else None,
    )

    def get_headers(api_key: str) -> Dict[str, str]:
        return {"X-API-Key": api_key, "Content-Type": "application/json"}

    async def make_request(
        method: str,
        url: str,
        api_key: str,
        payload: Optional[dict] = None,
        params: Optional[dict] = None,
    ):
        try:
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                response = await client.request(
                    method, url, headers=get_headers(api_key), params=params, json=payload,
                )
                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()
        except httpx.HTTPStatusError as http_err:
            # THE ENVELOPE STOPS DOUBLING HERE. Upstream already answers `{"detail": {…}}`; handing
            # that whole body to `HTTPException(detail=…)` makes THIS service answer
            # `{"detail": {"detail": {…}}}`, and every hop that re-raises adds another layer. Unwrap
            # to the innermost object so the depth is a constant one, whatever the chain did.
            detail: Any
            try:
                detail = unwrap_detail(http_err.response.json())
            except Exception:
                detail = http_err.response.text
            raise HTTPException(status_code=http_err.response.status_code, detail=detail)
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Request timed out")
        except httpx.RequestError as req_err:
            raise HTTPException(status_code=503, detail=f"Request failed: {req_err}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    # --- liveness probe (compose healthcheck) — no auth, no downstream call.
    @app.get("/health", include_in_schema=False)
    async def health():
        """Liveness, plus the CONFIGURED/NOT_CONFIGURED state of each named capability (ADR-0026).

        A green health check on a service whose ticket sink is unset, or whose assembly reached no
        domain, is a green light on a product that quietly does less than the operator thinks. The
        tri-state is computed from the declaration, so it cannot drift from what the keys mean."""
        body = {"status": "ok", "service": "mcp"}
        try:
            from .config_preflight import capability_states
            body["capabilities"] = capability_states()
        except Exception:  # noqa: BLE001 — health must answer even if the declaration is unreadable
            pass
        return body

    # --- validation errors an agent can act on.
    # The stock message is "Input validation error: 'meeting_id' is a required property": it names
    # neither the tool nor what was actually sent, so a caller that guessed a near-miss parameter
    # name has no way to self-correct except by re-reading the schema. Name the tool, echo the
    # parameters received, and point at the near-miss.
    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        tool = "unknown_tool"
        route = request.scope.get("route")
        if route is not None:
            tool = getattr(route, "operation_id", None) or getattr(route, "name", None) or tool

        received = sorted(set(request.query_params.keys()))
        missing = [
            str(err["loc"][-1]) for err in exc.errors()
            if err.get("type") == "missing" and err.get("loc")
        ]
        hints = []
        for want in missing:
            near = [
                got for got in received
                if got != want and (got in want or want in got or got.endswith(want) or want.endswith(got))
            ]
            if near:
                hints.append(f"you sent `{near[0]}` — this tool expects `{want}`")
        detail = {
            "tool": tool,
            "message": f"{tool}: invalid arguments.",
            "missing": missing,
            "received": received or ["(none)"],
            "errors": exc.errors(),
        }
        if hints:
            detail["hint"] = "; ".join(hints)
            detail["message"] = f"{tool}: invalid arguments — {detail['hint']}."
        return JSONResponse(status_code=422, content=jsonable_encoder({"detail": detail}))

    # ---------------------------
    # Tools (each a FastAPI route; operation_id = MCP tool name)
    # ---------------------------
    @app.post("/parse-meeting-link", operation_id="parse_meeting_link", response_model=ParseMeetingLinkResponse)
    async def parse_meeting_link(
        data: ParseMeetingLinkRequest,
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Parse a meeting URL into platform/native_meeting_id/passcode.

        This is useful for agents: users can paste the full meeting URL, and Vexa will extract the
        exact fields needed by the REST API.
        """
        _ = api_key  # Auth required for MCP usage, even though parsing doesn't call the gateway.
        return parse_meeting_url(data.meeting_url).model_dump()

    @app.post("/request-meeting-bot", operation_id="request_meeting_bot")
    async def request_meeting_bot(
        data: RequestMeetingBot,
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Request a Vexa bot to join a meeting for transcription.

        Provide meeting_url OR native_meeting_id (+ platform, + passcode for Teams).
        Note: after a successful request, it typically takes about 10 seconds for the bot to join.
        """
        url = f"{base_url}/bots"
        payload = data.model_dump(exclude_none=True)
        meeting_url = payload.pop("meeting_url", None)
        if meeting_url:
            parsed = parse_meeting_url(meeting_url)
            payload["platform"] = parsed.platform
            payload["native_meeting_id"] = parsed.native_meeting_id
            # Only set passcode from URL if caller didn't explicitly pass one.
            payload.setdefault("passcode", parsed.passcode)
            # Forward raw URL for long Teams legacy links.
            if parsed.meeting_url:
                payload["meeting_url"] = parsed.meeting_url
            # Forward enterprise hostname for short Teams links.
            if parsed.teams_base_host:
                payload["teams_base_host"] = parsed.teams_base_host
        try:
            return await make_request("POST", url, api_key, payload)
        except HTTPException as e:
            # Common idempotency case: the meeting already exists for this key.
            if e.status_code == 409:
                meetings = await make_request("GET", f"{base_url}/meetings", api_key)
                platform = payload.get("platform")
                native = payload.get("native_meeting_id")
                if isinstance(meetings, list):
                    for m in meetings:
                        if isinstance(m, dict) and m.get("platform") == platform and m.get("native_meeting_id") == native:
                            return {"status": "already_exists", "meeting": m}
                return {"status": "already_exists", "detail": getattr(e, "detail", None)}
            raise

    @app.get("/bot-status", operation_id="get_bot_status")
    async def get_bot_status(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """
        Get the status of currently running bots under your API key.
        """
        return await make_request("GET", f"{base_url}/bots/status", api_key)

    @app.put("/bot-config", operation_id="update_bot_config")
    async def update_bot_config(
        data: UpdateBotConfig,
        native_meeting_id: Optional[str] = Query(None, description=_ID_DESC),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        meeting_id: Optional[str] = Query(None, deprecated=True, description=_LEGACY_ID_DESC),
        meeting_platform: Optional[str] = Query(None, deprecated=True, description=_LEGACY_PLATFORM_DESC),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Update the configuration of an active bot (e.g., changing the transcription language).
        Identify the meeting with `platform` + `native_meeting_id` — the exact field names
        request_meeting_bot, list_meetings and parse_meeting_link hand back.
        """
        plat, mid = _resolve_identity(
            "update_bot_config", platform, native_meeting_id, meeting_platform, meeting_id
        )
        return await make_request("PUT", f"{base_url}/bots/{plat}/{mid}/config", api_key, data.model_dump())

    @app.delete("/bot", operation_id="stop_bot")
    async def stop_bot(
        native_meeting_id: Optional[str] = Query(None, description=_ID_DESC),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        meeting_id: Optional[str] = Query(None, deprecated=True, description=_LEGACY_ID_DESC),
        meeting_platform: Optional[str] = Query(None, deprecated=True, description=_LEGACY_PLATFORM_DESC),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Remove an active bot from a meeting.
        Identify the meeting with `platform` + `native_meeting_id` — the exact field names
        request_meeting_bot, list_meetings and parse_meeting_link hand back.
        """
        plat, mid = _resolve_identity("stop_bot", platform, native_meeting_id, meeting_platform, meeting_id)
        return await make_request("DELETE", f"{base_url}/bots/{plat}/{mid}", api_key)

    @app.get("/meetings", operation_id="list_meetings")
    async def list_meetings(
        limit: Optional[int] = Query(20, ge=1, le=100, description="Max meetings to return (default 20)"),
        offset: Optional[int] = Query(0, ge=0, description="Number of meetings to skip"),
        status: Optional[str] = Query(None, description="Filter by status: active, completed, failed"),
        platform: Optional[str] = Query(None, description="Filter by platform: google_meet, teams, zoom"),
        metadata_filter: Optional[str] = Query(
            None,
            description=(
                'JSON object — return only meetings whose metadata (set via annotate_meeting) '
                'CONTAINS it, e.g. {"crm_deal":"acme-42"}. Extra keys on the meeting still match. '
                "Filtered in the database, so this searches your whole history, not just one page."
            ),
        ),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        List meetings associated with your API key (pagination + status/platform/metadata filters).

        `metadata_filter` is what makes this a query rather than a log: meetings you annotated with
        annotate_meeting can be found again by the values you put on them.
        """
        _validate_platform("list_meetings", platform)
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if status:
            params["status"] = status
        if platform:
            params["platform"] = platform
        if metadata_filter:
            # Validate here so a malformed filter is a clear tool error rather than a gateway 422
            # the agent has to decode. A filter that silently failed to apply would be worse than
            # an error: the agent would read a full unfiltered list as "these all match".
            try:
                parsed = json.loads(metadata_filter)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=422,
                    detail="list_meetings: 'metadata_filter' must be a JSON object string, e.g. {\"crm_deal\":\"acme-42\"}",
                )
            if not isinstance(parsed, dict):
                raise HTTPException(
                    status_code=422,
                    detail="list_meetings: 'metadata_filter' must be a JSON OBJECT, not a bare value or array.",
                )
            params["metadata"] = metadata_filter
        return await make_request("GET", f"{base_url}/meetings", api_key, params=params or None)

    @app.get("/meeting-transcript", operation_id="get_meeting_transcript")
    async def get_meeting_transcript(
        native_meeting_id: Optional[str] = Query(None, description=_ID_DESC),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        meeting_db_id: Optional[int] = Query(None, description=_DB_ID_DESC),
        since_index: Optional[int] = Query(
            None,
            ge=0,
            description=(
                "Return only segments at or after this index. Pass the `next_index` from your "
                "previous call to follow a live meeting without re-reading what you already have."
            ),
        ),
        meeting_id: Optional[str] = Query(None, deprecated=True, description=_LEGACY_ID_DESC),
        meeting_platform: Optional[str] = Query(None, deprecated=True, description=_LEGACY_PLATFORM_DESC),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Get the transcript for a meeting (segments with speaker, timestamp, text). Works DURING the
        meeting as well as after — poll it to follow a live one.

        Identify the meeting with `platform` + `native_meeting_id` — the exact field names
        request_meeting_bot, list_meetings and parse_meeting_link hand back — or with
        `meeting_db_id` for ONE exact past meeting. The pair names the ROOM, so on a recurring
        link it returns the NEWEST session held there; `meeting_db_id` never moves.

        To follow a live meeting cheaply, pass `since_index` = the `next_index` from your previous
        call; you get only what has been said since, instead of the whole transcript every time.
        """
        if meeting_db_id is not None:
            url = f"{base_url}/transcripts/by-id/{meeting_db_id}"
        else:
            plat, mid = _resolve_identity(
                "get_meeting_transcript", platform, native_meeting_id, meeting_platform,
                meeting_id, accepts_db_id=True,
            )
            url = f"{base_url}/transcripts/{plat}/{mid}"
        result = await make_request("GET", url, api_key)

        # The cursor is applied here rather than at the gateway: the scarce resource is the
        # CALLER's context window, not the hop to meeting-api. `total_segments`/`next_index` are
        # always reported against the full transcript so a caller can tell "nothing new" from
        # "nothing at all", and can resume after dropping its own state.
        if isinstance(result, dict):
            key = "segments" if isinstance(result.get("segments"), list) else (
                "transcripts" if isinstance(result.get("transcripts"), list) else None
            )
            if key:
                segments = result[key]
                total = len(segments)
                start = min(since_index, total) if since_index is not None else 0
                result = {**result, key: segments[start:], "total_segments": total, "next_index": total}
                if since_index is not None:
                    result["since_index"] = start
        return result

    @app.get("/transcript-search", operation_id="search_transcripts")
    async def search_transcripts(
        q: str = Query(..., min_length=1, description=(
            'What was said. Supports "quoted phrases", `or`, and `-excluded` terms. '
            "Malformed input never errors, so you can pass a user's words through as-is."
        )),
        limit: int = Query(20, ge=1, le=100, description="Max hits (default 20)."),
        offset: int = Query(0, ge=0),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        native_meeting_id: Optional[str] = Query(
            None, description="Restrict the search to ONE ROOM — every session held on that "
                              "link. " + _ID_DESC
        ),
        meeting_db_id: Optional[int] = Query(
            None, description="Restrict the search to ONE exact meeting. " + _DB_ID_DESC
        ),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Search what was SAID across your meetings. Use this instead of pulling transcripts and
        reading them — it returns ranked snippets, not whole transcripts.

        Each hit carries the meeting's `platform` + `native_meeting_id`, the speaker, the
        timestamp, and a snippet with the match wrapped in <mark> tags. Feed those identity fields
        straight into get_meeting_transcript when you need the full context around a hit.

        This is lexical search with stemming, not semantic: "renewal" finds "renewals" but will
        NOT find "extend the contract". Search the words people actually said.

        Related but different: list_meetings(metadata_filter=...) finds meetings you TAGGED;
        this finds meetings where something was SAID.
        """
        _validate_platform("search_transcripts", platform)
        params: Dict[str, Any] = {"q": q, "limit": limit, "offset": offset}
        if platform:
            params["platform"] = platform
        if native_meeting_id:
            params["native_meeting_id"] = native_meeting_id
        if meeting_db_id is not None:
            # Sent under the REST spelling. The row id travels as `meeting_db_id` on every tool
            # surface precisely so it can never be confused with the platform's string id.
            params["meeting_id"] = meeting_db_id
        return await make_request("GET", f"{base_url}/transcripts/search", api_key, params=params)

    # --- annotations: the caller's OWN description of a meeting -----------------------------
    # This is the join key between a Vexa meeting and everything else the agent knows. Without it
    # an agent can read a transcript and can do nothing with the fact afterwards: there is nowhere
    # to record that this meeting is the Acme renewal, nowhere to keep its own summary, and so no
    # way to find the meeting again by anything other than its platform id.
    @app.post("/meeting-annotate", operation_id="annotate_meeting")
    async def annotate_meeting(
        data: AnnotateMeeting,
        native_meeting_id: Optional[str] = Query(None, description=_ID_DESC),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        meeting_db_id: Optional[int] = Query(None, description=_DB_ID_DESC),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Set the meeting's title and/or attach arbitrary metadata to it. Works DURING a live meeting
        and after it has ended.

        To annotate ONE PAST meeting, pass `meeting_db_id`. `platform` + `native_meeting_id` name
        the ROOM, and a recurring link is the same room every week, so the pair always writes to
        the NEWEST meeting held there — which is not the one you just read if you read an older
        one.

        `metadata` is your own JSON — a CRM id, a ticket, tags, your own summary, anything.
        It always MERGES key-wise, and sending a key as null deletes exactly that key. You can
        only ever affect keys you name: other agents and the human may have written annotations
        here that you cannot see, and nothing you send can destroy them.

        Find these meetings again with
        `list_meetings(metadata_filter='{"your_key":"your_value"}')` — note the filter is a JSON
        STRING, not an object.
        """
        plat = mid = None
        if meeting_db_id is not None:
            url = f"{base_url}/meetings/{meeting_db_id}/annotate"
        else:
            plat, mid = _resolve_identity("annotate_meeting", platform, native_meeting_id,
                                          None, None, accepts_db_id=True)
            url = f"{base_url}/meetings/{plat}/{mid}/annotate"
        body: Dict[str, Any] = {}
        if data.title is not None:
            body["title"] = data.title
        if data.metadata is not None:
            body["metadata"] = data.metadata
        row = await make_request("POST", url, api_key, body)

        # Return WHAT WAS WRITTEN, not the whole meeting row. Annotating is a small write to one
        # field; the full row carries object-storage paths, a playback URL and a reference to a
        # ~69 MB audio artifact, none of which the caller asked for and all of which land in a
        # calling model's context. Read paths still serve the full row — this trims the WRITE's
        # echo, which is the one place nobody requested any of it.
        if not isinstance(row, dict):
            return row
        row_data = row.get("data") if isinstance(row.get("data"), dict) else {}
        return {
            # The row's own identity, falling back to what was asked for. When the caller
            # addressed by db id there is no pair to fall back TO, so the row is the only source —
            # which is the right way round: it is what was actually written.
            "platform": row.get("platform", plat),
            "native_meeting_id": row.get("native_meeting_id", mid),
            "meeting_db_id": row.get("id", meeting_db_id),
            "status": row.get("status"),
            "title": row_data.get("title"),
            "metadata": row_data.get("metadata", {}),
        }

    # --- the interactive bot: our bot TALKS and reads the room ------------------------------
    # No notetaker MCP exposes this, because no notetaker has it: their bot is a recorder. Ours is
    # a participant, so an agent connected here can answer a question out loud in the meeting it is
    # listening to. These wrap gateway routes that already exist and were simply unreachable.
    @app.post("/meeting-speak", operation_id="speak_in_meeting")
    async def speak_in_meeting(
        data: SpeakInMeeting,
        native_meeting_id: Optional[str] = Query(None, description=_ID_DESC),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        SPEAK OUT LOUD in a live meeting through the Vexa bot. Everyone in the meeting hears it.

        This is an irreversible, human-visible action in a real conversation — never call it to
        test, to acknowledge, or on your own initiative. Say something only when the person you are
        working for has asked you to say that thing.
        """
        plat, mid = _resolve_identity("speak_in_meeting", platform, native_meeting_id, None, None)
        return await make_request(
            "POST", f"{base_url}/bots/{plat}/{mid}/speak", api_key, data.model_dump(exclude_none=True)
        )

    @app.get("/meeting-chat", operation_id="get_meeting_chat")
    async def get_meeting_chat(
        native_meeting_id: Optional[str] = Query(None, description=_ID_DESC),
        platform: Optional[str] = Query(None, description=_PLATFORM_DESC),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Read the meeting's in-call chat messages. Complements the transcript: links, spellings and
        side comments land in chat and are never spoken aloud.
        """
        plat, mid = _resolve_identity("get_meeting_chat", platform, native_meeting_id, None, None)
        return await make_request("GET", f"{base_url}/bots/{plat}/{mid}/chat", api_key)

    @app.get("/recordings", operation_id="list_recordings")
    async def list_recordings(
        limit: int = Query(50, ge=1, le=200, description="Max recordings to return (default 50)."),
        offset: int = Query(0, ge=0, description="Number of recordings to skip."),
        meeting_db_id: Optional[int] = Query(
            None, description="Only this meeting's recordings (the `meeting_db_id` other tools return)."
        ),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        List the authenticated user's recordings, NEWEST FIRST, one page at a time.
        Wraps: GET /recordings

        Rows are summaries — id, meeting_id, status, timestamps, duration and the playback URL.
        Call get_recording(recording_id) for one recording's full media-file detail.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if meeting_db_id is not None:
            params["meeting_id"] = meeting_db_id
        return await make_request("GET", f"{base_url}/recordings", api_key, params=params)

    @app.get("/recordings/{recording_id}", operation_id="get_recording")
    async def get_recording(
        recording_id: int,
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Get a single recording and its media files. Wraps: GET /recordings/{recording_id}
        """
        return await make_request("GET", f"{base_url}/recordings/{recording_id}", api_key)

    @app.post("/report-issue", operation_id="report_issue")
    async def report_issue(
        data: ReportIssue,
        request: Request,
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Tell the Vexa maintainers that something went wrong. A human reads every ticket, and it cannot be withdrawn.

        Call this the moment you hit a problem with Vexa — you do not need to have solved it first:
        - something BROKE: an error, a bot that never joined, an empty transcript, a call that hung;
        - something was CONFUSING: docs or an error message left you guessing which argument to send;
        - something is MISSING: the capability you needed does not exist in the API.

        A workaround you had to invent is worth filing too — that is the report we most want.
        Write what you tried and what happened in your own words. Do not paste API keys, and do not
        paste your user's meeting content.

        File only when you hit this while doing REAL WORK for someone. If you are exploring,
        evaluating or testing this API, do not file — write your findings in your report instead.
        A ticket reaches humans at another company and cannot be withdrawn, and an evaluation run
        can otherwise open several in a single session.

        If the issue concerns a meeting or a bot, include `native_meeting_id` and `platform`. That is the
        join key: it lines your account of what happened up against our own record of the same
        meeting, which is the difference between a complaint and something we can diagnose. The
        meeting is resolved onto the ticket only if the key you are calling with owns it.

        Put links, if you have them, in the text — nothing you send here is ever fetched by us.

        One-way: nothing auto-replies and nothing auto-closes. Returns the ticket
        (`id` · `status` · `severity` · `opened` · `opened_by` · `entity`), plus `url` when the
        deployment's sink issues one — tell your human where the report landed. On a deployment
        with no ticket sink configured it returns 503 and nothing else is affected.
        """
        # Ticket text is DATA, never instruction. It is validated, capped, and forwarded verbatim
        # to the operator's sink — this service never interprets it, never dereferences anything in
        # it, and no agent of ours is ever steered by it. (Grow-Mouth § The ticket.)
        #
        # Whole-body ceiling, before the field-level caps: refuse the megabyte payload rather than
        # parse it. The authenticated door also sits behind the gateway's per-user rate limiter;
        # the key-less door in the design note needs a per-IP limit and this cap AT THE GATEWAY.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Ticket body too large (max {_MAX_BODY_BYTES} bytes). Trim `logs`.",
            )

        sink_url = (os.getenv("VEXA_TICKET_SINK_URL") or "").strip()
        if not sink_url:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Ticketing is not configured on this Vexa deployment. Set VEXA_TICKET_SINK_URL "
                    "on the MCP service to enable report_issue, or report the issue at "
                    "https://github.com/Vexa-ai/vexa/issues."
                ),
            )

        # Authenticate the caller BEFORE spending the operator's credential. The gateway is the only
        # authority on whether a key is real; this service never makes that call itself. Filing costs
        # the operator's sink token and writes into an operator surface, so a caller we cannot
        # authenticate never reaches the sink — with or without a meeting_id. Failing closed is the
        # point: an unverifiable caller is refused rather than filed on the operator's behalf.
        try:
            meetings = await make_request("GET", f"{base_url}/meetings", api_key)
        except HTTPException as exc:
            if exc.status_code in (401, 403):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid credentials (send Authorization: Bearer <VEXA_API_KEY>).",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from exc
            raise HTTPException(
                status_code=502,
                detail="Could not verify your credentials with the gateway; the ticket was not filed.",
            ) from exc

        # The entity pointer, authorisation-checked. The gateway answered above for the CALLER'S OWN
        # KEY, so a caller can only ever resolve a meeting they own — the check is the gateway's, not
        # a trust decision made here. Resolution stays best-effort: an unresolvable or unowned id
        # still files the ticket (with the id quoted as text, entity null), because a ticket we
        # refused to accept teaches us nothing.
        entity: Optional[Dict[str, Any]] = None
        if data.native_meeting_id:
            rows = meetings if isinstance(meetings, list) else (meetings or {}).get("meetings", [])
            for m in rows if isinstance(rows, list) else []:
                if not isinstance(m, dict):
                    continue
                if m.get("native_meeting_id") != data.native_meeting_id:
                    continue
                if data.platform and m.get("platform") != data.platform:
                    continue
                entity = {
                    "type": "meeting",
                    "id": data.native_meeting_id,
                    "platform": m.get("platform"),
                    "url": f"/transcripts/{m.get('platform')}/{data.native_meeting_id}",
                }
                break

        fingerprint = _fingerprint(data.deployment, data.what_happened)
        opened = datetime.now(timezone.utc).isoformat()
        caller = _caller_fingerprint(api_key)
        payload: Dict[str, Any] = {
            "source": "vexa-mcp",
            "tool": "report_issue",
            "reported_at": opened,
            "fingerprint": fingerprint,
            # Identity HINT, not the credential — see _caller_fingerprint().
            "caller_fingerprint": caller,
            # Canonical ticket pair (Linode-shaped), composed from the agent's answers so every
            # ticket surface lands ONE shape in the sink.
            "summary": _summary_of(data.what_happened),
            "description": _description_of(data),
            # The agent's own words, kept discrete for the reader and for later labelling.
            "what_i_tried": data.what_i_tried,
            "what_happened": data.what_happened,
            "deployment": data.deployment,
            "version": data.version,
            "severity": data.severity,
            "native_meeting_id": data.native_meeting_id,
            "platform": data.platform,
            "entity": entity,
            "logs": data.logs,
            "logs_truncated": data._logs_truncated,
        }

        sink_token = (os.getenv("VEXA_TICKET_SINK_TOKEN") or "").strip()
        headers, sink_payload = _sink_request(payload, sink_token)

        # Same injectable transport as the gateway hop (tests inject MockTransport). Note the
        # caller's API key is NOT sent on this hop — only its salted fingerprint, in the body.
        # The URL is the OPERATOR'S env value; no caller-supplied string is ever fetched.
        sink_body: Any = None
        try:
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                response = await client.post(sink_url, headers=headers, json=sink_payload)
                response.raise_for_status()
                if response.content:
                    try:
                        sink_body = response.json()
                    except Exception:
                        sink_body = None
        except httpx.HTTPStatusError as http_err:
            raise HTTPException(
                status_code=502,
                detail=f"Ticket sink rejected the report (status {http_err.response.status_code}).",
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Ticket sink timed out")
        except httpx.RequestError as req_err:
            raise HTTPException(status_code=503, detail=f"Ticket sink unreachable: {req_err}")

        # Ticket object, mirroring Linode's response (id · status · severity · opened · updated ·
        # opened_by · entity). This service holds no store, so the id is the sink's when the sink
        # returns one, and the content fingerprint otherwise.
        ticket_id = None
        ticket_url = None
        if isinstance(sink_body, dict):
            # `number` + `html_url` are GitHub's; `id`/`ticket_id`/`url` cover a raw webhook that
            # issues its own. Whatever the sink named the ticket, the calling agent gets it back so
            # it can tell its human WHERE the report went.
            if _sink_format() == _SINK_FORMAT_GITHUB:
                # GitHub carries both: `id` is the global db row, `number` is what a human quotes.
                ticket_id = sink_body.get("number") or sink_body.get("id")
            else:
                ticket_id = sink_body.get("id") or sink_body.get("ticket_id")
            ticket_url = sink_body.get("html_url") or sink_body.get("url")
        ack: Dict[str, Any] = {
            "id": ticket_id if ticket_id is not None else fingerprint,
            "status": "new",
            "severity": data.severity,
            "opened": opened,
            "updated": opened,
            "opened_by": caller,
            "entity": entity,
            "fingerprint": fingerprint,
            "message": "Thanks — a human reads this. Quote the ticket id if you follow up.",
        }
        if ticket_url:
            ack["url"] = ticket_url
        return ack

    # ---------------------------
    # ASSEMBLY (PRD decision 40) — the tools the DEPLOYED domains declare
    # ---------------------------
    # The fourteen tools above are this edge's own. Everything else it serves belongs to a domain:
    # meetings owns the bots and transcripts, identity owns the person, flows owns the reaction
    # engine, agent owns the desks. Each publishes a manifest at `/.well-known/mcp-tools.json`, this
    # asks the ones the deployment names, and every tool becomes a route here with
    # `operation_id=<name>` — the SAME mechanism the fourteen use, so an assembled tool and a
    # built-in one are indistinguishable to a client.
    #
    # BEFORE `FastApiMCP(app, ...)`, necessarily: the MCP surface is derived from this app's
    # OpenAPI, so a route added afterwards would be a tool nobody can see.
    #
    # AND IT FAILS THE BOOT. Every rule in `manifest.py` is a failure that is otherwise silent — a
    # domain's tools quietly missing, one name claimed twice, a manifest naming a route its service
    # does not serve. A server that started anyway would answer `tools/list` with a lie.
    #
    # A CONFIGURED DOMAIN THAT NEVER ANSWERS IS ONE OF THOSE, and it did NOT fail the boot until
    # now: `discover` swallowed every exception, so a refusing port cost a whole domain its tools
    # permanently and quietly. It refuses by name instead — after waiting the domain out, because a
    # cold `compose up` starts everything at once and the first refused connection is a race, not a
    # fault. That wait is why this call can take a few seconds on a cold start; see `discover`.
    app.state.assembly = None
    _assembly_env = assembly_env if assembly_env is not None else os.environ
    if not _assembly_env.get("VEXA_MCP_ASSEMBLY_OFF"):
        with httpx.Client(transport=assembly_transport) as _boot:
            _assembly, _openapi, _bases = discover_mod.discover(_boot, env=_assembly_env)
        _bound = bind_mod.verify(_assembly, _openapi)
        register_mod.register(app, _bound, _bases, transport=transport, env=_assembly_env)
        app.state.assembly = _assembly

    # ---------------------------
    # MCP mount + prompts
    # ---------------------------
    mcp = FastApiMCP(app, headers=["authorization", "x-api-key"])
    # Orientation at connect time. FastApiMCP has no `instructions` kwarg, but the lowlevel
    # Server it wraps carries the field the spec defines — a client that connects should not have
    # to infer what Vexa is from nine tool descriptions.
    mcp.server.instructions = VEXA_INSTRUCTIONS

    @mcp.server.list_prompts()
    async def _list_prompts() -> mcp_types.ListPromptsResult:
        return mcp_types.ListPromptsResult(prompts=list(PROMPTS.values()))

    @mcp.server.get_prompt()
    async def _get_prompt(name: str, arguments: Optional[Dict[str, str]] = None) -> mcp_types.GetPromptResult:
        return get_prompt_result(name, arguments)

    # Close every tool's argument schema. Without `additionalProperties: false` an unknown
    # argument is not rejected — fastapi-mcp silently DROPS it before the HTTP call, so the
    # server-side guard never sees it and the tool answers 200 as if the argument had been
    # honoured. A hallucinated or typo'd parameter then looks like it worked, and the wrong
    # answer is indistinguishable from the right one. Closing the schema moves the refusal to
    # where the argument actually is: the MCP validation layer.
    for _tool in mcp.tools:
        schema = getattr(_tool, "inputSchema", None)
        if isinstance(schema, dict) and schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)

    mcp.mount_http()
    # fastapi-mcp 0.4 buffers the ASGI response; a sessioned GET is an open SSE
    # stream and never completes that buffer — install a passthrough (#921).
    install_streaming_http_transport(mcp)
    # An upstream refusal reaches the agent as ONE string; fastapi-mcp writes it as prose with a JSON
    # document inside. Render it structurally instead — the actionable words first, the body once.
    install_structured_tool_errors(mcp)
    # STANDING NOTICES ride out on the meeting tools' results (`notices.py`) — the ones that
    # answered and the ones that were REFUSED (#1549). An agent reads what it asked for; a fact that
    # stays true between calls has to travel on that, or it travels on a tool somebody has to
    # remember to call — and a refusal is the result it most has to act on. Never an error, never a
    # new tool: when the deployment names no flows domain, or it does not answer inside two seconds,
    # the result is the result and the refusal is the refusal.
    notices_mod.install(mcp, transport=transport, env=_assembly_env)
    app.state.mcp = mcp
    return app
