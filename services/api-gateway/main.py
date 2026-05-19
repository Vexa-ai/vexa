import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, status, Depends, WebSocket, WebSocketDisconnect, Path
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader
import httpx
import os
from dotenv import load_dotenv
import json # For request body processing and token cacheing
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Set, Tuple
import asyncio
import logging
import websockets
import redis.asyncio as aioredis
from datetime import datetime, timedelta, timezone
import secrets
import time
import re
import hashlib

# Import schemas for documentation
from meeting_api.schemas import (
    MeetingCreate, MeetingResponse, MeetingListResponse, MeetingDataUpdate,
    TranscriptionResponse, TranscriptionSegment,
    UserCreate, UserResponse, TokenResponse, UserDetailResponse,
    ErrorResponse,
    Platform,
    BotStatusResponse,
    SpeakRequest, ChatSendRequest, ChatMessagesResponse, ScreenContentRequest,
)

load_dotenv()

# Configuration - Service endpoints are now mandatory environment variables
ADMIN_API_URL = os.getenv("ADMIN_API_URL")
MEETING_API_URL = os.getenv("MEETING_API_URL")
TRANSCRIPTION_COLLECTOR_URL = os.getenv("TRANSCRIPTION_COLLECTOR_URL")
MCP_URL = os.getenv("MCP_URL")
CALENDAR_SERVICE_URL = os.getenv("CALENDAR_SERVICE_URL")  # Optional — calendar-service
AGENT_API_URL = os.getenv("AGENT_API_URL")  # Optional — agent-api for chat
RUNTIME_API_URL = os.getenv("RUNTIME_API_URL", "http://runtime-api:8090")

# Public share-link settings (for "ChatGPT read from URL" flows)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # Optional override, e.g. https://api.vexa.ai
TRANSCRIPT_SHARE_TTL_SECONDS = int(os.getenv("TRANSCRIPT_SHARE_TTL_SECONDS", "900"))  # 15 min
TRANSCRIPT_SHARE_TTL_MAX_SECONDS = int(os.getenv("TRANSCRIPT_SHARE_TTL_MAX_SECONDS", "86400"))  # 24h max

# Rate limiting — requests per minute per API key (or per IP for unauthenticated).
# 0 = disabled. Separate limits for auth'd API calls vs admin/WebSocket.
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "120"))  # per-key default
RATE_LIMIT_ADMIN_RPM = int(os.getenv("RATE_LIMIT_ADMIN_RPM", "30"))  # admin endpoints
RATE_LIMIT_WS_RPM = int(os.getenv("RATE_LIMIT_WS_RPM", "20"))  # WebSocket upgrades

# Scope enforcement — map route prefixes to required scopes.
# Three scopes: "bot" (meeting bots), "tx" (transcripts), "browser" (browser sessions).
# Multi-scope tokens pass checks for all their domains.
ROUTE_SCOPES = {
    "/user/": {"bot"},
    "/bots": {"bot", "browser"},
    "/b/": {"browser"},
    "/transcripts": {"tx"},
    "/meetings": {"tx"},
}

# --- Validation at startup ---
if not all([ADMIN_API_URL, MEETING_API_URL, TRANSCRIPTION_COLLECTOR_URL, MCP_URL]):
    missing_vars = [
        var_name
        for var_name, var_value in {
            "ADMIN_API_URL": ADMIN_API_URL,
            "MEETING_API_URL": MEETING_API_URL,
            "TRANSCRIPTION_COLLECTOR_URL": TRANSCRIPTION_COLLECTOR_URL,
            "MCP_URL": MCP_URL,
        }.items()
        if not var_value
    ]
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Security Schemes for OpenAPI
api_key_scheme = APIKeyHeader(name="X-API-Key", description="API Key for client operations", auto_error=False)
admin_api_key_scheme = APIKeyHeader(name="X-Admin-API-Key", description="API Key for admin operations", auto_error=False)

_VEXA_ENV = os.getenv("VEXA_ENV", "development")
_PUBLIC_DOCS = _VEXA_ENV != "production"
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
    version="1.5.0", # Interactive bots, recordings, MCP, webhooks, transcript sharing, voice agent
    contact={
        "name": "Vexa Support",
        "url": "https://vexa.ai",
        "email": "support@vexa.ai",
    },
    license_info={
        "name": "Apache-2.0",
    },
    docs_url="/docs" if _PUBLIC_DOCS else None,
    redoc_url="/redoc" if _PUBLIC_DOCS else None,
    openapi_url="/openapi.json" if _PUBLIC_DOCS else None,
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
_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
_cors_wildcard = _cors_raw == "*"
CORS_ORIGINS = ["*"] if _cors_wildcard else [
    origin.strip()
    for origin in _cors_raw.split(",")
    if origin.strip()
]
from meeting_api.security_headers import SecurityHeadersMiddleware

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Rate Limiting Middleware ---
RATE_LIMIT_SKIP_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

async def _check_rate_limit(redis_client, key: str, limit: int) -> Tuple[bool, int, int]:
    """Sliding-window rate limit check using Redis sorted sets.
    Returns (allowed, remaining, retry_after_seconds)."""
    if limit <= 0:
        return True, 0, 0

    now = time.time()
    window = 60  # 1-minute window
    window_start = now - window
    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zadd(key, {f"{now}": now})
    pipe.zcard(key)
    pipe.expire(key, window + 1)
    results = await pipe.execute()
    count = results[2]
    remaining = max(0, limit - count)
    if count > limit:
        # Find oldest request in window to calculate retry-after
        retry_after = int(window - (now - window_start)) + 1
        return False, 0, retry_after
    return True, remaining, 0

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path in RATE_LIMIT_SKIP_PATHS or RATE_LIMIT_RPM <= 0:
        return await call_next(request)

    redis_client = getattr(app.state, "redis", None)
    if not redis_client:
        return await call_next(request)

    # Determine rate limit bucket and limit
    api_key = request.headers.get("x-api-key", "")
    admin_key = request.headers.get("x-admin-api-key", "")
    is_ws = request.headers.get("upgrade", "").lower() == "websocket"

    if is_ws:
        identifier = _token_hash(api_key) if api_key else request.client.host
        bucket = f"ratelimit:ws:{identifier}"
        limit = RATE_LIMIT_WS_RPM
    elif path.startswith("/admin"):
        identifier = _token_hash(admin_key) if admin_key else request.client.host
        bucket = f"ratelimit:admin:{identifier}"
        limit = RATE_LIMIT_ADMIN_RPM
    else:
        identifier = _token_hash(api_key) if api_key else request.client.host
        bucket = f"ratelimit:api:{identifier}"
        limit = RATE_LIMIT_RPM

    try:
        allowed, remaining, retry_after = await _check_rate_limit(redis_client, bucket, limit)
    except Exception:
        # Redis failure — don't block requests
        return await call_next(request)

    if not allowed:
        return Response(
            content=json.dumps({"detail": "Rate limit exceeded", "retry_after": retry_after}),
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Limit"] = str(limit)
    return response


# --- HTTP Client ---
# Use a single client instance for connection pooling
@app.on_event("startup")
async def startup_event():
    app.state.http_client = httpx.AsyncClient(timeout=30.0)
    # Initialize Redis for Pub/Sub used by WS
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    # v0.10.5 Pack C.1 — bounded socket config closes the silent-hang class (#267).
    app.state.redis = await aioredis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )

