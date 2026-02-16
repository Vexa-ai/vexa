import json
import os
import re
from urllib.parse import urlparse, parse_qs
from fastapi import FastAPI, Header, HTTPException, Depends, Response
from fastapi_mcp import FastApiMCP
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, model_validator
import httpx
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import mcp.types as mcp_types

app = FastAPI()

BASE_URL = os.getenv("API_GATEWAY_URL", "http://api-gateway:8000")

# Standard bearer-token auth parsing. We treat the token value as the Vexa API key.
bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------
# Dependencies & Utilities
# ---------------------------
async def get_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """
    Extract API key from standard HTTP auth.

    Industry-standard format for MCP over HTTP:
    - Authorization: Bearer <token>

    For backwards compatibility, we also accept:
    - Authorization: <token>
    - X-API-Key: <token>

    The extracted token value is treated as the Vexa API key and forwarded to the REST API as X-API-Key.
    """
    token: Optional[str] = None

    # Prefer parsed Bearer scheme (Authorization: Bearer <token>)
    if creds and (creds.credentials or "").strip():
        token = creds.credentials.strip()
    elif authorization and authorization.strip():
        # Back-compat: allow raw token in Authorization.
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        else:
            token = authorization.strip()
    elif x_api_key and x_api_key.strip():
        token = x_api_key.strip()

    if not token:
        # RFC 6750 hint; helps clients understand which scheme to use.
        raise HTTPException(
            status_code=401,
            detail="Missing credentials (send Authorization: Bearer <VEXA_API_KEY>).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def get_headers(api_key: str) -> Dict[str, str]:
    """Create headers with the provided API key"""
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }


# ---------------------------
# Request Models
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
            "- Zoom: numeric meeting ID only (10-11 digits)"
        ),
    )
    language: Optional[str] = Field(None, description="Optional language code for transcription (e.g., 'en', 'es'). If not specified, auto-detected")
    bot_name: Optional[str] = Field(None, description="Optional custom name for the bot in the meeting")
    platform: str = Field("google_meet", description="The meeting platform (e.g., 'google_meet', 'teams', 'zoom'). Default is 'google_meet'.")
    passcode: Optional[str] = Field(
        None,
        description=(
            "Meeting passcode.\n"
            "- Teams: passcode is the value of the `?p=` parameter in your Teams meeting link.\n"
            "- Zoom: passcode is the value of the `?pwd=` parameter (optional)."
        ),
    )

    @model_validator(mode="after")
    def validate_meeting_identity(self):
        if (self.meeting_url and self.meeting_url.strip()) and (self.native_meeting_id and self.native_meeting_id.strip()):
            # Avoid ambiguous precedence.
            raise ValueError("Provide either meeting_url OR native_meeting_id, not both.")
        if not (self.meeting_url and self.meeting_url.strip()) and not (self.native_meeting_id and self.native_meeting_id.strip()):
            raise ValueError("Missing meeting identifier: provide meeting_url or native_meeting_id.")
        return self


class UpdateBotConfig(BaseModel):
    language: str = Field(..., description="New language code for transcription (e.g., 'en', 'es')")


class UpdateMeetingData(BaseModel):
    name: Optional[str] = Field(None, description="Optional meeting name/title")
    participants: Optional[List[str]] = Field(None, description="Optional list of participant names")
    languages: Optional[List[str]] = Field(None, description="Optional list of language codes detected/used in the meeting")
    notes: Optional[str] = Field(None, description="Optional meeting notes or description")

class ParseMeetingLinkRequest(BaseModel):
    meeting_url: str = Field(..., description="Full meeting URL to parse.")

class ParseMeetingLinkResponse(BaseModel):
    platform: str
    native_meeting_id: str
    passcode: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)

class TranscriptShareLinkResponse(BaseModel):
    share_id: str
    url: str
    expires_at: str
    expires_in_seconds: int

class RecordingConfigUpdate(BaseModel):
    enabled: Optional[bool] = Field(None, description="Enable or disable recording for this user's bots.")
    capture_modes: Optional[List[str]] = Field(
        None,
        description="Capture modes: ['audio'], ['audio','video'], ['screenshot'], etc.",
    )

