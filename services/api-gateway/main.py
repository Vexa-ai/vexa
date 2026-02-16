import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, status, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader
import httpx
import os
from dotenv import load_dotenv
import json # For request body processing
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Set, Tuple
import asyncio
import redis.asyncio as aioredis
from datetime import datetime, timedelta, timezone
import secrets

# Import schemas for documentation
from shared_models.schemas import (
    MeetingCreate, MeetingResponse, MeetingListResponse, MeetingDataUpdate, # Updated/Added Schemas
    TranscriptionResponse, TranscriptionSegment,
    UserCreate, UserResponse, TokenResponse, UserDetailResponse, # Admin Schemas
    ErrorResponse,
    Platform, # Import Platform enum for path parameters
    BotStatusResponse, # ADDED: Import response model for documentation
    SpeakRequest, ChatSendRequest, ChatMessagesResponse, ScreenContentRequest, # Voice agent schemas
)

load_dotenv()

# Configuration - Service endpoints are now mandatory environment variables
ADMIN_API_URL = os.getenv("ADMIN_API_URL")
BOT_MANAGER_URL = os.getenv("BOT_MANAGER_URL")
TRANSCRIPTION_COLLECTOR_URL = os.getenv("TRANSCRIPTION_COLLECTOR_URL")
MCP_URL = os.getenv("MCP_URL")

# Public share-link settings (for "ChatGPT read from URL" flows)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # Optional override, e.g. https://api.vexa.ai
TRANSCRIPT_SHARE_TTL_SECONDS = int(os.getenv("TRANSCRIPT_SHARE_TTL_SECONDS", "900"))  # 15 min
TRANSCRIPT_SHARE_TTL_MAX_SECONDS = int(os.getenv("TRANSCRIPT_SHARE_TTL_MAX_SECONDS", "86400"))  # 24h max

# --- Validation at startup ---
if not all([ADMIN_API_URL, BOT_MANAGER_URL, TRANSCRIPTION_COLLECTOR_URL, MCP_URL]):
    missing_vars = [
        var_name
        for var_name, var_value in {
            "ADMIN_API_URL": ADMIN_API_URL,
            "BOT_MANAGER_URL": BOT_MANAGER_URL,
            "TRANSCRIPTION_COLLECTOR_URL": TRANSCRIPTION_COLLECTOR_URL,
            "MCP_URL": MCP_URL,
        }.items()
        if not var_value
    ]
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Response Models
# class BotResponseModel(BaseModel): ...
# class MeetingModel(BaseModel): ...
# class MeetingsResponseModel(BaseModel): ...
# class TranscriptSegmentModel(BaseModel): ...
# class TranscriptResponseModel(BaseModel): ...
# class UserModel(BaseModel): ...
# class TokenModel(BaseModel): ...

# Security Schemes for OpenAPI
api_key_scheme = APIKeyHeader(name="X-API-Key", description="API Key for client operations", auto_error=False)
admin_api_key_scheme = APIKeyHeader(name="X-Admin-API-Key", description="API Key for admin operations", auto_error=False)

app = FastAPI(
    title="Vexa API Gateway",
    description="""
    **Main entry point for the Vexa platform APIs.**
    
    Provides access to:
    - Bot Management (Starting/Stopping transcription bots)
    - Transcription Retrieval
    - User & Token Administration (Admin only)
    
    ## Authentication
    
    Two types of API keys are used:
    
    1.  **`X-API-Key`**: Required for all regular client operations (e.g., managing bots, getting transcripts). Obtain your key from an administrator.
    2.  **`X-Admin-API-Key`**: Required *only* for administrative endpoints (prefixed with `/admin`). This key is configured server-side.
    
    Include the appropriate header in your requests.
    """,
    version="1.2.0", # Incremented version
    contact={
        "name": "Vexa Support",
        "url": "https://vexa.io/support", # Placeholder URL
        "email": "support@vexa.io", # Placeholder Email
    },
    license_info={
        "name": "Proprietary",
    },
    # Include security schemes in OpenAPI spec
    # Note: Applying them globally or per-route is done below
)

# Custom OpenAPI Schema
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    # Generate basic schema first, without components
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        contact=app.contact,
        license_info=app.license_info,
    )
    
    # Manually add security schemes to the schema
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    
    # Add securitySchemes component
    openapi_schema["components"]["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API Key for client operations"
        },
        "AdminApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Admin-API-Key",
            "description": "API Key for admin operations"
        }
    }
    
    # Optional: Add global security requirement
    # openapi_schema["security"] = [{"ApiKeyAuth": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- HTTP Client --- 
# Use a single client instance for connection pooling
@app.on_event("startup")
async def startup_event():
    app.state.http_client = httpx.AsyncClient()
    # Initialize Redis for Pub/Sub used by WS
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    app.state.redis = await aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)