@app.on_event("shutdown")
async def shutdown_event():
    await app.state.http_client.aclose()
    try:
        await app.state.redis.close()
    except Exception:
        pass

logger = logging.getLogger("api_gateway")


# --- Helper for Forwarding ---
async def forward_request(client: httpx.AsyncClient, method: str, url: str, request: Request, *, require_auth: bool = True) -> Response:
    # Copy original headers, converting to a standard dict
    # Exclude host, content-length, transfer-encoding as they are handled by httpx/server
    excluded_headers = {"host", "content-length", "transfer-encoding"}
    headers = {k.lower(): v for k, v in request.headers.items() if k.lower() not in excluded_headers}

    # Security: strip any client-supplied identity headers (prevent spoofing)
    for h in ["x-user-id", "x-user-scopes", "x-user-limits",
              "x-user-webhook-url", "x-user-webhook-secret", "x-user-webhook-events"]:
        headers.pop(h, None)

    # Determine target service based on URL path prefix
    is_admin_request = url.startswith(f"{ADMIN_API_URL}/admin")

    # Forward appropriate auth header if present
    if is_admin_request:
        # Admin routes — admin-api validates X-Admin-API-Key itself
        admin_key = request.headers.get("x-admin-api-key")
        if admin_key:
            headers["x-admin-api-key"] = admin_key
    else:
        # Client API key auth — fail-closed: reject if missing or invalid
        client_key = request.headers.get("x-api-key")
        if require_auth and not client_key:
            return Response(
                content=json.dumps({"detail": "Missing API key"}),
                status_code=401,
                media_type="application/json",
            )

        if client_key:
            headers["x-api-key"] = client_key

            # Validate token via admin-api and inject identity headers
            user_data = await _resolve_token(client, client_key)
            if user_data:
                user_scopes = set(user_data.get("scopes", []))
                headers["x-user-id"] = str(user_data["user_id"])
                headers["x-user-scopes"] = ",".join(user_scopes)
                headers["x-user-limits"] = str(user_data.get("max_concurrent", 1))

                # Inject webhook config headers (meeting-api stores in meeting.data)
                wh_url = user_data.get("webhook_url")
                if wh_url:
                    headers["x-user-webhook-url"] = wh_url
                    wh_secret = user_data.get("webhook_secret")
                    if wh_secret:
                        headers["x-user-webhook-secret"] = wh_secret
                    wh_events = user_data.get("webhook_events")
                    if wh_events and isinstance(wh_events, dict):
                        enabled = [evt for evt, on in wh_events.items() if on]
                        if enabled:
                            headers["x-user-webhook-events"] = ",".join(enabled)

                # Scope enforcement: check if token has required scope for this route
                req_path = request.url.path
                for prefix, required in ROUTE_SCOPES.items():
                    if req_path.startswith(prefix):
                        if not user_scopes & required:
                            return Response(
                                content=json.dumps({"detail": "Insufficient scope for this endpoint"}),
                                status_code=403,
                                media_type="application/json",
                            )
                        break
            elif require_auth:
                return Response(
                    content=json.dumps({"detail": "Invalid API key"}),
                    status_code=401,
                    media_type="application/json",
                )

    # Forward query parameters
    forwarded_params = dict(request.query_params)

    content = await request.body()

    try:
        resp = await client.request(method, url, headers=headers, params=forwarded_params or None, content=content)
        # Return downstream response directly (including headers, status code)
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {exc}")


def _token_hash(api_key: str) -> str:
    """Short hash of full token for cache/rate-limit keys (avoids prefix collisions)."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


async def _resolve_token(client: httpx.AsyncClient, api_key: str) -> Optional[dict]:
    """Validate a token via admin-api, with Redis cache (60s TTL)."""
    cache_key = f"gateway:token:{_token_hash(api_key)}"
    redis_client: Optional[aioredis.Redis] = getattr(app.state, "redis", None)

    # Check cache first
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # Redis down — fall through to admin-api

    # Validate via admin-api
    try:
        validate_headers = {}
        internal_secret = os.getenv("INTERNAL_API_SECRET", "")
        if internal_secret:
            validate_headers["X-Internal-Secret"] = internal_secret
        validate_resp = await client.post(
            f"{ADMIN_API_URL}/internal/validate",
            json={"token": api_key},
            headers=validate_headers,
            timeout=5.0,
        )
        if validate_resp.status_code == 200:
            user_data = validate_resp.json()
            # Cache the result
            if redis_client:
                try:
                    await redis_client.set(cache_key, json.dumps(user_data), ex=60)
                except Exception:
                    pass  # Redis write failure is non-fatal
            return user_data
    except Exception as e:
        logger.warning(f"Token validation failed: {e}")

    # Validation failed or admin-api unreachable — caller decides whether to reject
    return None

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
    url = f"{MEETING_API_URL}/bots"
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
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}"
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
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/config"
    # forward_request handles reading and passing the body from the original request
    return await forward_request(app.state.http_client, "PUT", url, request)
# -------------------------------------------

# --- GET /bots — meeting history (all statuses) from meeting-api DB ---
@app.get("/bots",
         tags=["Bot Management"],
         summary="List recent meetings/bots for the user",
         dependencies=[Depends(api_key_scheme)])
async def list_bots_proxy(request: Request):
    """Forward to meeting-api GET /bots — returns all meetings (active + completed)."""
    url = f"{MEETING_API_URL}/bots"
    return await forward_request(app.state.http_client, "GET", url, request)

# --- ADD Route for GET /bots/status ---
@app.get("/bots/status",
         tags=["Bot Management"],
         summary="Get status of running bots for the user",
         description="Retrieves a list of currently running bot containers associated with the authenticated user.",
         response_model=BotStatusResponse, # Document expected response
         dependencies=[Depends(api_key_scheme)])
async def get_bots_status_proxy(request: Request):
    """Forward request to Bot Manager to get running bot status."""
    url = f"{MEETING_API_URL}/bots/status"
    return await forward_request(app.state.http_client, "GET", url, request)
# --- END Route for GET /bots/status ---

@app.get("/bots/id/{meeting_id}",
         tags=["Bot Management"],
         summary="Get a single meeting/bot by database ID",
         dependencies=[Depends(api_key_scheme)])
async def get_bot_by_id_proxy(meeting_id: int, request: Request):
    """Forward to meeting-api GET /bots/{meeting_id}."""
    url = f"{MEETING_API_URL}/bots/id/{meeting_id}"
    return await forward_request(app.state.http_client, "GET", url, request)

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
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/speak"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.delete("/bots/{platform}/{native_meeting_id}/speak",
            tags=["Voice Agent"],
            summary="Interrupt bot speech",
            description="Stops any currently playing TTS or audio in the meeting.",
            dependencies=[Depends(api_key_scheme)])
async def speak_stop_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward speak stop request to Bot Manager."""
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/speak"
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
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/chat"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.get("/bots/{platform}/{native_meeting_id}/chat",
         tags=["Voice Agent"],
         summary="Read chat messages from the meeting",
         description="Returns chat messages captured by the bot from the meeting chat.",
         response_model=ChatMessagesResponse,
         dependencies=[Depends(api_key_scheme)])
