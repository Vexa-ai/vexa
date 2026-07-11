"""``create_app(gateway_url, ...) -> FastAPI`` — the Vexa MCP service (v0.12).

Port of 0.10.6 ``services/mcp/main.py`` reduced to the tools whose REST routes EXIST on
the v0.12 public API (the gateway — ``core/gateway/services/gateway/src/gateway/app.py``).
Every tool is a thin FastAPI route; ``FastApiMCP`` derives the MCP tool surface from them
and mounts the streamable-HTTP MCP transport at ``/mcp``.

Auth: the caller's credential (``Authorization: Bearer <key>`` / raw ``Authorization`` /
``X-API-Key``) is treated as the Vexa API key and forwarded to the gateway as ``X-API-Key``
— the gateway (not this service) resolves it to a user and enforces scopes. Stateless:
no DB, no redis, never reaches past the gateway.

The gateway transport is injectable (``transport=httpx.MockTransport`` in the tests) so the
conformance tests drive the SHIPPED app in-process with a fake gateway — the repo's test idiom.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
import mcp.types as mcp_types
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel, Field, model_validator

from .link_parser import ParseMeetingLinkResponse, parse_meeting_url
from .prompts import PROMPTS, get_prompt_result

_DEFAULT_GATEWAY_URL = "http://gateway:8000"

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


class ParseMeetingLinkRequest(BaseModel):
    meeting_url: str = Field(..., description="Full meeting URL to parse.")


def create_app(
    gateway_url: Optional[str] = None,
    *,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> FastAPI:
    """Build the MCP service app.

    ``gateway_url`` — the PUBLIC API base (env ``GATEWAY_URL``, compose ``http://gateway:8000``).
    ``transport``   — optional httpx transport override; the tests inject ``httpx.MockTransport``
                      so the shipped forwarding path runs with no network.
    """
    base_url = (gateway_url or os.getenv("GATEWAY_URL") or _DEFAULT_GATEWAY_URL).rstrip("/")

    _vexa_env = os.getenv("VEXA_ENV", "development")
    _public_docs = _vexa_env != "production"
    app = FastAPI(
        title="Vexa MCP Service (v0.12)",
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
            detail: Any
            try:
                detail = http_err.response.json()
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
        return {"status": "ok", "service": "mcp"}

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

    @app.put("/bot-config/{meeting_platform}/{meeting_id}", operation_id="update_bot_config")
    async def update_bot_config(
        meeting_id: str,
        data: UpdateBotConfig,
        meeting_platform: str = "google_meet",
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Update the configuration of an active bot (e.g., changing the transcription language).
        """
        url = f"{base_url}/bots/{meeting_platform}/{meeting_id}/config"
        return await make_request("PUT", url, api_key, data.model_dump())

    @app.delete("/bot/{meeting_platform}/{meeting_id}", operation_id="stop_bot")
    async def stop_bot(
        meeting_id: str,
        meeting_platform: str = "google_meet",
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Remove an active bot from a meeting.
        """
        return await make_request("DELETE", f"{base_url}/bots/{meeting_platform}/{meeting_id}", api_key)

    @app.get("/meetings", operation_id="list_meetings")
    async def list_meetings(
        limit: Optional[int] = Query(20, ge=1, le=100, description="Max meetings to return (default 20)"),
        offset: Optional[int] = Query(0, ge=0, description="Number of meetings to skip"),
        status: Optional[str] = Query(None, description="Filter by status: active, completed, failed"),
        platform: Optional[str] = Query(None, description="Filter by platform: google_meet, teams, zoom"),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        List meetings associated with your API key (pagination + status/platform filters).
        """
        params: Dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if status:
            params["status"] = status
        if platform:
            params["platform"] = platform
        return await make_request("GET", f"{base_url}/meetings", api_key, params=params or None)

    @app.get("/meeting-transcript/{meeting_platform}/{meeting_id}", operation_id="get_meeting_transcript")
    async def get_meeting_transcript(
        meeting_id: str,
        meeting_platform: str = "google_meet",
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        Get the real-time transcript for a meeting (segments with speaker, timestamp, text).
        Can be called during or after the meeting.
        """
        return await make_request("GET", f"{base_url}/transcripts/{meeting_platform}/{meeting_id}", api_key)

    @app.get("/recordings", operation_id="list_recordings")
    async def list_recordings(
        limit: int = 50,
        offset: int = 0,
        meeting_db_id: Optional[int] = None,
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        List recordings for the authenticated user. Wraps: GET /recordings
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

    # ---------------------------
    # MCP mount + prompts
    # ---------------------------
    mcp = FastApiMCP(app, headers=["authorization", "x-api-key"])

    @mcp.server.list_prompts()
    async def _list_prompts() -> mcp_types.ListPromptsResult:
        return mcp_types.ListPromptsResult(prompts=list(PROMPTS.values()))

    @mcp.server.get_prompt()
    async def _get_prompt(name: str, arguments: Optional[Dict[str, str]] = None) -> mcp_types.GetPromptResult:
        return get_prompt_result(name, arguments)

    mcp.mount_http()
    app.state.mcp = mcp
    return app