class MeetingBundleRequest(BaseModel):
    meeting_platform: str = Field(..., description="Meeting platform: google_meet, teams, zoom.")
    meeting_id: str = Field(..., description="Native meeting ID (same value used when requesting the bot).")
    include_segments: bool = Field(
        False,
        description="If true, include transcript segments inline. If false, segments are omitted to keep output small.",
    )
    include_share_link: bool = Field(
        True,
        description="If true, attempt to create a public share link for the transcript.",
    )
    share_ttl_seconds: Optional[int] = Field(
        86400,
        description="TTL for the share link in seconds (only used when include_share_link=true).",
    )
    include_recordings: bool = Field(
        True,
        description="If true, include recordings metadata from the transcript response.",
    )
    include_media_download_urls: bool = Field(
        False,
        description=(
            "If true, resolve download URLs for each recording media file (extra API calls). "
            "Useful post-meeting to fetch audio quickly."
        ),
    )


# ---------------------------
# Helper for async requests
# ---------------------------
async def make_request(
    method: str,
    url: str,
    api_key: str,
    payload: Optional[dict] = None,
    params: Optional[dict] = None,
):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(
                method,
                url,
                headers=get_headers(api_key),
                params=params,
                json=payload,
            )
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
    except httpx.HTTPStatusError as http_err:
        # Allow MCP transport to mark tool calls as errors (isError=true) when appropriate.
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

def _parse_meeting_url(meeting_url: str) -> ParseMeetingLinkResponse:
    url = (meeting_url or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="meeting_url cannot be empty")

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parse_qs(parsed.query or "")

    warnings: List[str] = []

    # Google Meet
    if host in {"meet.google.com"}:
        code = path.strip("/").split("/")[0] if path else ""
        if not re.fullmatch(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", code):
            raise HTTPException(status_code=422, detail="Invalid Google Meet URL: expected https://meet.google.com/abc-defg-hij")
        return ParseMeetingLinkResponse(platform="google_meet", native_meeting_id=code, passcode=None, warnings=warnings)

    # Teams Free (teams.live.com/meet/<digits>?p=<passcode>)
    if host.endswith("teams.live.com"):
        m = re.match(r"^/meet/(\d{10,15})/?$", path)
        if not m:
            raise HTTPException(status_code=422, detail="Unsupported teams.live.com URL format. Expected /meet/<10-15 digit id>.")
        native_id = m.group(1)
        passcode = (query.get("p") or [None])[0]
        if not passcode:
            warnings.append("Teams meeting link has no ?p= passcode. Many Teams meetings require it.")
        return ParseMeetingLinkResponse(platform="teams", native_meeting_id=native_id, passcode=passcode, warnings=warnings)

    # Teams enterprise link style (not supported)
    if host.endswith("teams.microsoft.com"):
        if "/l/meetup-join/" in path:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Unsupported Teams link type: teams.microsoft.com/l/meetup-join/... is not supported yet "
                    "(see issues #105, #110). Use a teams.live.com/meet/<id>?p=<passcode> link."
                ),
            )
        raise HTTPException(status_code=422, detail="Unsupported Teams URL host/path.")

    # Zoom
    if "zoom.us" in host:
        # Typical: /j/<id> or /w/<id>
        parts = [p for p in path.split("/") if p]
        native_id = ""
        if len(parts) >= 2 and parts[0] in {"j", "w"}:
            native_id = parts[1]
        if not re.fullmatch(r"^\d{10,11}$", native_id or ""):
            raise HTTPException(status_code=422, detail="Unsupported Zoom URL format. Expected https://zoom.us/j/<10-11 digit id>")
        passcode = (query.get("pwd") or [None])[0]
        return ParseMeetingLinkResponse(platform="zoom", native_meeting_id=native_id, passcode=passcode, warnings=warnings)

    raise HTTPException(status_code=422, detail="Unsupported meeting URL (unknown provider).")


# ---------------------------
# Endpoints (docstrings preserved)
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
    _ = api_key  # Auth required for MCP usage, even though parsing doesn't call backend.
    parsed = _parse_meeting_url(data.meeting_url)
    return parsed.model_dump()