@app.on_event("shutdown")
async def shutdown_event():
    await app.state.http_client.aclose()
    try:
        await app.state.redis.close()
    except Exception:
        pass

# --- Helper for Forwarding --- 
async def forward_request(client: httpx.AsyncClient, method: str, url: str, request: Request) -> Response:
    # Copy original headers, converting to a standard dict
    # Exclude host, content-length, transfer-encoding as they are handled by httpx/server
    excluded_headers = {"host", "content-length", "transfer-encoding"}
    headers = {k.lower(): v for k, v in request.headers.items() if k.lower() not in excluded_headers}
    
    # Debug logging for original request headers
    print(f"DEBUG: Original request headers: {dict(request.headers)}")
    print(f"DEBUG: Original query params: {dict(request.query_params)}")
    
    # Determine target service based on URL path prefix
    is_admin_request = url.startswith(f"{ADMIN_API_URL}/admin")
    
    # Forward appropriate auth header if present
    if is_admin_request:
        admin_key = request.headers.get("x-admin-api-key")
        if admin_key:
            headers["x-admin-api-key"] = admin_key
            print(f"DEBUG: Forwarding x-admin-api-key header")
        else:
            print(f"DEBUG: No x-admin-api-key header found in request")
    else:
        # Forward client API key for bot-manager and transcription-collector
        client_key = request.headers.get("x-api-key")
        if client_key:
            headers["x-api-key"] = client_key
            print(f"DEBUG: Forwarding x-api-key header: {client_key[:5]}...")
        else:
            print(f"DEBUG: No x-api-key header found in request. Headers: {dict(request.headers)}")
    
    # Debug logging for forwarded headers
    print(f"DEBUG: Forwarded headers: {headers}")
    
    # Forward query parameters
    forwarded_params = dict(request.query_params)
    if forwarded_params:
        print(f"DEBUG: Forwarding query params: {forwarded_params}")
    
    content = await request.body()
    
    try:
        print(f"DEBUG: Forwarding {method} request to {url}")
        resp = await client.request(method, url, headers=headers, params=forwarded_params or None, content=content)
        print(f"DEBUG: Response from {url}: status={resp.status_code}")
        # Return downstream response directly (including headers, status code)
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
    except httpx.RequestError as exc:
        print(f"DEBUG: Request error: {exc}")
        raise HTTPException(status_code=503, detail=f"Service unavailable: {exc}")

# --- Root Endpoint --- 
@app.get("/", tags=["General"], summary="API Gateway Root")
async def root():
    """Provides a welcome message for the Vexa API Gateway."""
    return {"message": "Welcome to the Vexa API Gateway"}

# --- Bot Manager Routes --- 
@app.post("/bots",
         tags=["Bot Management"],
         summary="Request a new bot to join a meeting",
         description="Creates a new meeting record and launches a bot instance based on platform and native meeting ID.",
         # response_model=MeetingResponse, # Response comes from downstream, keep commented
         status_code=status.HTTP_201_CREATED,
         dependencies=[Depends(api_key_scheme)],
         # Explicitly define the request body schema for OpenAPI documentation
         openapi_extra={
             "requestBody": {
                 "content": {
                     "application/json": {
                         "schema": MeetingCreate.schema()
                     }
                 },
                 "required": True,
                 "description": "Specify the meeting platform, native ID, and optional bot name."
             },
         })
# Function signature remains generic for forwarding
async def request_bot_proxy(request: Request): 
    """Forward request to Bot Manager to start a bot."""
    url = f"{BOT_MANAGER_URL}/bots"
    # forward_request handles reading and passing the body from the original request
    return await forward_request(app.state.http_client, "POST", url, request)

@app.delete("/bots/{platform}/{native_meeting_id}",
           tags=["Bot Management"],
           summary="Stop a bot for a specific meeting",
           description="Stops the bot container associated with the specified platform and native meeting ID. Requires ownership via API key.",
           response_model=MeetingResponse,
           dependencies=[Depends(api_key_scheme)])
async def stop_bot_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward request to Bot Manager to stop a bot."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}"
    return await forward_request(app.state.http_client, "DELETE", url, request)

# --- ADD Route for PUT /bots/.../config ---
@app.put("/bots/{platform}/{native_meeting_id}/config",
          tags=["Bot Management"],
          summary="Update configuration for an active bot",
          description="Updates the language and/or task for an active bot. Sends command via Bot Manager.",
          status_code=status.HTTP_202_ACCEPTED,
          dependencies=[Depends(api_key_scheme)])
