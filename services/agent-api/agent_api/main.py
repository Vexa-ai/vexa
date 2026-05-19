"""Agent Runtime — generic AI agent runtime.

Containers are ephemeral — they can die at any time. State lives in Redis
(sessions) and S3 (workspaces). If a container dies mid-chat, the next
message recreates it seamlessly.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

import redis.asyncio as aioredis

from agent_api import config
from agent_api.auth import require_api_key
from agent_api.chat import (
    clear_session,
    delete_session_meta,
    get_session_meta,
    list_sessions,
    run_chat_turn,
    save_session_meta,
)
from agent_api.container_manager import ContainerManager
from agent_api import workspace

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agent_api")

_VEXA_ENV = os.getenv("VEXA_ENV", "development")
_PUBLIC_DOCS = _VEXA_ENV != "production"
app = FastAPI(
    title="Agent Runtime",
    version="0.1.0",
    docs_url="/docs" if _PUBLIC_DOCS else None,
    redoc_url="/redoc" if _PUBLIC_DOCS else None,
    openapi_url="/openapi.json" if _PUBLIC_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in config.CORS_ORIGINS],
    allow_methods=["*"],
    allow_headers=["*"],
)

cm = ContainerManager()


# ── Request / response models ──────────────────────────────────────────────


class ChatRequest(BaseModel):
    user_id: str
    message: str
    session_id: Optional[str] = None
    session_name: Optional[str] = None
    model: Optional[str] = None
    cli_flags: Optional[list] = None  # extra flags forwarded to agent CLI


class UserIdRequest(BaseModel):
    user_id: str


class SessionCreateRequest(BaseModel):
    user_id: str
    name: str = "New session"
    workspace: Optional[str] = None
    meeting_aware: bool = False


class SessionRenameRequest(BaseModel):
    user_id: str
    name: str



class FileWriteRequest(BaseModel):
    user_id: str
    path: str
    content: str


class ScheduleRequest(BaseModel):
    user_id: str
    action: str = "chat"  # "chat" or "http"
    message: Optional[str] = None
    cron: Optional[str] = None
    execute_at: Optional[str] = None
    url: Optional[str] = None
    method: Optional[str] = "POST"


# ── Lifecycle ──────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    # Redis — v0.10.5 Pack C.1 hardened client config (#267).
    app.state.redis = aioredis.from_url(
        config.REDIS_URL,
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )
    await app.state.redis.ping()
    logger.info("Redis connected")

    await cm.startup()

    # Migrate legacy S3 workspace paths (workspaces/{uid}/ → workspaces/{uid}/default/)
    try:
        await workspace.migrate_legacy_workspaces()
    except Exception as e:
        logger.warning(f"Legacy workspace migration failed (non-fatal): {e}")

    # Periodic workspace sync — S3-only safety net (no git commit).
    # Catches workspace changes even if the agent forgets to save or the
    # SSE stream is cancelled by client disconnect / scheduler timeout.
    # Agent's explicit `vexa workspace save` still does git commit + S3.
    async def _periodic_workspace_sync():
        interval = int(os.getenv("WORKSPACE_SYNC_INTERVAL", "60"))
        while True:
            await asyncio.sleep(interval)
            for _key, info in list(cm._containers.items()):
                if await cm._is_alive(info.name):
                    try:
                        await workspace.sync_up_s3_only(info.user_id, info.name, info.workspace_name)
                    except Exception as e:
                        logger.warning(f"Periodic sync failed for {info.user_id}:{info.session_id}: {e}")

    app.state._periodic_sync_task = asyncio.create_task(_periodic_workspace_sync())
    logger.info(f"Agent API ready on port {config.PORT}")


@app.on_event("shutdown")
async def shutdown():
    if hasattr(app.state, "_periodic_sync_task"):
        app.state._periodic_sync_task.cancel()
    await cm.shutdown()
    await app.state.redis.close()


# ── Health ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "containers": len(cm._containers)}


# ── Chat endpoints ─────────────────────────────────────────────────────────


def _format_meeting_context(context_json: str) -> str:
    """Format X-Meeting-Context JSON into a human-readable system prompt prefix."""
    try:
        ctx = json.loads(context_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    meetings = ctx.get("active_meetings", [])
    if not meetings:
        return ""
    parts = ["[MEETING CONTEXT] The user has active meetings right now:\n"]
    for m in meetings:
        mid = m.get("meeting_id", "unknown")
        platform = m.get("platform", "unknown")
        participants = m.get("participants", [])
        parts.append(f"Meeting {mid} ({platform}), participants: {', '.join(participants) or 'unknown'}")
        segments = m.get("latest_segments", [])
        if segments:
            parts.append("Latest transcript:")
            for s in segments[-30:]:  # cap display at 30 most recent
                speaker = s.get("speaker", "Unknown")
                text = s.get("text", "")
                parts.append(f"  {speaker}: {text}")
        parts.append("")
    parts.append("Use this meeting context to inform your responses. The user may ask about what's being discussed.")
    return "\n".join(parts)


def _chat_stream(req: ChatRequest, context_prefix: str = ""):
    """Shared SSE generator for /api/chat and /internal/chat.
    Retries once with a fresh container on failure."""

    async def generate():
        retries = 0
        max_retries = 1
        while retries <= max_retries:
            try:
                async for data in run_chat_turn(
                    app.state.redis, cm,
                    req.user_id, req.message, req.model,
                    req.session_id, req.session_name,
                    context_prefix=context_prefix,
                    cli_flags=req.cli_flags,
                ):
                    yield data
                break
            except Exception as e:
                retries += 1
                if retries <= max_retries:
                    logger.warning(f"Chat failed for {req.user_id}, retrying ({retries}/{max_retries}): {e}")
                    key = f"{req.user_id}:{req.session_id or 'default'}"
                    cm._containers.pop(key, None)
                    yield f"data: {json.dumps({'type': 'reconnecting'})}\n\n"
                else:
                    logger.error(f"Chat failed for {req.user_id} after {max_retries} retries: {e}", exc_info=True)
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

    # NOTE: Auto-save after SSE stream is unreliable in async generators.
    # Workspace persistence relies on:
    # 1. Agent calling `vexa workspace save` (CLAUDE.md instruction, DNS + auth fixed)
    # 2. The /internal/workspace/save endpoint (vexa CLI inside container)


@app.post("/api/chat", dependencies=[Depends(require_api_key)])
async def chat(req: ChatRequest, request: Request):
    """Send a message to the agent. Returns SSE stream."""
    meeting_context_header = request.headers.get("x-meeting-context", "")
    context_prefix = _format_meeting_context(meeting_context_header) if meeting_context_header else ""
    return _chat_stream(req, context_prefix=context_prefix)


@app.post("/internal/chat")
async def internal_chat(req: ChatRequest, request: Request):
    """Internal chat endpoint for scheduler — no user API key required.
    Protected by INTERNAL_API_SECRET if configured."""
    if config.INTERNAL_API_SECRET:
        provided = request.headers.get("x-internal-secret", "")
        if provided != config.INTERNAL_API_SECRET:
            raise HTTPException(403, "Invalid internal secret")
    return _chat_stream(req)


@app.delete("/api/chat", dependencies=[Depends(require_api_key)])
async def interrupt_chat(req: UserIdRequest):
    """Interrupt an in-progress chat turn."""
    await cm.interrupt(req.user_id)
    return {"status": "interrupted"}


@app.post("/api/chat/reset", dependencies=[Depends(require_api_key)])
async def reset_chat(req: UserIdRequest):
    """Reset the chat session (keeps workspace files)."""
    await cm.reset_session(req.user_id)
    await clear_session(app.state.redis, req.user_id)
    return {"status": "reset"}


# ── Session management ─────────────────────────────────────────────────────


@app.get("/api/sessions", dependencies=[Depends(require_api_key)])
async def get_sessions(user_id: str):
    """List all sessions for a user."""
    sessions = await list_sessions(app.state.redis, user_id)
    return {"sessions": sessions}


@app.post("/api/sessions", dependencies=[Depends(require_api_key)])
async def create_session(req: SessionCreateRequest):
    """Create a new named session."""
    session_id = str(uuid.uuid4())
    extra = {"meeting_aware": req.meeting_aware}
    if req.workspace:
        extra["workspace"] = req.workspace
    await save_session_meta(
        app.state.redis, req.user_id, session_id, req.name, extra=extra,
    )
    return {"session_id": session_id, "name": req.name, "workspace": req.workspace}


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_api_key)])
async def delete_session(session_id: str, user_id: str):
    """Delete a session from the index."""
    await delete_session_meta(app.state.redis, user_id, session_id)
    return {"status": "deleted"}


@app.put("/api/sessions/{session_id}", dependencies=[Depends(require_api_key)])
async def rename_session(session_id: str, req: SessionRenameRequest):
    """Rename a session."""
    await save_session_meta(app.state.redis, req.user_id, session_id, req.name)
    return {"status": "renamed", "name": req.name}


# ── Workspace template endpoints (S3, pre-container) ─────────────────────


@app.post("/api/workspaces", dependencies=[Depends(require_api_key)])
async def upload_workspace_endpoint(request: Request, user_id: str, name: str):
    """Upload a workspace from local as tar.gz."""
    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty body")
    result = await workspace.upload_workspace(user_id, name, body)
    return result


@app.get("/api/workspaces", dependencies=[Depends(require_api_key)])
async def list_workspaces_endpoint(user_id: str):
    """List user's named workspaces."""
    ws = await workspace.list_workspaces(user_id)
    return {"workspaces": ws}