@app.post("/request-meeting-bot", operation_id="request_meeting_bot")
async def request_meeting_bot(
    data: RequestMeetingBot,
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Request a Vexa bot to join a meeting for transcription.
    
    Args:
        native_meeting_id: The meeting identifier (see field description for platform-specific formats)
        language: Optional language code for transcription (e.g., 'en', 'es'). If not specified, auto-detected
        bot_name: Optional custom name for the bot in the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'teams', 'zoom'). Default is 'google_meet'.
        passcode: Passcode for Teams (and optionally Zoom). For Teams, extracted from `?p=...` in the meeting URL.
    
    Returns:
        JSON string with bot request details and status
    
    Note: After a successful request, it typically takes about 10 seconds for the bot to join the meeting.
    """
    url = f"{BASE_URL}/bots"
    payload = data.model_dump(exclude_none=True)
    meeting_url = payload.pop("meeting_url", None)
    if meeting_url:
        parsed = _parse_meeting_url(meeting_url)
        payload["platform"] = parsed.platform
        payload["native_meeting_id"] = parsed.native_meeting_id
        # Only set passcode from URL if caller didn't explicitly pass one.
        payload.setdefault("passcode", parsed.passcode)
    try:
        return await make_request("POST", url, api_key, payload)
    except HTTPException as e:
        # Common idempotency case: the meeting already exists for this key.
        if e.status_code == 409:
            meetings = await make_request("GET", f"{BASE_URL}/meetings", api_key)
            platform = payload.get("platform")
            native = payload.get("native_meeting_id")
            if isinstance(meetings, list):
                for m in meetings:
                    if isinstance(m, dict) and m.get("platform") == platform and m.get("native_meeting_id") == native:
                        return {"status": "already_exists", "meeting": m}
            return {"status": "already_exists", "detail": getattr(e, "detail", None)}
        raise


@app.get("/meeting-transcript/{meeting_platform}/{meeting_id}", operation_id="get_meeting_transcript")
async def get_meeting_transcript(
    meeting_id: str,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Get the real-time transcript for a meeting.
    
    Args:
        meeting_id: The unique identifier for the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with the meeting transcript data including segments with speaker, timestamp, and text
    
    Note: This provides real-time transcription data and can be called during or after the meeting.
    """
    url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}"
    return await make_request("GET", url, api_key)