async def chat_read_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward chat read request to Bot Manager."""
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/chat"
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
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/screen"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.delete("/bots/{platform}/{native_meeting_id}/screen",
            tags=["Voice Agent"],
            summary="Stop screen sharing",
            description="Stops the bot's screen share and clears the displayed content.",
            dependencies=[Depends(api_key_scheme)])
async def screen_stop_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward screen stop request to Bot Manager."""
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/screen"
    return await forward_request(app.state.http_client, "DELETE", url, request)


@app.put("/bots/{platform}/{native_meeting_id}/avatar",
         tags=["Voice Agent"],
         summary="Set bot avatar image",
         description="Sets a custom avatar for the bot's camera feed. Shown when no screen content is active. Provide 'url' (image URL) or 'image_base64' (data URI). Use DELETE to revert to default.",
         dependencies=[Depends(api_key_scheme)])
async def avatar_set_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward avatar set request to Bot Manager."""
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/avatar"
    return await forward_request(app.state.http_client, "PUT", url, request)


@app.delete("/bots/{platform}/{native_meeting_id}/avatar",
            tags=["Voice Agent"],
            summary="Reset bot avatar to default",
            description="Resets the bot's avatar to the default Vexa logo.",
            dependencies=[Depends(api_key_scheme)])
async def avatar_reset_proxy(platform: Platform, native_meeting_id: str, request: Request):
    """Forward avatar reset request to Bot Manager."""
    url = f"{MEETING_API_URL}/bots/{platform.value}/{native_meeting_id}/avatar"
    return await forward_request(app.state.http_client, "DELETE", url, request)

# --- END Voice Agent Interaction Routes ---

# --- Calendar Routes (proxy to Calendar Service) ---

@app.post("/calendar/connect",
          tags=["Calendar"],
          summary="Trigger initial calendar sync after OAuth",
          dependencies=[Depends(api_key_scheme)])
async def calendar_connect_proxy(request: Request):
    if not CALENDAR_SERVICE_URL:
        raise HTTPException(status_code=501, detail="Calendar service not configured")
    url = f"{CALENDAR_SERVICE_URL}/calendar/connect"
    return await forward_request(app.state.http_client, "POST", url, request)

@app.get("/calendar/status",
         tags=["Calendar"],
         summary="Check calendar connection status",
         dependencies=[Depends(api_key_scheme)])
async def calendar_status_proxy(request: Request):
    if not CALENDAR_SERVICE_URL:
        raise HTTPException(status_code=501, detail="Calendar service not configured")
    url = f"{CALENDAR_SERVICE_URL}/calendar/status"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.delete("/calendar/disconnect",
            tags=["Calendar"],
            summary="Disconnect calendar integration",
            dependencies=[Depends(api_key_scheme)])
async def calendar_disconnect_proxy(request: Request):
    if not CALENDAR_SERVICE_URL:
        raise HTTPException(status_code=501, detail="Calendar service not configured")
    url = f"{CALENDAR_SERVICE_URL}/calendar/disconnect"
    return await forward_request(app.state.http_client, "DELETE", url, request)

@app.get("/calendar/events",
         tags=["Calendar"],
         summary="List upcoming calendar events",
         dependencies=[Depends(api_key_scheme)])
async def calendar_events_proxy(request: Request):
    if not CALENDAR_SERVICE_URL:
        raise HTTPException(status_code=501, detail="Calendar service not configured")
    url = f"{CALENDAR_SERVICE_URL}/calendar/events"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.put("/calendar/preferences",
         tags=["Calendar"],
         summary="Update calendar auto-join preferences",
         dependencies=[Depends(api_key_scheme)])
async def calendar_preferences_proxy(request: Request):
    if not CALENDAR_SERVICE_URL:
        raise HTTPException(status_code=501, detail="Calendar service not configured")
    url = f"{CALENDAR_SERVICE_URL}/calendar/preferences"
    return await forward_request(app.state.http_client, "PUT", url, request)

# --- END Calendar Routes ---

# --- Recording Routes (proxy to Bot Manager) ---

@app.get("/recordings",
         tags=["Recordings"],
         summary="List recordings for the authenticated user",
         description="Returns a paginated list of recordings. Optionally filter by meeting_id.",
         dependencies=[Depends(api_key_scheme)])
async def list_recordings_proxy(request: Request):
    """Forward request to Bot Manager to list recordings."""
    url = f"{MEETING_API_URL}/recordings"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/recordings/{recording_id}",
         tags=["Recordings"],
         summary="Get recording details",
         description="Returns a single recording with its media files.",
         dependencies=[Depends(api_key_scheme)])
async def get_recording_proxy(recording_id: int, request: Request):
    """Forward request to Bot Manager to get recording details."""
    url = f"{MEETING_API_URL}/recordings/{recording_id}"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/recordings/{recording_id}/media/{media_file_id}/download",
         tags=["Recordings"],
         summary="Get download URL for a media file",
         description="Generates a presigned URL to download the specified media file.",
         dependencies=[Depends(api_key_scheme)])
async def download_media_proxy(recording_id: int, media_file_id: int, request: Request):
    """Forward request to Bot Manager for presigned download URL."""
    url = f"{MEETING_API_URL}/recordings/{recording_id}/media/{media_file_id}/download"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.get("/recordings/{recording_id}/media/{media_file_id}/raw",
         tags=["Recordings"],
         summary="Download media bytes via API (local backend)",
         description="Streams media bytes through API. Primarily for local filesystem storage backend.",
         dependencies=[Depends(api_key_scheme)])
async def download_media_raw_proxy(recording_id: int, media_file_id: int, request: Request):
    """Forward request to Bot Manager for raw media streaming."""
    url = f"{MEETING_API_URL}/recordings/{recording_id}/media/{media_file_id}/raw"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.delete("/recordings/{recording_id}",
            tags=["Recordings"],
            summary="Delete a recording",
            description="Deletes a recording, its media files from storage, and all database rows.",
            dependencies=[Depends(api_key_scheme)])
async def delete_recording_proxy(recording_id: int, request: Request):
    """Forward request to Bot Manager to delete a recording."""
    url = f"{MEETING_API_URL}/recordings/{recording_id}"
    return await forward_request(app.state.http_client, "DELETE", url, request)

@app.get("/recording-config",
         tags=["Recordings"],
         summary="Get recording configuration",
         description="Returns the user's recording configuration.",
         dependencies=[Depends(api_key_scheme)])
async def get_recording_config_proxy(request: Request):
    """Forward request to Bot Manager to get recording config."""
    url = f"{MEETING_API_URL}/recording-config"
    return await forward_request(app.state.http_client, "GET", url, request)

@app.put("/recording-config",
         tags=["Recordings"],
         summary="Update recording configuration",
         description="Update the user's recording configuration (enable/disable, capture modes).",
         dependencies=[Depends(api_key_scheme)])
async def update_recording_config_proxy(request: Request):
    """Forward request to Bot Manager to update recording config."""
    url = f"{MEETING_API_URL}/recording-config"
    return await forward_request(app.state.http_client, "PUT", url, request)

# --- Deferred Transcription Route ---

@app.post("/meetings/{meeting_id}/transcribe",
          tags=["Meetings"],
          summary="Transcribe a completed meeting recording",
          dependencies=[Depends(api_key_scheme)])
async def transcribe_meeting_proxy(meeting_id: int, request: Request):
    """Forward transcribe request to Bot Manager."""
    url = f"{MEETING_API_URL}/meetings/{meeting_id}/transcribe"
    return await forward_request(app.state.http_client, "POST", url, request)

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

@app.get("/meetings/{meeting_id}",
        tags=["Transcriptions"],
        summary="Get a single meeting by database ID",
        dependencies=[Depends(api_key_scheme)])
async def get_meeting_by_id_proxy(meeting_id: int, request: Request):
    """Forward to meeting-api GET /bots/id/{meeting_id}."""
    url = f"{MEETING_API_URL}/bots/id/{meeting_id}"
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

@app.put("/user/workspace-git",
         tags=["User"],
         summary="Set git workspace config",
         status_code=status.HTTP_200_OK,
         dependencies=[Depends(api_key_scheme)])
async def set_workspace_git_proxy(request: Request):
    url = f"{ADMIN_API_URL}/user/workspace-git"
    return await forward_request(app.state.http_client, "PUT", url, request)

@app.delete("/user/workspace-git",
         tags=["User"],
         summary="Remove git workspace config",
         status_code=status.HTTP_200_OK,
         dependencies=[Depends(api_key_scheme)])
async def delete_workspace_git_proxy(request: Request):
    url = f"{ADMIN_API_URL}/user/workspace-git"
    return await forward_request(app.state.http_client, "DELETE", url, request)

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

# --- Agent API Routes (chat, sessions) ---

async def _get_meeting_context(client: httpx.AsyncClient, user_id: str) -> Optional[str]:
    """Fetch active meeting context for a user. Returns JSON string or None.

    Uses two approaches:
    1. GET /bots/status for running containers (with platform/meeting_id if available)
    2. GET /meetings for all user meetings filtered to active status (fallback)
    """
    try:
        headers = {"x-user-id": str(user_id)}

        # Strategy 1: Check running bots
        active_meetings = []
        bot_platforms = {}  # meeting_id -> platform for active bots

        resp = await client.get(f"{MEETING_API_URL}/bots/status", headers=headers, timeout=5.0)
        has_running_bots = False
        if resp.status_code == 200:
            bots_data = resp.json()
            running_bots = bots_data.get("running_bots", [])
            has_running_bots = len(running_bots) > 0

            for bot in running_bots:
                platform = bot.get("platform")
                meeting_id = bot.get("native_meeting_id")
                if platform and meeting_id:
                    bot_platforms[meeting_id] = platform

        if not has_running_bots:
            return None

        # Strategy 2: Get meetings list to find active ones with correct platform/ID
        if not bot_platforms:
            # Bots are running but platform/meeting_id not in status — check meetings list
            m_resp = await client.get(
                f"{TRANSCRIPTION_COLLECTOR_URL}/meetings",
                headers=headers,
                timeout=5.0,
            )
            if m_resp.status_code == 200:
                m_data = m_resp.json()
                meetings_list = m_data.get("meetings", m_data) if isinstance(m_data, dict) else m_data
                if isinstance(meetings_list, list):
                    for m in meetings_list:
                        if m.get("status") in ("active", "requested", "joining"):
                            mid = m.get("native_meeting_id") or m.get("platform_specific_id")
                            plat = m.get("platform")
                            if mid and plat:
                                bot_platforms[mid] = plat

        if not bot_platforms:
            return None

        # Fetch transcript for each active meeting
        for meeting_id, platform in bot_platforms.items():
            segments = []
            try:
                t_resp = await client.get(
                    f"{TRANSCRIPTION_COLLECTOR_URL}/transcripts/{platform}/{meeting_id}",
                    headers=headers,
                    timeout=5.0,
                )
                if t_resp.status_code == 200:
                    t_data = t_resp.json()
                    all_segments = t_data.get("segments", [])
                    segments = all_segments[-50:]  # latest 50 segments max
            except Exception:
                pass

            participants = list(set(
                s.get("speaker", "") for s in segments if s.get("speaker")
            ))
            active_meetings.append({
                "meeting_id": meeting_id,
                "platform": platform,
                "status": "active",
                "participants": participants,
                "latest_segments": [
                    {
                        "speaker": s.get("speaker", "Unknown"),
                        "text": s.get("text", ""),
                        "timestamp": str(s.get("absolute_start_time") or s.get("start_time") or s.get("start", "")),
                    }
                    for s in segments
                ],
            })

        if not active_meetings:
            return None
        return json.dumps({"active_meetings": active_meetings})
    except Exception as e:
        logger.warning(f"Meeting context fetch failed for user {user_id}: {e}")
        return None


@app.post("/api/chat",
          tags=["Agent"],
          summary="Send a message to the AI agent (SSE stream)",
          description="Forwards chat to agent-api. If session is meeting_aware, injects active meeting context.",
          dependencies=[Depends(api_key_scheme)])
async def agent_chat_proxy(request: Request):
    """Forward chat to agent-api with meeting context injection."""
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")

    body = await request.body()
    extra_headers = {}

    # Parse body to check meeting_aware session
    try:
        data = json.loads(body)
        user_id = data.get("user_id", "")
        session_id = data.get("session_id")
        if user_id and session_id:
            redis_client: Optional[aioredis.Redis] = getattr(app.state, "redis", None)
            if redis_client:
                meta_raw = await redis_client.hget(f"agent:sessions:{user_id}", session_id)
                if meta_raw:
                    meta = json.loads(meta_raw)
                    if meta.get("meeting_aware"):
                        # Resolve internal user_id from API key
                        client_key = request.headers.get("x-api-key")
                        internal_uid = user_id
                        if client_key:
                            user_data = await _resolve_token(app.state.http_client, client_key)
                            if user_data:
                                internal_uid = str(user_data["user_id"])
                        context = await _get_meeting_context(app.state.http_client, internal_uid)
                        if context:
                            extra_headers["x-meeting-context"] = context
                            logger.info(f"Meeting context injected for user {user_id} ({len(context)} bytes)")
    except Exception as e:
        logger.warning(f"Meeting context injection error: {e}")

    # Build forwarding headers
    excluded = {"host", "content-length", "transfer-encoding"}
    headers = {k.lower(): v for k, v in request.headers.items() if k.lower() not in excluded}
    for h in ["x-user-id", "x-user-scopes", "x-user-limits",
              "x-user-webhook-url", "x-user-webhook-secret", "x-user-webhook-events"]:
        headers.pop(h, None)

    # Auth: inject identity headers
    client_key = headers.get("x-api-key")
    if client_key:
        user_data = await _resolve_token(app.state.http_client, client_key)
        if user_data:
            headers["x-user-id"] = str(user_data["user_id"])
            headers["x-user-scopes"] = ",".join(user_data.get("scopes", []))
            headers["x-user-limits"] = str(user_data.get("max_concurrent", 1))

    headers.update(extra_headers)
    params = dict(request.query_params)

    # Stream the SSE response from agent-api (long timeout for SSE)
    url = f"{AGENT_API_URL}/api/chat"
    try:
        sse_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0))
        req = sse_client.build_request(
            "POST", url, headers=headers, params=params or None, content=body,
        )
        resp = await sse_client.send(req, stream=True)

        async def stream_response():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "content-encoding"}
        }
        return StreamingResponse(stream_response(), status_code=resp.status_code, headers=resp_headers)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Agent API unavailable: {exc}")


@app.delete("/api/chat",
            tags=["Agent"],
            summary="Interrupt an in-progress chat",
            dependencies=[Depends(api_key_scheme)])
async def agent_chat_interrupt_proxy(request: Request):
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")
    url = f"{AGENT_API_URL}/api/chat"
    return await forward_request(app.state.http_client, "DELETE", url, request)


@app.post("/api/chat/reset",
          tags=["Agent"],
          summary="Reset the chat session",
          dependencies=[Depends(api_key_scheme)])
async def agent_chat_reset_proxy(request: Request):
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")
    url = f"{AGENT_API_URL}/api/chat/reset"
    return await forward_request(app.state.http_client, "POST", url, request)


@app.get("/api/sessions",
         tags=["Agent"],
         summary="List agent sessions for a user",
         dependencies=[Depends(api_key_scheme)])
async def agent_sessions_list_proxy(request: Request):
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")
    url = f"{AGENT_API_URL}/api/sessions"
    return await forward_request(app.state.http_client, "GET", url, request)


@app.post("/api/sessions",
          tags=["Agent"],
          summary="Create a new agent session",
          dependencies=[Depends(api_key_scheme)])
async def agent_session_create_proxy(request: Request):
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")
    url = f"{AGENT_API_URL}/api/sessions"
    return await forward_request(app.state.http_client, "POST", url, request)


@app.put("/api/sessions/{session_id}",
         tags=["Agent"],
         summary="Rename an agent session",
         dependencies=[Depends(api_key_scheme)])
async def agent_session_rename_proxy(session_id: str, request: Request):
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")
    url = f"{AGENT_API_URL}/api/sessions/{session_id}"
    return await forward_request(app.state.http_client, "PUT", url, request)


@app.delete("/api/sessions/{session_id}",
            tags=["Agent"],
            summary="Delete an agent session",
            dependencies=[Depends(api_key_scheme)])
async def agent_session_delete_proxy(session_id: str, request: Request):
    if not AGENT_API_URL:
        raise HTTPException(503, "Agent API not configured")
    url = f"{AGENT_API_URL}/api/sessions/{session_id}"
    return await forward_request(app.state.http_client, "DELETE", url, request)


# --- MCP Routes ---
# Following FastAPI-MCP best practices:
# - Example 04: Separate server deployment (MCP service runs separately)
# - Example 08: Auth token passthrough via Authorization header
# The MCP service handles MCP protocol, gateway just forwards requests
@app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
               tags=["MCP"],
               summary="Forward MCP requests to MCP service",
               description="Forwards requests to the separate MCP service. MCP protocol endpoint for Model Context Protocol.",
               dependencies=[Depends(api_key_scheme)])
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
               description="Forwards MCP requests with paths to the separate MCP service.",
               dependencies=[Depends(api_key_scheme)])
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

# --- Auth: /auth/me returns caller identity from API key ---
@app.get("/auth/me", tags=["Auth"])
async def auth_me(request: Request):
    """Return identity of the caller based on their API key."""
    api_key = request.headers.get("x-api-key")
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    user_data = await _resolve_token(app.state.http_client, api_key)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {
        "user_id": user_data["user_id"],
        "email": user_data.get("email", ""),
        "scopes": user_data.get("scopes", []),
        "max_concurrent": user_data.get("max_concurrent", 1),
    }


# --- Remote Browser Session Routes ---
# Token-based access: /b/{token} serves UI, /b/{token}/vnc/* proxies noVNC, /b/{token}/cdp proxies CDP
# No X-API-Key needed — the token IS the auth.

logger = logging.getLogger("api-gateway.browser")


_touch_timestamps: dict[str, float] = {}
_TOUCH_DEBOUNCE = 30  # seconds — don't /touch same container more often than this


async def _fire_touch(container_name: str) -> None:
    """Fire-and-forget POST /containers/{name}/touch to runtime-api.

    Debounced: skips if same container was touched within _TOUCH_DEBOUNCE seconds.
    Validates container_name to prevent SSRF via crafted Redis values.
    """
    if not re.match(r'^[a-zA-Z0-9_-]+$', container_name):
        logger.warning("Suspicious container_name in touch: %s", container_name[:20])
        return
    now = time.time()
    if now - _touch_timestamps.get(container_name, 0) < _TOUCH_DEBOUNCE:
        return
    _touch_timestamps[container_name] = now
    try:
        await app.state.http_client.post(
            f"{RUNTIME_API_URL}/containers/{container_name}/touch",
            timeout=5.0,
        )
    except Exception as exc:
        logger.debug("touch failed for %s: %s", container_name, exc)


async def resolve_browser_session(token: str) -> Optional[dict]:
    """Resolve session token to container info from Redis.

    Expected Redis value at ``browser_session:{token}`` is a JSON object with at
    least ``container_name`` and ``meeting_id`` keys.  Example::

        {
            "container_name": "vexa-bot-abc123",
            "meeting_id": "42",
            "user_id": "7"
        }
    """
    try:
        data = await app.state.redis.get(f"browser_session:{token}")
    except Exception as exc:
        logger.warning("Redis error resolving browser session %s...: %s", token[:8], exc)
        return None
    if not data:
        return None
    try:
        session = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
    # Fire-and-forget touch to keep container alive
    container_name = session.get("container_name")
    if container_name:
        asyncio.create_task(_fire_touch(container_name))

    # Resolve container IP if missing (K8s pods don't have DNS names)
    if not session.get("container_ip") and container_name:
        try:
            resp = await app.state.http_client.get(
                f"{RUNTIME_API_URL}/containers/{container_name}",
                timeout=5.0,
            )
            if resp.status_code == 200:
                info = resp.json()
                ip = info.get("ip")
                if ip:
                    session["container_ip"] = ip
                    # Cache in Redis for next request
                    try:
                        updated = json.dumps(session)
                        await app.state.redis.set(
                            f"browser_session:{token}", updated, ex=86400)
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("Failed to resolve container IP for %s: %s", container_name, exc)

    return session


def _browser_dashboard_html(token: str, session: dict) -> str:
    """Return the inline HTML for the remote browser dashboard."""
    meeting_id = session.get("meeting_id", "")
    vnc_iframe_url = f"/b/{token}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&path=b/{token}/vnc/websockify"
    return f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Remote Browser</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html, body {{ height: 100%; background: #1a1a2e; color: #eee; font-family: system-ui, -apple-system, sans-serif; }}
    .toolbar {{
      display: flex; align-items: center; gap: 10px;
      padding: 8px 16px; background: #0f3460; height: 48px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }}
    .toolbar h1 {{ font-size: 16px; font-weight: 600; margin-right: auto; white-space: nowrap; }}
    .btn {{
      border: none; padding: 7px 16px; border-radius: 4px; cursor: pointer;
      font-size: 13px; font-weight: 600; color: #fff; transition: background 0.15s;
    }}
    .btn:disabled {{ opacity: 0.5; cursor: wait; }}
    .btn-green {{ background: #27ae60; }}
    .btn-green:hover:not(:disabled) {{ background: #219a52; }}
    .btn-purple {{ background: #8e44ad; }}
    .btn-purple:hover:not(:disabled) {{ background: #7d3c98; }}
    .btn-blue {{ background: #2980b9; }}
    .btn-blue:hover:not(:disabled) {{ background: #2471a3; }}
    .vnc-frame {{
      width: 100%; border: none; display: block;
      height: calc(100vh - 48px);
    }}
    .toast {{
      position: fixed; top: 60px; right: 20px; background: #16213e;
      border: 1px solid #0f3460; padding: 12px 20px; border-radius: 6px;
      max-width: 400px; z-index: 999; font-size: 13px;
      transition: opacity 0.3s; white-space: pre-line;
    }}
    .toast.hidden {{ opacity: 0; pointer-events: none; }}
    #storage-panel {{
      display: none; position: fixed; top: 48px; right: 0; width: 480px;
      bottom: 0; background: #16213e; border-left: 1px solid #0f3460;
      z-index: 100; overflow-y: auto; font-size: 13px;
    }}
    #storage-panel.open {{ display: block; }}
    .panel-header {{
      padding: 12px 16px; background: #0f3460;
      display: flex; justify-content: space-between; align-items: center;
    }}
    .panel-header h2 {{ font-size: 15px; }}
    .panel-body {{ padding: 16px; color: #8899aa; }}
  </style>
</head>
<body>
  <div class="toolbar">
    <h1>Remote Browser</h1>
    <button class="btn btn-green" onclick="saveStorage()" id="save-btn">Save Storage</button>
    <button class="btn btn-purple" onclick="toggleAudit()">Storage Audit</button>
    <button class="btn btn-blue" onclick="window.open('/b/{token}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&path=b/{token}/vnc/websockify', '_blank')">Fullscreen</button>
  </div>
  <div class="toast hidden" id="toast"></div>

  <div id="storage-panel">
    <div class="panel-header">
      <h2>Storage Audit</h2>
      <button class="btn" style="background:#555;padding:5px 12px;font-size:12px" onclick="toggleAudit()">Close</button>
    </div>
    <div class="panel-body">
      <p>Storage audit coming soon.</p>
      <p style="margin-top:12px;font-size:12px;color:#556">
        This panel will show cookies, localStorage, and IndexedDB
        from the browser session for inspection and debugging.
      </p>
    </div>
  </div>

  <iframe class="vnc-frame" src="{vnc_iframe_url}" id="vnc-iframe"></iframe>

  <script>
    const TOKEN = "{token}";
    const MEETING_ID = "{meeting_id}";
    const toast = document.getElementById('toast');
    let toastTimer;

    function showToast(msg, ms) {{
      toast.textContent = msg;
      toast.classList.remove('hidden');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => toast.classList.add('hidden'), ms || 4000);
    }}

    async function saveStorage() {{
      const btn = document.getElementById('save-btn');
      btn.disabled = true;
      btn.textContent = 'Saving...';
      showToast('Saving browser storage to MinIO...');
      try {{
        const res = await fetch('/b/' + TOKEN + '/save', {{ method: 'POST' }});
        const data = await res.json();
        if (res.ok) {{
          showToast(data.message || 'Storage saved!', 5000);
        }} else {{
          showToast('Error: ' + (data.detail || res.statusText), 6000);
        }}
      }} catch (e) {{
        showToast('Error: ' + e.message, 6000);
      }} finally {{
        btn.disabled = false;
        btn.textContent = 'Save Storage';
      }}
    }}

    function toggleAudit() {{
      document.getElementById('storage-panel').classList.toggle('open');
    }}
  </script>
</body>
</html>"""


