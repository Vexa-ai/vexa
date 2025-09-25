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
from datetime import datetime

# Import schemas for documentation
from shared_models.schemas import (
    MeetingCreate, MeetingResponse, MeetingListResponse, MeetingDataUpdate, # Updated/Added Schemas
    TranscriptionResponse, TranscriptionSegment,
    UserCreate, UserResponse, TokenResponse, UserDetailResponse, # Admin Schemas
    ErrorResponse,
    Platform, # Import Platform enum for path parameters
    BotStatusResponse # ADDED: Import response model for documentation
)

load_dotenv()

# Configuration - Service endpoints are now mandatory environment variables
ADMIN_API_URL = os.getenv("ADMIN_API_URL")
BOT_MANAGER_URL = os.getenv("BOT_MANAGER_URL")
TRANSCRIPTION_COLLECTOR_URL = os.getenv("TRANSCRIPTION_COLLECTOR_URL")

# --- Validation at startup ---
if not all([ADMIN_API_URL, BOT_MANAGER_URL, TRANSCRIPTION_COLLECTOR_URL]):
    missing_vars = [
        var_name
        for var_name, var_value in {
            "ADMIN_API_URL": ADMIN_API_URL,
            "BOT_MANAGER_URL": BOT_MANAGER_URL,
            "TRANSCRIPTION_COLLECTOR_URL": TRANSCRIPTION_COLLECTOR_URL,
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
            f"tc:meeting:{user_id}:{platform}:{native_id}:mutable",
            f"tc:meeting:{user_id}:{platform}:{native_id}:finalized",
            f"bm:meeting:{user_id}:{platform}:{native_id}:status",
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