@app.get("/recordings", operation_id="list_recordings")
async def list_recordings(
    limit: int = 50,
    offset: int = 0,
    meeting_db_id: Optional[int] = None,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    List recordings for the authenticated user.

    Wraps: GET /recordings?limit=&offset=&meeting_id=
    """
    url = f"{BASE_URL}/recordings"
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if meeting_db_id is not None:
        params["meeting_id"] = meeting_db_id
    return await make_request("GET", url, api_key, params=params)


@app.get("/recordings/{recording_id}", operation_id="get_recording")
async def get_recording(
    recording_id: int,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    Get a single recording and its media files.

    Wraps: GET /recordings/{recording_id}
    """
    url = f"{BASE_URL}/recordings/{recording_id}"
    return await make_request("GET", url, api_key)


@app.delete("/recordings/{recording_id}", operation_id="delete_recording")
async def delete_recording(
    recording_id: int,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    Delete a recording and its media files.

    Wraps: DELETE /recordings/{recording_id}
    """
    url = f"{BASE_URL}/recordings/{recording_id}"
    return await make_request("DELETE", url, api_key)


@app.get("/recordings/{recording_id}/media/{media_file_id}/download", operation_id="get_recording_media_download")
async def get_recording_media_download(
    recording_id: int,
    media_file_id: int,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    Get a download URL for a recording media file.

    Wraps: GET /recordings/{recording_id}/media/{media_file_id}/download
    """
    url = f"{BASE_URL}/recordings/{recording_id}/media/{media_file_id}/download"
    data = await make_request("GET", url, api_key)
    # Bot Manager returns a relative URL for local storage; make it absolute through the gateway.
    try:
        dl = data.get("download_url")
        if isinstance(dl, str) and dl.startswith("/"):
            data["download_url"] = f"{BASE_URL}{dl}"
    except Exception:
        pass
    return data


@app.get("/recording-config", operation_id="get_recording_config")
async def get_recording_config(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    """
    Get recording configuration for the authenticated user.

    Wraps: GET /recording-config
    """
    url = f"{BASE_URL}/recording-config"
    return await make_request("GET", url, api_key)


@app.put("/recording-config", operation_id="update_recording_config")
async def update_recording_config(
    data: RecordingConfigUpdate,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    Update recording configuration for the authenticated user.

    Wraps: PUT /recording-config
    """
    url = f"{BASE_URL}/recording-config"
    payload = {k: v for k, v in data.model_dump().items() if v is not None}
    return await make_request("PUT", url, api_key, payload=payload)


@app.post("/meeting-bundle", operation_id="get_meeting_bundle")
async def get_meeting_bundle(
    data: MeetingBundleRequest,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    Get a compact post-meeting bundle: meeting status + notes + recordings + (optional) segments + (optional) share link.

    Intended use cases:
    - Post-meeting: fetch notes + recording links, then create a share URL for the transcript.
    - Meeting prep: quickly confirm meeting identity and existing metadata.
    """
    transcript = await make_request(
        "GET",
        f"{BASE_URL}/transcripts/{data.meeting_platform}/{data.meeting_id}",
        api_key,
    )

    result: Dict[str, Any] = dict(transcript) if isinstance(transcript, dict) else {"transcript": transcript}

    if not data.include_segments and isinstance(result, dict):
        result.pop("segments", None)

    if not data.include_recordings and isinstance(result, dict):
        result.pop("recordings", None)

    if data.include_media_download_urls and isinstance(result, dict):
        recs = result.get("recordings")
        if isinstance(recs, list):
            for rec in recs:
                if not isinstance(rec, dict):
                    continue
                rid = rec.get("id")
                mfs = rec.get("media_files")
                if not rid or not isinstance(mfs, list):
                    continue
                for mf in mfs:
                    if not isinstance(mf, dict):
                        continue
                    mf_id = mf.get("id")
                    if not mf_id:
                        continue
                    mf["download"] = await get_recording_media_download(int(rid), int(mf_id), api_key)

    if data.include_share_link:
        share_args: Dict[str, Any] = {}
        if data.share_ttl_seconds is not None:
            share_args["ttl_seconds"] = data.share_ttl_seconds
        # Best-effort: if share link creation fails, return the rest of the bundle.
        try:
            share = await make_request(
                "POST",
                f"{BASE_URL}/transcripts/{data.meeting_platform}/{data.meeting_id}/share",
                api_key,
                params=share_args or None,
            )
            result["share_link"] = share
        except Exception as e:
            result["share_link_error"] = str(e)

    return result

@app.post("/transcript-share-link/{meeting_platform}/{meeting_id}", operation_id="create_transcript_share_link")
async def create_transcript_share_link(
    meeting_id: str,
    meeting_platform: str,
    meeting_db_id: Optional[int] = None,
    ttl_seconds: Optional[int] = None,
    api_key: str = Depends(get_api_key),
) -> Dict[str, Any]:
    """
    Create a short-lived public URL for a transcript (for ChatGPT 'Read from URL' and easy sharing).

    Wraps: POST /transcripts/{platform}/{native_meeting_id}/share
    """
    params: Dict[str, Any] = {}
    if meeting_db_id is not None:
        params["meeting_id"] = meeting_db_id
    if ttl_seconds is not None:
        params["ttl_seconds"] = ttl_seconds

    share_url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}/share"
    return await make_request("POST", share_url, api_key, payload=None, params=params or None)


@app.get("/bot-status", operation_id="get_bot_status")
async def get_bot_status(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    """
    Get the status of currently running bots.
    
    Returns:
        JSON with details about active bots under your API key
    """
    url = f"{BASE_URL}/bots/status"
    return await make_request("GET", url, api_key)


@app.put("/bot-config/{meeting_platform}/{meeting_id}", operation_id="update_bot_config")
async def update_bot_config(
    meeting_id: str,
    data: UpdateBotConfig,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Update the configuration of an active bot (e.g., changing the language).
    
    Args:
        meeting_id: The identifier of the meeting with the active bot
        language: New language code for transcription (e.g., 'en', 'es')
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON indicating whether the update request was accepted
    """
    url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}/config"
    return await make_request("PUT", url, api_key, data.model_dump())


@app.delete("/bot/{meeting_platform}/{meeting_id}", operation_id="stop_bot")
async def stop_bot(
    meeting_id: str,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Remove an active bot from a meeting.
    
    Args:
        meeting_id: The identifier of the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON confirming the bot removal
    """
    url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}"
    return await make_request("DELETE", url, api_key)


@app.get("/meetings", operation_id="list_meetings")
async def list_meetings(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    """
    List all meetings associated with your API key.
    
    Returns:
        JSON with a list of meeting records
    """
    url = f"{BASE_URL}/meetings"
    return await make_request("GET", url, api_key)


@app.patch("/meeting/{meeting_platform}/{meeting_id}", operation_id="update_meeting_data")
async def update_meeting_data(
    meeting_id: str,
    data: UpdateMeetingData,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Update meeting metadata such as name, participants, languages, and notes.
    
    Args:
        meeting_id: The unique identifier of the meeting
        name: Optional meeting name/title
        participants: Optional list of participant names
        languages: Optional list of language codes detected/used in the meeting
        notes: Optional meeting notes or description
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with the updated meeting record
    """
    url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    payload = {"data": {k: v for k, v in data.model_dump().items() if v is not None}}
    return await make_request("PATCH", url, api_key, payload)


@app.delete("/meeting/{meeting_platform}/{meeting_id}", operation_id="delete_meeting")
async def delete_meeting(
    meeting_id: str,
    meeting_platform: str = "google_meet",
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Purge transcripts and anonymize meeting data for finalized meetings.
    
    Only works for meetings in completed or failed states. Deletes all transcripts
    but preserves meeting and session records for telemetry.
    
    Args:
        meeting_id: The unique identifier of the meeting
        meeting_platform: The meeting platform (e.g., 'google_meet', 'zoom'). Default is 'google_meet'.
    
    Returns:
        JSON with confirmation message
    
    Raises:
        409 Conflict: If meeting is not in a finalized state.
    """
    url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    return await make_request("DELETE", url, api_key)


# ---------------------------
# MCP & Server
# ---------------------------
mcp = FastApiMCP(app, headers=["authorization", "x-api-key"])

# ---------------------------
# MCP Prompts (agent guidance)
# ---------------------------
_PROMPTS: Dict[str, mcp_types.Prompt] = {
    "vexa.meeting_prep": mcp_types.Prompt(
        name="vexa.meeting_prep",
        title="Vexa: Meeting Prep",
        description="Parse link, request bot, and attach meeting notes/metadata.",
        arguments=[
            mcp_types.PromptArgument(
                name="meeting_url",
                description="Full meeting URL (recommended for Teams/Zoom).",
                required=False,
            ),
            mcp_types.PromptArgument(
                name="meeting_platform",
                description="google_meet | teams | zoom (optional if meeting_url is provided).",
                required=False,
            ),
            mcp_types.PromptArgument(
                name="meeting_id",
                description="Native meeting ID (optional if meeting_url is provided).",
                required=False,
            ),
            mcp_types.PromptArgument(
                name="notes",
                description="Optional notes/agenda/context to store on the meeting.",
                required=False,
            ),
        ],
    ),
    "vexa.during_meeting": mcp_types.Prompt(
        name="vexa.during_meeting",
        title="Vexa: During Meeting",
        description="Check bot status and retrieve current transcript snapshot.",
        arguments=[
            mcp_types.PromptArgument(name="meeting_platform", description="google_meet | teams | zoom", required=True),
            mcp_types.PromptArgument(name="meeting_id", description="Native meeting ID", required=True),
        ],
    ),
    "vexa.post_meeting": mcp_types.Prompt(
        name="vexa.post_meeting",
        title="Vexa: Post Meeting",
        description="Fetch bundle (notes, recordings, share link) and produce follow-ups.",
        arguments=[
            mcp_types.PromptArgument(name="meeting_platform", description="google_meet | teams | zoom", required=True),
            mcp_types.PromptArgument(name="meeting_id", description="Native meeting ID", required=True),
        ],
    ),
    "vexa.teams_link_help": mcp_types.Prompt(
        name="vexa.teams_link_help",
        title="Vexa: Teams Link Help",
        description="Supported Teams links and passcode requirements (issues #105/#110).",
        arguments=[
            mcp_types.PromptArgument(name="meeting_url", description="Teams meeting URL from the user", required=False),
        ],
    ),
}


@mcp.server.list_prompts()
async def _list_prompts() -> mcp_types.ListPromptsResult:
    return mcp_types.ListPromptsResult(prompts=list(_PROMPTS.values()))


@mcp.server.get_prompt()
async def _get_prompt(name: str, arguments: Optional[Dict[str, str]] = None) -> mcp_types.GetPromptResult:
    args = arguments or {}

    def t(text: str) -> mcp_types.TextContent:
        return mcp_types.TextContent(type="text", text=text)

    if name == "vexa.meeting_prep":
        meeting_url = (args.get("meeting_url") or "").strip()
        meeting_platform = (args.get("meeting_platform") or "").strip()
        meeting_id = (args.get("meeting_id") or "").strip()
        notes = (args.get("notes") or "").strip()

        return mcp_types.GetPromptResult(
            description="Meeting prep flow using Vexa MCP tools.",
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=t(
                        "You are helping me prepare a meeting using Vexa.\n\n"
                        "Goals:\n"
                        "1. Identify meeting platform + native meeting id (+ passcode if needed).\n"
                        "2. Request the meeting bot (idempotent).\n"
                        "3. Store meeting notes/metadata so it appears in transcript responses.\n\n"
                        "Rules:\n"
                        "- Prefer calling `parse_meeting_link` when `meeting_url` is provided.\n"
                        "- For Teams: only `teams.live.com/meet/<id>?p=<passcode>` is supported; "
                        "`teams.microsoft.com/l/meetup-join/...` is not supported.\n"
                        "- When requesting a bot, pass `meeting_url` if you have it; otherwise use "
                        "`native_meeting_id` (+ `passcode` for Teams, from ?p=).\n"
                        "- After the meeting exists, call `update_meeting_data` with `notes` if provided.\n\n"
                        f"Input:\n- meeting_url: {meeting_url or '(none)'}\n"
                        f"- meeting_platform: {meeting_platform or '(none)'}\n"
                        f"- meeting_id: {meeting_id or '(none)'}\n"
                        f"- notes: {notes or '(none)'}\n\n"
                        "Now do the tool calls and tell me what you did and what to do next."
                    ),
                )
            ],
        )

    if name == "vexa.during_meeting":
        meeting_platform = (args.get("meeting_platform") or "").strip()
        meeting_id = (args.get("meeting_id") or "").strip()
        return mcp_types.GetPromptResult(
            description="During-meeting helper prompt using Vexa MCP tools.",
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=t(
                        "You are my during-meeting assistant using Vexa.\n\n"
                        f"Meeting: platform={meeting_platform}, id={meeting_id}\n\n"
                        "Steps:\n"
                        "- Call `get_bot_status` to confirm the bot is active / requested.\n"
                        "- Call `get_meeting_transcript` to fetch the current transcript snapshot.\n"
                        "- If the transcript is empty, explain whether the meeting may not have started, "
                        "bot may not be admitted yet, or transcription isn't producing segments.\n\n"
                        "Then summarize key points and action items so far."
                    ),
                )
            ],
        )

    if name == "vexa.post_meeting":
        meeting_platform = (args.get("meeting_platform") or "").strip()
        meeting_id = (args.get("meeting_id") or "").strip()
        return mcp_types.GetPromptResult(
            description="Post-meeting helper prompt using Vexa MCP tools.",
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=t(
                        "You are my post-meeting assistant using Vexa.\n\n"
                        f"Meeting: platform={meeting_platform}, id={meeting_id}\n\n"
                        "Steps:\n"
                        "- Call `get_meeting_bundle` (segments off) to fetch meeting status, notes, recordings, and share link.\n"
                        "- If recordings exist, resolve download URLs if needed.\n"
                        "- Produce:\n"
                        "  1) concise summary\n"
                        "  2) decisions\n"
                        "  3) action items with owners (if known) and due dates (if mentioned)\n"
                        "  4) open questions\n"
                    ),
                )
            ],
        )

    if name == "vexa.teams_link_help":
        meeting_url = (args.get("meeting_url") or "").strip()
        return mcp_types.GetPromptResult(
            description="Teams link troubleshooting prompt.",
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=t(
                        "Help me troubleshoot a Microsoft Teams meeting link for Vexa.\n\n"
                        f"User link: {meeting_url or '(none provided)'}\n\n"
                        "Checklist:\n"
                        "- If link is `teams.live.com/meet/<id>?p=<passcode>`:\n"
                        "  - native_meeting_id = <id> (10-15 digits)\n"
                        "  - passcode = value of ?p= (often required)\n"
                        "  - Prefer using `meeting_url` directly with `request_meeting_bot`.\n"
                        "- If link is `teams.microsoft.com/l/meetup-join/...`: explain it's not supported yet (issues #105/#110).\n"
                        "- If passcode fails validation, explain constraints (8-20 alphanumeric) and ask for a corrected link.\n\n"
                        "If a link is provided, call `parse_meeting_link` and show the extracted fields."
                    ),
                )
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")

mcp.mount_http()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18888)