@app.get("/b/{token}", tags=["Remote Browser"], summary="Browser session dashboard",
         response_class=HTMLResponse)
async def browser_session_page(token: str):
    """Serve the remote browser dashboard UI. Token is the auth."""
    session = await resolve_browser_session(token)
    if not session:
        raise HTTPException(status_code=404, detail="Browser session not found or expired")
    return HTMLResponse(content=_browser_dashboard_html(token, session))


@app.api_route("/b/{token}/vnc/{path:path}", methods=["GET", "POST"],
               tags=["Remote Browser"], summary="Proxy noVNC static files",
               include_in_schema=False)
async def browser_vnc_proxy(token: str, path: str, request: Request):
    """Proxy HTTP requests (noVNC static files) to the container's port 6080."""
    session = await resolve_browser_session(token)
    if not session:
        raise HTTPException(status_code=404, detail="Browser session not found or expired")

    container = session.get("container_ip") or session["container_name"]
    # In single-container (lite) deployments, VNC runs on localhost, not a container hostname
    vnc_host = os.getenv("VNC_HOST") or container
    target_url = f"http://{vnc_host}:6080/{path}"

    # Forward query string
    qs = str(request.url.query)
    if qs:
        target_url += f"?{qs}"

    excluded_headers = {"host", "content-length", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded_headers}

    content = await request.body()

    try:
        resp = await app.state.http_client.request(
            request.method, target_url, headers=headers, content=content,
            timeout=30.0,
        )
        # Filter hop-by-hop response headers
        resp_headers = {k: v for k, v in resp.headers.items()
                        if k.lower() not in ("transfer-encoding", "connection", "keep-alive")}
        return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach browser container: {exc}")