@app.delete("/api/workspaces/{name}", dependencies=[Depends(require_api_key)])
async def delete_workspace_endpoint(name: str, user_id: str):
    """Delete a named workspace."""
    await workspace.delete_workspace(user_id, name)
    return {"status": "deleted"}


@app.get("/api/workspaces/{name}/files", dependencies=[Depends(require_api_key)])
async def list_workspace_template_files(name: str, user_id: str):
    """List files in a named workspace (from S3)."""
    files = await workspace.list_workspace_files_s3(user_id, name)
    return {"files": files}


@app.post("/api/workspaces/{name}/file", dependencies=[Depends(require_api_key)])
async def write_workspace_template_file(name: str, req: FileWriteRequest):
    """Write a single file to a named workspace in S3."""
    _validate_path(req.path)
    await workspace.write_workspace_file_s3(req.user_id, name, req.path, req.content)
    return {"path": req.path, "status": "written"}


# ── Schedule bridge ───────────────────────────────────────────────────────


@app.post("/api/schedule", dependencies=[Depends(require_api_key)])
async def schedule_bridge(req: ScheduleRequest):
    """Bridge: vexa CLI -> agent-api -> runtime-api scheduler.

    Translates agent-friendly schedule requests into scheduler job specs.
    The scheduler is generic — it fires HTTP callbacks. This endpoint
    builds the callback URL pointing to /internal/chat on agent-api.
    """
    import time as _time

    # Build the target request for the scheduler
    if req.action == "chat":
        if not req.message:
            raise HTTPException(400, "message required for action=chat")
        callback_headers = {"Content-Type": "application/json"}
        if config.INTERNAL_API_SECRET:
            callback_headers["X-Internal-Secret"] = config.INTERNAL_API_SECRET
        target_request = {
            "method": "POST",
            "url": f"{config.AGENT_API_INTERNAL_URL}/internal/chat",
            "body": {"user_id": req.user_id, "message": req.message},
            "headers": callback_headers,
        }
    elif req.action == "http":
        if not req.url:
            raise HTTPException(400, "url required for action=http")
        # SSRF protection: reject internal/private URLs
        from urllib.parse import urlparse
        parsed = urlparse(req.url)
        hostname = parsed.hostname or ""
        if hostname in ("localhost", "127.0.0.1", "0.0.0.0") or hostname.endswith(".internal"):
            raise HTTPException(400, "Cannot schedule requests to internal URLs")
        if any(hostname.startswith(prefix) for prefix in ("10.", "172.", "192.168.")):
            raise HTTPException(400, "Cannot schedule requests to private network URLs")
        if "." not in hostname:
            raise HTTPException(400, "Cannot schedule requests to internal service names")
        target_request = {
            "method": req.method or "POST",
            "url": req.url,
        }
    else:
        raise HTTPException(400, f"Unknown action: {req.action}")

    # Determine execute_at
    execute_at = req.execute_at or _time.time() + 60  # default: 1 minute from now

    # Build scheduler job spec
    job_spec = {
        "execute_at": execute_at,
        "request": target_request,
        "metadata": {"user_id": req.user_id, "action": req.action, "source": "agent-api"},
    }
    if req.cron:
        job_spec["metadata"]["cron"] = req.cron

    # Forward to runtime-api scheduler
    try:
        resp = await cm._http.post("/scheduler/jobs", json=job_spec)
        if resp.status_code not in (200, 201):
            raise HTTPException(502, f"Scheduler error: {resp.status_code} {resp.text[:200]}")
        return resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Scheduler unreachable: {e}")