# Need to accept request body for PUT
async def update_bot_config_proxy(platform: Platform, native_meeting_id: str, request: Request): 
    """Forward request to Bot Manager to update bot config."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/config"
    # forward_request handles reading and passing the body from the original request
    return await forward_request(app.state.http_client, "PUT", url, request)
# -------------------------------------------

# --- ADD Route for GET /bots/status ---
@app.get("/bots/status",
         tags=["Bot Management"],
         summary="Get status of running bots for the user",
         description="Retrieves a list of currently running bot containers associated with the authenticated user.",
         response_model=BotStatusResponse, # Document expected response
         dependencies=[Depends(api_key_scheme)])
async def get_bots_status_proxy(request: Request):
    """Forward request to Bot Manager to get running bot status."""
    url = f"{BOT_MANAGER_URL}/bots/status"
    return await forward_request(app.state.http_client, "GET", url, request)
# --- END Route for GET /bots/status ---

# --- Voice Agent Interaction Routes (proxy to Bot Manager) ---

@app.post("/bots/{platform}/{native_meeting_id}/speak",
          tags=["Voice Agent"],
          summary="Make the bot speak in a meeting",
          description="Sends text for TTS or raw audio to be played into the meeting via the bot's microphone.",
          dependencies=[Depends(api_key_scheme)],
          openapi_extra={
              "requestBody": {
                  "content": {
                      "application/json": {
                          "schema": SpeakRequest.schema()
                      }
                  },
                  "required": True,
                  "description": "Text to speak (TTS) or audio URL/base64 to play."
              },
          })
async def speak_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward speak request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/speak"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.delete("/bots/{platform}/{native_meeting_id}/speak",
            tags=["Voice Agent"],
            summary="Interrupt bot speech",
            description="Stops any currently playing TTS or audio in the meeting.",
            dependencies=[Depends(api_key_scheme)])
async def speak_stop_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward speak stop request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/speak"
    return await forward_request(app.state.http_client, "DELETE", url, request)

@app.post("/bots/{platform}/{native_meeting_id}/chat",
          tags=["Voice Agent"],
          summary="Send a chat message in the meeting",
          description="Sends a text message into the meeting chat via the bot.",
          dependencies=[Depends(api_key_scheme)],
          openapi_extra={
              "requestBody": {
                  "content": {
                      "application/json": {
                          "schema": ChatSendRequest.schema()
                      }
                  },
                  "required": True,
                  "description": "Chat message text to send."
              },
          })
async def chat_send_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward chat send request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/chat"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.get("/bots/{platform}/{native_meeting_id}/chat",
         tags=["Voice Agent"],
         summary="Read chat messages from the meeting",
         description="Returns chat messages captured by the bot from the meeting chat.",
         response_model=ChatMessagesResponse,
         dependencies=[Depends(api_key_scheme)])
async def chat_read_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward chat read request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/chat"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.post("/bots/{platform}/{native_meeting_id}/screen",
          tags=["Voice Agent"],
          summary="Show content on screen share",
          description="Displays an image, video, or URL via the bot's screen share in the meeting.",
          dependencies=[Depends(api_key_scheme)],
          openapi_extra={
              "requestBody": {
                  "content": {
                      "application/json": {
                          "schema": ScreenContentRequest.schema()
                      }
                  },
                  "required": True,
                  "description": "Content to display (image, video, or URL)."
              },
          })
async def screen_show_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward screen content request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/screen"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.delete("/bots/{platform}/{native_meeting_id}/screen",
            tags=["Voice Agent"],
            summary="Stop screen sharing",
            description="Stops the bot's screen share and clears the displayed content.",
            dependencies=[Depends(api_key_scheme)])
async def screen_stop_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward screen stop request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/screen"
    return await forward_request(app.state.http_client, "DELETE", url, request)


@app.put("/bots/{platform}/{native_meeting_id}/avatar",
         tags=["Voice Agent"],
         summary="Set bot avatar image",
         description="Sets a custom avatar for the bot's camera feed. Shown when no screen content is active. Provide 'url' (image URL) or 'image_base64' (data URI). Use DELETE to revert to default.",
         dependencies=[Depends(api_key_scheme)])
async def avatar_set_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward avatar set request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/avatar"
    return await forward_request(app.state.http_client, "PUT", url, request)


@app.delete("/bots/{platform}/{native_meeting_id}/avatar",
            tags=["Voice Agent"],
            summary="Reset bot avatar to default",
            description="Resets the bot's avatar to the default Vexa logo.",
            dependencies=[Depends(api_key_scheme)])
async def avatar_reset_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward avatar reset request to Bot Manager."""
    url = f"{BOT_MANAGER_URL}/bots/{platform.value}/{native_meeting_id}/avatar"
    return await forward_request(app.state.http_client, "DELETE", url, request)

# --- END Voice Agent Interaction Routes ---

# --- Recording Routes (proxy to Bot Manager) ---

@app.get("/recordings",
         tags=["Recordings"],
         summary="List recordings for the authenticated user",
         description="Returns a paginated list of recordings. Optionally filter by meeting_id.",
         dependencies=[Depends(api_key_scheme)])