@app.websocket("/b/{token}/vnc/websockify")
async def browser_vnc_ws(websocket: WebSocket, token: str):
    """Bidirectional WebSocket proxy for VNC (noVNC <-> websockify on container:6080)."""
    session = await resolve_browser_session(token)
    if not session:
        await websocket.close(code=4404)
        return

    container = session.get("container_ip") or session["container_name"]
    vnc_host = os.getenv("VNC_HOST") or container
    upstream_url = f"ws://{vnc_host}:6080/websockify"

    await websocket.accept(subprotocol="binary")

    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=["binary"],
            max_size=16 * 1024 * 1024,  # 16 MB max frame
            open_timeout=10,
        ) as upstream:

            async def client_to_upstream():
                try:
                    while True:
                        data = await websocket.receive()
                        if "bytes" in data and data["bytes"] is not None:
                            await upstream.send(data["bytes"])
                        elif "text" in data and data["text"] is not None:
                            await upstream.send(data["text"])
                except (WebSocketDisconnect, Exception):
                    pass

            async def upstream_to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            async def periodic_touch():
                try:
                    while True:
                        await asyncio.sleep(60)
                        await _fire_touch(container)
                except asyncio.CancelledError:
                    pass

            # Run both directions + periodic touch; when one proxy ends, cancel all
            done, pending = await asyncio.wait(
                [asyncio.create_task(client_to_upstream()),
                 asyncio.create_task(upstream_to_client()),
                 asyncio.create_task(periodic_touch())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.warning("VNC WebSocket proxy error for token %s: %s", token, exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def _proxy_cdp_http(token: str, path: str, request: Request) -> Response:
    """Shared CDP HTTP proxy body used by both /cdp and /cdp/{path} routes."""
    session = await resolve_browser_session(token)
    if not session:
        raise HTTPException(status_code=404, detail="Browser session not found")
    container = session.get("container_ip") or session["container_name"]
    try:
        qs = f"?{request.url.query}" if request.url.query else ""
        # Default to the /json/version handshake endpoint so that a bare
        # /b/{token}/cdp call works — Playwright's chromium.connectOverCDP
        # hits the base URL without a path, expects the CDP version JSON,
        # and reads webSocketDebuggerUrl from it.
        upstream_path = path or "json/version"
        resp = await app.state.http_client.get(
            f"http://{container}:9223/{upstream_path}{qs}", timeout=10.0,
            headers={"Host": "localhost"}  # CDP rejects non-localhost Host headers
        )
        # Rewrite webSocketDebuggerUrl to point through our CDP WebSocket
        # proxy. Preserve the inbound scheme — behind a TLS terminator the
        # gateway sees X-Forwarded-Proto=https and must emit wss://, not
        # ws://; downgrading breaks Playwright's connectOverCDP from any
        # secure origin.
        import re
        host = request.headers.get("host", "localhost:8056")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        incoming_scheme = (forwarded_proto.split(",")[0].strip().lower()
                           or request.url.scheme
                           or "http")
        ws_scheme = "wss" if incoming_scheme == "https" else "ws"
        proxy_ws_url = f"{ws_scheme}://{host}/b/{token}/cdp"
        content = re.sub(r'"webSocketDebuggerUrl":\s*"[^"]*"',
                        f'"webSocketDebuggerUrl": "{proxy_ws_url}"',
                        resp.text)
        return Response(content=content, status_code=resp.status_code,
                       headers={"content-type": resp.headers.get("content-type", "application/json")})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CDP HTTP proxy error: {exc}")


@app.api_route("/b/{token}/cdp/{path:path}", methods=["GET"],
               include_in_schema=False)
async def browser_cdp_http(token: str, path: str, request: Request):
    """HTTP proxy for CDP endpoints (e.g. /json/version) needed by Playwright connectOverCDP."""
    return await _proxy_cdp_http(token, path, request)


@app.api_route("/b/{token}/cdp", methods=["GET"], include_in_schema=False)
async def browser_cdp_http_bare(token: str, request: Request):
    """Bare /b/{token}/cdp alias — no trailing slash, no path.

    Without this, FastAPI's default redirect_slashes behavior 307-redirects
    GET /cdp → GET /cdp/ and strips the https scheme when the gateway sits
    behind a TLS terminator. Playwright's chromium.connectOverCDP(url)
    follows the redirect into plain http and then refuses the downgrade.
    Accepting the bare path as a first-class route removes the redirect.
    """
    return await _proxy_cdp_http(token, "json/version", request)


@app.websocket("/b/{token}/cdp-ws")
async def browser_cdp_ws_direct(websocket: WebSocket, token: str):
    """CDP WebSocket proxy (used by rewritten webSocketDebuggerUrl)."""
    await browser_cdp_ws(websocket, token)


@app.websocket("/b/{token}/cdp")
async def browser_cdp_ws(websocket: WebSocket, token: str):
    """Bidirectional WebSocket proxy for Chrome DevTools Protocol."""
    session = await resolve_browser_session(token)
    if not session:
        await websocket.close(code=4404)
        return

    container = session.get("container_ip") or session["container_name"]

    # Discover CDP WebSocket URL from the browser's /json/version endpoint
    try:
        resp = await app.state.http_client.get(
            f"http://{container}:9223/json/version", timeout=10.0,
            headers={"Host": "localhost"}
        )
        version_info = resp.json()
        cdp_ws_url = version_info.get("webSocketDebuggerUrl", "")
        # Replace localhost with container:9223 (socat proxy port)
        # Original may be ws://localhost/devtools/... or ws://localhost:9222/devtools/...
        import re
        cdp_ws_url = re.sub(r'ws://(localhost|127\.0\.0\.1)(:\d+)?/', f'ws://{container}:9223/', cdp_ws_url)
    except Exception as exc:
        logger.warning("Failed to discover CDP URL for %s: %s", container, exc)
        await websocket.close(code=4502)
        return

    if not cdp_ws_url:
        await websocket.close(code=4502)
        return

    logger.debug("CDP proxy: original URL=%s, rewritten URL=%s", version_info.get('webSocketDebuggerUrl', ''), cdp_ws_url)
    await websocket.accept()

    try:
        async with websockets.connect(
            cdp_ws_url,
            max_size=64 * 1024 * 1024,  # 64 MB — CDP can send large payloads (screenshots)
            open_timeout=10,
            additional_headers={"Host": "localhost"},  # CDP rejects non-localhost Host
        ) as upstream:

            async def client_to_upstream():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await upstream.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            async def upstream_to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception:
                    pass

            async def periodic_touch():
                try:
                    while True:
                        await asyncio.sleep(60)
                        await _fire_touch(container)
                except asyncio.CancelledError:
                    pass

            done, pending = await asyncio.wait(
                [asyncio.create_task(client_to_upstream()),
                 asyncio.create_task(upstream_to_client()),
                 asyncio.create_task(periodic_touch())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.warning("CDP WebSocket proxy error for token %s: %s", token, exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/b/{token}/save", tags=["Remote Browser"], summary="Save browser storage to MinIO")
async def browser_save_storage(token: str):
    """Convenience proxy: save browser userdata to MinIO via meeting-api."""
    session = await resolve_browser_session(token)
    if not session:
        raise HTTPException(status_code=404, detail="Browser session not found or expired")

    meeting_id = session.get("meeting_id")
    if not meeting_id:
        raise HTTPException(status_code=500, detail="Session missing meeting_id")

    # Forward to meeting-api (internal call, no user API key needed)
    try:
        resp = await app.state.http_client.post(
            f"{MEETING_API_URL}/internal/browser-sessions/{token}/save",
            timeout=60.0,  # sync can take a while
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach meeting-api: {exc}")


@app.delete("/b/{token}/storage", tags=["Remote Browser"], summary="Delete stored browser data from S3")
async def browser_delete_storage(token: str):
    """Delete stored browser userdata from S3 so user can start clean."""
    session = await resolve_browser_session(token)
    if not session:
        raise HTTPException(status_code=404, detail="Browser session not found or expired")

    user_id = session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=500, detail="Session missing user_id")

    try:
        resp = await app.state.http_client.delete(
            f"{MEETING_API_URL}/internal/browser-sessions/{user_id}/storage",
            timeout=60.0,
        )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach meeting-api: {exc}")


# --- End Remote Browser Session Routes ---


# --- v0.10.5 Pack X — synthetic-rig proxy routes ---
#
# Forward `/bots/internal/test/*` and `/bots/internal/callback/*` to
# meeting-api so the synthetic test rig (`tests3/synthetic/`) can drive
# OSS-side meeting lifecycle from the host without depending on the
# docker network for service-to-service hostnames. meeting-api itself
# enforces VEXA_ENV != production for the test endpoints — gateway
# just passes them through.
#
# /bots/internal/callback/* was already reachable because runtime-api
# delivers callbacks via the docker network (`http://localhost:8080`
# from runtime-api's POV — meeting-api hostname). The synthetic rig
# runs from the HOST, which has no docker-DNS access; the gateway
# proxy is the only way to reach meeting-api externally.
import os
_PACK_X_TEST_ROUTES_ENABLED = os.environ.get("VEXA_ENV", "development") != "production"


@app.api_route(
    "/bots/internal/test/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def synthetic_test_proxy(path: str, request: Request):
    """Forward Pack X synthetic-rig calls to meeting-api.

    require_auth=False because synthetic-rig callers don't carry user
    tokens. meeting-api endpoint enforces VEXA_ENV != production gate
    as the security boundary; gateway just passes through.
    """
    if not _PACK_X_TEST_ROUTES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    url = f"{MEETING_API_URL}/bots/internal/test/{path}"
    return await forward_request(app.state.http_client, request.method, url, request, require_auth=False)


@app.api_route(
    "/bots/internal/callback/{path:path}",
    methods=["POST"],
    include_in_schema=False,
)
async def synthetic_callback_proxy(path: str, request: Request):
    """Forward bot lifecycle callbacks to meeting-api — SYNTHETIC RIG ONLY.

    v0.10.5.3 audit (CRITICAL): this route was previously ungated; in
    production, anyone with a session_uid (UUIDv4) could POST here and
    drive arbitrary meeting state transitions via meeting-api's internal
    callback endpoint (force completion, inject failure_stage, trigger
    webhook deliveries). Audit finding bounced human → triage → develop.
    Production exposure was real because session_uids are not authentication.

    Real bot callbacks in production go runtime-api → meeting-api directly
    (in-cluster, docker-network-only, no auth needed because the network
    boundary IS the boundary). The api-gateway proxy here exists ONLY for
    the Pack X synthetic test rig that drives callbacks from outside the
    cluster. Same env gate as the synthetic_test_proxy route below — both
    return 404 in production.

    require_auth=False because synthetic-rig callers don't carry user
    tokens (the rig is its own boundary).
    """
    if not _PACK_X_TEST_ROUTES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    url = f"{MEETING_API_URL}/bots/internal/callback/{path}"
    return await forward_request(app.state.http_client, request.method, url, request, require_auth=False)


# --- End Pack X synthetic-rig proxy routes ---


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
                    # Resolve token to user_id so downstream auth works correctly
                    auth_headers: Dict[str, str] = {"X-API-Key": api_key}
                    user_data = await _resolve_token(app.state.http_client, api_key)
                    if user_data:
                        auth_headers["x-user-id"] = str(user_data["user_id"])
                        auth_headers["x-user-scopes"] = ",".join(user_data.get("scopes", []))
                        auth_headers["x-user-limits"] = str(user_data.get("max_concurrent_bots", 1))
                    resp = await app.state.http_client.post(url, headers=auth_headers, json={"meetings": payload_meetings})
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