# ── Workspace endpoints ────────────────────────────────────────────────────


@app.get("/api/workspace/files", dependencies=[Depends(require_api_key)])
async def list_workspace_files(user_id: str):
    """List files in the user's workspace."""
    container = cm.get_container_name(user_id)
    if not container:
        raise HTTPException(404, f"No container for user {user_id}")
    ws = config.WORKSPACE_PATH
    raw = await cm.exec_simple(container, [
        "sh", "-c",
        f"cd {ws} && find . -not -path './.git/*' -not -path './.git' "
        "-not -name '.gitkeep' -type f | sort",
    ])
    if not raw:
        return {"files": []}
    files = [f.lstrip("./") for f in raw.strip().split("\n") if f.strip()]
    return {"files": files}


@app.get("/api/workspace/file", dependencies=[Depends(require_api_key)])
async def get_workspace_file(user_id: str, path: str):
    """Get file content from workspace."""
    _validate_path(path)
    container = cm.get_container_name(user_id)
    if not container:
        raise HTTPException(404, f"No container for user {user_id}")
    content = await cm.exec_simple(container, ["cat", f"{config.WORKSPACE_PATH}/{path}"])
    return {"path": path, "content": content or ""}


@app.post("/api/workspace/file", dependencies=[Depends(require_api_key)])
async def put_workspace_file(req: FileWriteRequest):
    """Write a file to the workspace."""
    _validate_path(req.path)
    container = cm.get_container_name(req.user_id)
    if not container:
        raise HTTPException(404, f"No container for user {req.user_id}")

    import base64 as b64
    parent = os.path.dirname(req.path)
    ws = config.WORKSPACE_PATH
    if parent:
        await cm.exec_simple(container, ["mkdir", "-p", f"{ws}/{parent}"])
    encoded = b64.b64encode(req.content.encode()).decode()
    await cm.exec_with_stdin(
        container,
        ["sh", "-c", f"base64 -d > {ws}/{req.path}"],
        stdin_data=encoded.encode(),
    )
    return {"path": req.path, "status": "written"}