async def list_recordings_proxy(request: Request):
    """Forward request to Bot Manager to list recordings."""
    url = f"{BOT_MANAGER_URL}/recordings"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/recordings/{recording_id}",
         tags=["Recordings"],
         summary="Get recording details",
         description="Returns a single recording with its media files.",
         dependencies=[Depends(api_key_scheme)])
async def get_recording_proxy(recording_id: int, request: Request):
    """Forward request to Bot Manager to get recording details."""
    url = f"{BOT_MANAGER_URL}/recordings/{recording_id}"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/recordings/{recording_id}/media/{media_file_id}/download",
         tags=["Recordings"],
         summary="Get download URL for a media file",
         description="Generates a presigned URL to download the specified media file.",
         dependencies=[Depends(api_key_scheme)])
async def download_media_proxy(recording_id: int, media_file_id: int, request: Request):
    """Forward request to Bot Manager for presigned download URL."""
    url = f"{BOT_MANAGER_URL}/recordings/{recording_id}/media/{media_file_id}/download"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/recordings/{recording_id}/media/{media_file_id}/raw",
         tags=["Recordings"],
         summary="Download media bytes via API (local backend)",
         description="Streams media bytes through API. Primarily for local filesystem storage backend.",
         dependencies=[Depends(api_key_scheme)])
async def download_media_raw_proxy(recording_id: int, media_file_id: int, request: Request):
    """Forward request to Bot Manager for raw media streaming."""
    url = f"{BOT_MANAGER_URL}/recordings/{recording_id}/media/{media_file_id}/raw"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.delete("/recordings/{recording_id}",
            tags=["Recordings"],
            summary="Delete a recording",
            description="Deletes a recording, its media files from storage, and all database rows.",
            dependencies=[Depends(api_key_scheme)])
async def delete_recording_proxy(recording_id: int, request: Request):
    """Forward request to Bot Manager to delete a recording."""
    url = f"{BOT_MANAGER_URL}/recordings/{recording_id}"
    return await forward_request(app.state.http_client, "DELETE", url, request)

@app.get("/recording-config",
         tags=["Recordings"],
         summary="Get recording configuration",
         description="Returns the user's recording configuration.",
         dependencies=[Depends(api_key_scheme)])
async def get_recording_config_proxy(request: Request):
    """Forward request to Bot Manager to get recording config."""
    url = f"{BOT_MANAGER_URL}/recording-config"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.put("/recording-config",
         tags=["Recordings"],
         summary="Update recording configuration",
         description="Update the user's recording configuration (enable/disable, capture modes).",
         dependencies=[Depends(api_key_scheme)])
async def update_recording_config_proxy(request: Request):
    """Forward request to Bot Manager to update recording config."""
    url = f"{BOT_MANAGER_URL}/recording-config"
    return await forward_request(app.state.http_client, "PUT", url, request)

# --- Transcription Collector Routes ---
@app.get("/meetings",
        tags=["Transcriptions"],
        summary="Get list of user's meetings",
        description="Returns a list of all meetings initiated by the user associated with the API key.",
        response_model=MeetingListResponse, 
        dependencies=[Depends(api_key_scheme)])
async def get_meetings_proxy(request: Request):
    """Forward request to Transcription Collector to get meetings."""
    url = f"{TRANSCRIPTION_COLLECTOR_URL}/meetings"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/transcripts/{platform}/{native_meeting_id}",
        tags=["Transcriptions"],
        summary="Get transcript for a specific meeting",
        description="Retrieves the transcript segments for a meeting specified by its platform and native ID.",
        response_model=TranscriptionResponse,
        dependencies=[Depends(api_key_scheme)])
async def get_transcript_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward request to Transcription Collector to get a transcript."""
    url = f"{TRANSCRIPTION_COLLECTOR_URL}/transcripts/{platform.value}/{native_meeting_id}"
    return await forward_request(app.state.http_client, "GET", url, request)


# --- Public Transcript Share Links (no API integration needed by client) ---
class TranscriptShareResponse(BaseModel):
    share_id: str
    url: str
    expires_at: datetime
    expires_in_seconds: int


def _format_ts(seconds: float) -> str:
    """Format seconds into HH:MM:SS (or MM:SS) for readability."""
    try:
        s = int(seconds)
    except Exception:
        s = 0
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _best_base_url(request: Request) -> str:
    # Prefer explicit override for deployments where internal host differs from public host.
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")

    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


@app.post(
    "/transcripts/{platform}/{native_meeting_id}/share",
    tags=["Transcriptions"],
    summary="Create a short-lived public URL for a transcript (for ChatGPT 'Read from URL')",
    description="Mints a random, short-lived share URL that anyone can read (no auth). Intended for passing transcript content to ChatGPT via a link.",
    response_model=TranscriptShareResponse,
    dependencies=[Depends(api_key_scheme)],
)
async def create_transcript_share(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    meeting_id: Optional[int] = None,
    ttl_seconds: Optional[int] = None,
):
    # Clamp TTL
    ttl = ttl_seconds or TRANSCRIPT_SHARE_TTL_SECONDS
    if ttl < 60:
        ttl = 60
    if ttl > TRANSCRIPT_SHARE_TTL_MAX_SECONDS:
        ttl = TRANSCRIPT_SHARE_TTL_MAX_SECONDS

    # Fetch transcript from transcription-collector (auth required)
    api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")

    url = f"{TRANSCRIPTION_COLLECTOR_URL}/transcripts/{platform.value}/{native_meeting_id}"
    params: Dict[str, Any] = {}
    if meeting_id is not None:
        params["meeting_id"] = meeting_id

    try:
        resp = await app.state.http_client.get(url, headers={"X-API-Key": api_key}, params=params or None)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to reach transcription service: {e}")

    if resp.status_code != 200:
        # Proxy error through
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()
    segments = data.get("segments") or []

    # Build a plain-text payload
    lines: List[str] = []
    lines.append("MEETING TRANSCRIPT")
    lines.append("")
    lines.append(f"Platform: {data.get('platform')}")
    lines.append(f"Meeting ID: {data.get('native_meeting_id')}")
    if data.get("start_time"):
        lines.append(f"Start: {data.get('start_time')}")
    if data.get("end_time"):
        lines.append(f"End: {data.get('end_time')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for seg in segments:
        try:
            # Use absolute timestamp if available, otherwise fall back to relative
            abs_start = seg.get("absolute_start_time")
            if abs_start:
                # Format ISO datetime to readable format: "2025-12-25T12:47:21" -> "2025-12-25 12:47:21"
                try:
                    dt_obj = datetime.fromisoformat(abs_start.replace("Z", "+00:00"))
                    timestamp = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    timestamp = abs_start  # Fallback to raw value if parsing fails
            else:
                # Fallback to relative timestamp
                timestamp = _format_ts(float(seg.get("start_time") or seg.get("start") or 0))
            speaker = (seg.get("speaker") or "Unknown").strip() if isinstance(seg.get("speaker"), str) or seg.get("speaker") else "Unknown"
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            lines.append(f"[{timestamp}] {speaker}: {text}")
        except Exception:
            continue

    # Store share metadata in Redis (not the transcript itself - we'll fetch fresh on each request)
    share_id = secrets.token_urlsafe(16)
    redis_key = f"share:transcript:{share_id}"
    share_metadata = {
        "platform": platform.value,
        "native_meeting_id": native_meeting_id,
        "meeting_id": meeting_id,
        "api_key": api_key,  # Store API key to fetch fresh transcript
    }
    try:
        await app.state.redis.set(redis_key, json.dumps(share_metadata), ex=ttl)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to store share token: {e}")

    base = _best_base_url(request)
    public_url = f"{base}/public/transcripts/{share_id}.txt"
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    return TranscriptShareResponse(
        share_id=share_id,
        url=public_url,
        expires_at=expires_at,
        expires_in_seconds=ttl,
    )


@app.get(
    "/public/transcripts/{share_id}.txt",
    tags=["Transcriptions"],
    summary="Public transcript share (text)",
    description="Publicly accessible transcript content for a short-lived share ID. Fetches fresh transcript on each request. No auth. Intended for ChatGPT 'Read from URL'.",
)
async def get_public_transcript_share(share_id: str, request: Request):
    redis_key = f"share:transcript:{share_id}"
    try:
        metadata_json = await app.state.redis.get(redis_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to read share token: {e}")

    if not metadata_json:
        raise HTTPException(status_code=404, detail="Share link expired or not found")

    try:
        metadata = json.loads(metadata_json)
        platform = metadata.get("platform")
        native_meeting_id = metadata.get("native_meeting_id")
        meeting_id = metadata.get("meeting_id")
        api_key = metadata.get("api_key")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise HTTPException(status_code=500, detail=f"Invalid share metadata: {e}")

    # Fetch fresh transcript from transcription-collector
    url = f"{TRANSCRIPTION_COLLECTOR_URL}/transcripts/{platform}/{native_meeting_id}"
    params: Dict[str, Any] = {}
    if meeting_id is not None:
        params["meeting_id"] = meeting_id

    try:
        resp = await app.state.http_client.get(
            url, 
            headers={"X-API-Key": api_key}, 
            params=params or None,
            timeout=30.0  # 30 second timeout for transcript fetch
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Transcript fetch timeout")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to reach transcription service: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Failed to fetch transcript: {resp.text}")

    data = resp.json()
    segments = data.get("segments") or []

    # Build a plain-text payload (same format as when creating share)
    lines: List[str] = []
    lines.append("MEETING TRANSCRIPT")
    lines.append("")
    lines.append(f"Platform: {data.get('platform')}")
    lines.append(f"Meeting ID: {data.get('native_meeting_id')}")
    if data.get("start_time"):
        lines.append(f"Start: {data.get('start_time')}")
    if data.get("end_time"):
        lines.append(f"End: {data.get('end_time')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for seg in segments:
        try:
            # Use absolute timestamp if available, otherwise fall back to relative
            abs_start = seg.get("absolute_start_time")
            if abs_start:
                # Format ISO datetime to readable format: "2025-12-25T12:47:21" -> "2025-12-25 12:47:21"
                try:
                    dt_obj = datetime.fromisoformat(abs_start.replace("Z", "+00:00"))
                    timestamp = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    timestamp = abs_start  # Fallback to raw value if parsing fails
            else:
                # Fallback to relative timestamp
                timestamp = _format_ts(float(seg.get("start_time") or seg.get("start") or 0))
            speaker = (seg.get("speaker") or "Unknown").strip() if isinstance(seg.get("speaker"), str) or seg.get("speaker") else "Unknown"
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            lines.append(f"[{timestamp}] {speaker}: {text}")
        except Exception:
            continue

    transcript_text = "\n".join(lines).strip() + "\n"

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
    }
    return Response(content=transcript_text, status_code=200, headers=headers)

@app.patch("/meetings/{platform}/{native_meeting_id}",
           tags=["Transcriptions"],
           summary="Update meeting data",
           description="Updates meeting metadata. Only name, participants, languages, and notes can be updated.",
           response_model=MeetingResponse,
           dependencies=[Depends(api_key_scheme)],
           openapi_extra={
               "requestBody": {
                   "content": {
                       "application/json": {
                           "schema": {
                               "type": "object",
                               "properties": {
                                   "data": MeetingDataUpdate.schema()
                               },
                               "required": ["data"]
                           }
                       }
                   },
                   "required": True,
                   "description": "Meeting data to update (name, participants, languages, notes only)"
               },
           })
async def update_meeting_data_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward request to Transcription Collector to update meeting data."""
    url = f"{TRANSCRIPTION_COLLECTOR_URL}/meetings/{platform.value}/{native_meeting_id}"
    return await forward_request(app.state.http_client, "PATCH", url, request)