@app.post("/internal/workspace/save")
async def workspace_save(req: UserIdRequest):
    """Sync workspace from container to S3."""
    container = cm.get_container_name(req.user_id)
    if not container:
        raise HTTPException(404, f"No container for user {req.user_id}")
    ok = await workspace.sync_up(req.user_id, container)
    if not ok:
        raise HTTPException(500, "Workspace sync failed")
    return {"status": "saved"}


@app.post("/internal/webhooks/meeting-completed")
async def webhook_meeting_completed(request: Request):
    """Receive post-meeting webhook from meeting-api."""
    body = await request.json()
    event_type = body.get("event_type", "unknown")
    event_id = body.get("event_id", "?")
    meeting_id = body.get("data", {}).get("meeting", {}).get("id", "?")
    logger.info(f"[Webhook] Received {event_type} event_id={event_id} meeting={meeting_id}")
    return {"status": "received", "event_id": event_id}


@app.get("/internal/workspace/status")
async def workspace_status(user_id: str):
    """Check workspace and container status."""
    exists = await workspace.workspace_exists(user_id)
    container = cm.get_container_name(user_id)
    return {
        "user_id": user_id,
        "workspace_in_storage": exists,
        "container_running": container is not None,
    }


# ── Helpers ────────────────────────────────────────────────────────────────

_SAFE_PATH = re.compile(r"^[a-zA-Z0-9._/\-]+$")


def _validate_path(path: str) -> str:
    """Validate a workspace file path."""
    if not path or ".." in path or path.startswith("/") or not _SAFE_PATH.match(path):
        raise HTTPException(400, "Invalid path")
    return path