@app.delete("/meetings/{platform}/{native_meeting_id}",
            tags=["Transcriptions"],
            summary="Delete meeting transcripts and anonymize data",
            description="Purges transcripts and anonymizes meeting data for finalized meetings. Only works for completed or failed meetings. Preserves meeting records for telemetry.",
            dependencies=[Depends(api_key_scheme)])
async def delete_meeting_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward request to Transcription Collector to purge transcripts and anonymize meeting data."""
    url = f"{TRANSCRIPTION_COLLECTOR_URL}/meetings/{platform.value}/{native_meeting_id}"
    return await forward_request(app.state.http_client, "DELETE", url, request)

# --- User Profile Routes ---
@app.put("/user/webhook",
         tags=["User"],
         summary="Set user webhook URL",
         description="Sets a webhook URL for the authenticated user to receive notifications.",
         status_code=status.HTTP_200_OK,
         dependencies=[Depends(api_key_scheme)])
async def set_user_webhook_proxy(request: Request):
    """Forward request to Admin API to set user webhook."""
    url = f"{ADMIN_API_URL}/user/webhook"
    return await forward_request(app.state.http_client, "PUT", url, request)

# --- Admin API Routes --- 
@app.api_route("/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], 
               tags=["Administration"],
               summary="Forward admin requests",
               description="Forwards requests prefixed with `/admin` to the Admin API service. Requires `X-Admin-API-Key`.",
               dependencies=[Depends(admin_api_key_scheme)])
async def forward_admin_request(request: Request, path: str):
    """Generic forwarder for all admin endpoints."""
    admin_path = f"/admin/{path}" 
    url = f"{ADMIN_API_URL}{admin_path}"
    return await forward_request(app.state.http_client, request.method, url, request)

# --- MCP Routes ---
# Following FastAPI-MCP best practices:
# - Example 04: Separate server deployment (MCP service runs separately)
# - Example 08: Auth token passthrough via Authorization header
# The MCP service handles MCP protocol, gateway just forwards requests
@app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
               tags=["MCP"],
               summary="Forward MCP requests to MCP service",
               description="Forwards requests to the separate MCP service. MCP protocol endpoint for Model Context Protocol.")
async def forward_mcp_root(request: Request):
    """Forward MCP root endpoint requests to the separate MCP service."""
    url = f"{MCP_URL}/mcp"
    
    # Build headers following MCP transport protocol requirements
    # MCP expects Authorization header (per Example 08)
    headers = {}
    
    # Auth: Convert X-API-Key to Authorization if needed (MCP expects Authorization)
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header:
        headers["Authorization"] = auth_header
    else:
        x_api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if x_api_key:
            headers["Authorization"] = x_api_key
    
    # MCP transport protocol: GET requires text/event-stream, others use application/json
    if request.method == "GET":
        headers["Accept"] = "text/event-stream"
    else:
        headers["Accept"] = "application/json"
        if request.method in ["POST", "PUT", "PATCH"]:
            headers["Content-Type"] = "application/json"
    
    # Preserve other headers (excluding hop-by-hop headers)
    excluded = {"host", "content-length", "transfer-encoding", "accept", "authorization", "x-api-key"}
    for k, v in request.headers.items():
        if k.lower() not in excluded:
            headers[k] = v
    
    content = await request.body()
    
    try:
        resp = await app.state.http_client.request(
            request.method, url, headers=headers,
            params=dict(request.query_params) or None,
            content=content
        )
        # Some MCP server implementations return a 400 JSON-RPC error for the initial GET handshake
        # (while still providing a valid `mcp-session-id` header). Many clients treat non-2xx as fatal.
        status_code = resp.status_code
        if (
            request.method == "GET"
            and resp.status_code == 400
            and "mcp-session-id" in resp.headers
            and b"Missing session ID" in (resp.content or b"")
        ):
            status_code = 200
        return Response(content=resp.content, status_code=status_code, headers=dict(resp.headers))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"MCP service unavailable: {exc}")


@app.api_route("/mcp/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
               tags=["MCP"],
               summary="Forward MCP path requests",
               description="Forwards MCP requests with paths to the separate MCP service.")
async def forward_mcp_path(request: Request, path: str):
    """Forward MCP path requests to the separate MCP service."""
    url = f"{MCP_URL}/mcp/{path}"
    
    # Same header handling as root endpoint
    headers = {}
    
    # Auth: Convert X-API-Key to Authorization if needed
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header:
        headers["Authorization"] = auth_header
    else:
        x_api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if x_api_key:
            headers["Authorization"] = x_api_key
    
    # MCP transport protocol
    if request.method == "GET":
        headers["Accept"] = "text/event-stream"
    else:
        headers["Accept"] = "application/json"
        if request.method in ["POST", "PUT", "PATCH"]:
            headers["Content-Type"] = "application/json"
    
    # Preserve other headers
    excluded = {"host", "content-length", "transfer-encoding", "accept", "authorization", "x-api-key"}
    for k, v in request.headers.items():
        if k.lower() not in excluded:
            headers[k] = v
    
    content = await request.body()
    
    try:
        resp = await app.state.http_client.request(
            request.method, url, headers=headers,
            params=dict(request.query_params) or None,
            content=content
        )
        status_code = resp.status_code
        if (
            request.method == "GET"
            and resp.status_code == 400
            and "mcp-session-id" in resp.headers
            and b"Missing session ID" in (resp.content or b"")
        ):
            status_code = 200
        return Response(content=resp.content, status_code=status_code, headers=dict(resp.headers))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"MCP service unavailable: {exc}")

# --- Removed internal ID resolution and full transcript fetching from Gateway ---

# --- WebSocket Multiplex Endpoint ---
@app.websocket("/ws")
async def websocket_multiplex(ws: WebSocket):
    # Accept first to avoid HTTP 403 during handshake when rejecting
    await ws.accept()
    # Authenticate using header or query param AND validate token against DB
    api_key = ws.headers.get("x-api-key") or ws.query_params.get("api_key")
    if not api_key:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": "missing_api_key"}))
        finally:
            await ws.close(code=4401)  # Unauthorized
        return

    # Do not resolve API key to user here; leave authorization to downstream service

    redis = app.state.redis
    sub_tasks: Dict[Tuple[str, str], asyncio.Task] = {}
    subscribed_meetings: Set[Tuple[str, str]] = set()

    async def subscribe_meeting(platform: str, native_id: str, user_id: str, meeting_id: str):
        key = (platform, native_id, user_id)
        if key in subscribed_meetings:
            return
        subscribed_meetings.add(key)
        channels = [
            f"tc:meeting:{meeting_id}:mutable",  # Meeting-ID based channel
            f"bm:meeting:{meeting_id}:status",  # Meeting-ID based channel (consistent)
            f"va:meeting:{meeting_id}:chat",     # Chat messages from bot
        ]

        async def fan_in(channel_names: List[str]):
            pubsub = redis.pubsub()
            await pubsub.subscribe(*channel_names)
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = message.get("data")
                    try:
                        await ws.send_text(data)
                    except Exception:
                        break
            finally:
                try:
                    await pubsub.unsubscribe(*channel_names)
                    await pubsub.close()
                except Exception:
                    pass

        sub_tasks[key] = asyncio.create_task(fan_in(channels))

    async def unsubscribe_meeting(platform: str, native_id: str, user_id: str):
        key = (platform, native_id, user_id)
        task = sub_tasks.pop(key, None)
        if task:
            task.cancel()
        subscribed_meetings.discard(key)

    try:
        # Expect subscribe messages from client
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_text(json.dumps({"type": "error", "error": "invalid_json"}))
                continue

            action = msg.get("action")
            if action == "subscribe":
                meetings = msg.get("meetings", None)
                if not isinstance(meetings, list):
                    await ws.send_text(json.dumps({"type": "error", "error": "invalid_subscribe_payload", "details": "'meetings' must be a non-empty list"}))
                    continue
                if len(meetings) == 0:
                    await ws.send_text(json.dumps({"type": "error", "error": "invalid_subscribe_payload", "details": "'meetings' list cannot be empty"}))
                    continue

                # Call downstream authorization API in transcription-collector
                try:
                    # Convert incoming meetings (platform/native_id) to expected schema (platform/native_meeting_id)
                    payload_meetings = []
                    for m in meetings:
                        if isinstance(m, dict):
                            plat = str(m.get("platform", "")).strip()
                            nid = str(m.get("native_id", "")).strip()
                            if plat and nid:
                                payload_meetings.append({"platform": plat, "native_meeting_id": nid})
                    if not payload_meetings:
                        await ws.send_text(json.dumps({"type": "error", "error": "invalid_subscribe_payload", "details": "no valid meeting objects"}))
                        continue

                    url = f"{TRANSCRIPTION_COLLECTOR_URL}/ws/authorize-subscribe"
                    headers = {"X-API-Key": api_key}
                    resp = await app.state.http_client.post(url, headers=headers, json={"meetings": payload_meetings})
                    if resp.status_code != 200:
                        await ws.send_text(json.dumps({"type": "error", "error": "authorization_service_error", "status": resp.status_code, "detail": resp.text}))
                        continue
                    data = resp.json()
                    authorized = data.get("authorized") or []
                    errors = data.get("errors") or []
                    if errors:
                        await ws.send_text(json.dumps({"type": "error", "error": "invalid_subscribe_payload", "details": errors}))
                        # Continue to subscribe to any meetings that were authorized
                    subscribed: List[Dict[str, str]] = []
                    for item in authorized:
                        plat = item.get("platform"); nid = item.get("native_id")
                        user_id = item.get("user_id"); meeting_id = item.get("meeting_id")
                        if plat and nid and user_id and meeting_id:
                            await subscribe_meeting(plat, nid, user_id, meeting_id)
                            subscribed.append({"platform": plat, "native_id": nid})
                    await ws.send_text(json.dumps({"type": "subscribed", "meetings": subscribed}))
                except Exception as e:
                    await ws.send_text(json.dumps({"type": "error", "error": "authorization_call_failed", "details": str(e)}))
                    continue
            elif action == "unsubscribe":
                meetings = msg.get("meetings", None)
                if not isinstance(meetings, list):
                    await ws.send_text(json.dumps({"type": "error", "error": "invalid_unsubscribe_payload", "details": "'meetings' must be a list"}))
                    continue
                unsubscribed: List[Dict[str, str]] = []
                errors: List[str] = []

                for idx, m in enumerate(meetings):
                    if not isinstance(m, dict):
                        errors.append(f"meetings[{idx}] must be an object")
                        continue
                    plat = str(m.get("platform", "")).strip()
                    nid = str(m.get("native_id", "")).strip()
                    if not plat or not nid:
                        errors.append(f"meetings[{idx}] missing 'platform' or 'native_id'")
                        continue
                    
                    # Find the subscription key that matches platform and native_id
                    # Since we now use (platform, native_id, user_id) as key, we need to find it
                    matching_key = None
                    for key in subscribed_meetings:
                        if key[0] == plat and key[1] == nid:
                            matching_key = key
                            break
                    
                    if matching_key:
                        await unsubscribe_meeting(plat, nid, matching_key[2])
                        unsubscribed.append({"platform": plat, "native_id": nid})
                    else:
                        errors.append(f"meetings[{idx}] not currently subscribed")

                if errors and not unsubscribed:
                    await ws.send_text(json.dumps({"type": "error", "error": "invalid_unsubscribe_payload", "details": errors}))
                    continue

                await ws.send_text(json.dumps({
                    "type": "unsubscribed",
                    "meetings": unsubscribed
                }))
                
            elif action == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                await ws.send_text(json.dumps({"type": "error", "error": "unknown_action"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"type": "error", "error": str(e)}))
        except Exception:
            pass
    finally:
        for task in sub_tasks.values():
            task.cancel()

# --- Main Execution --- 
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True) 
