import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status, UploadFile, File, Form, Query, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import logging
import os
import base64
from typing import Optional, List, Dict, Any
import redis.asyncio as aioredis
import asyncio
import json
import httpx
import hmac
import uuid as uuid_lib

# Local imports - Remove unused ones
# from app.database.models import init_db # Using local init_db now
# from app.database.service import TranscriptionService # Not used here
# from app.tasks.monitoring import celery_app # Not used here

from .config import BOT_IMAGE_NAME, REDIS_URL
from app.orchestrators import (
    get_socket_session, close_docker_client, start_bot_container,
    stop_bot_container, _record_session_start, get_running_bots_status,
    verify_container_running,
)
# Note: get_running_bots_status and verify_container_running are abstracted
# and work for both Docker containers and process orchestrator (Lite setup)
from shared_models.database import init_db, get_db, async_session_local
from shared_models.models import User, Meeting, MeetingSession, Transcription, Recording, MediaFile
from shared_models.schemas import (
    MeetingCreate, MeetingResponse, Platform, BotStatusResponse, MeetingConfigUpdate,
    MeetingStatus, MeetingCompletionReason, MeetingFailureStage,
    is_valid_status_transition, get_status_source,
    RecordingResponse, RecordingListResponse, RecordingStatus, RecordingSource,
    MediaFileType, MediaFileResponse,
)
from shared_models.storage import create_storage_client
from app.auth import get_user_and_token # MODIFIED
from app.zoom_obf import (
    ZoomOBFError,
    get_zoom_oauth_client_credentials,
    get_zoom_refresh_token,
    mint_zoom_obf_token,
    refresh_zoom_access_token,
    resolve_zoom_access_token_from_user_data,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import attributes
from datetime import datetime # For start_time

# Delayed stop timeout for fallback container shutdown after stop command.
# Keep this above typical recording upload time to avoid interrupting uploads.
try:
    BOT_STOP_DELAY_SECONDS = max(0, int(os.getenv("BOT_STOP_DELAY_SECONDS", "90")))
except ValueError:
    BOT_STOP_DELAY_SECONDS = 90

# --- Status Transition Helper ---

async def update_meeting_status(
    meeting: Meeting, 
    new_status: MeetingStatus, 
    db: AsyncSession,
    completion_reason: Optional[MeetingCompletionReason] = None,
    failure_stage: Optional[MeetingFailureStage] = None,
    error_details: Optional[str] = None,
    transition_reason: Optional[str] = None,
    transition_metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Update meeting status with proper validation and data enrichment.
    
    Args:
        meeting: Meeting object to update
        new_status: New status to set
        db: Database session
        completion_reason: Reason for completion (if applicable)
        failure_stage: Stage where failure occurred (if applicable)
        error_details: Additional error details
        
    Returns:
        True if status was updated, False if transition was invalid
    """
    # Normalize invalid status values to valid enum (safety net for any legacy data)
    try:
        current_status = MeetingStatus(meeting.status)
    except ValueError:
        # Handle any invalid status values (e.g., legacy 'error' status)
        logger.warning(f"Invalid meeting status '{meeting.status}' for meeting {meeting.id}, normalizing to 'failed'")
        current_status = MeetingStatus.FAILED
        meeting.status = MeetingStatus.FAILED.value
        await db.commit()
    
    # Validate transition
    # #region agent log
    try:
        with open('/home/dima/dev/.cursor/debug.log', 'a') as f:
            import json
            f.write(json.dumps({"location": "bot-manager/main.py:79", "message": "Validating status transition", "data": {"meeting_id": meeting.id, "current_status": current_status.value, "new_status": new_status.value}, "timestamp": __import__('time').time(), "sessionId": "debug-session", "runId": "run1", "hypothesisId": "D"}) + "\n")
    except: pass
    # #endregion
    
    if not is_valid_status_transition(current_status, new_status):
        logger.warning(f"Invalid status transition from '{current_status.value}' to '{new_status.value}' for meeting {meeting.id}")
        logger.error(f"[DEBUG] Invalid transition: current='{current_status.value}', requested='{new_status.value}', meeting_id={meeting.id}")
        return False
    
    # Update status
    old_status = meeting.status
    meeting.status = new_status.value
    
    # Update data field with status-specific information (work on a fresh copy so JSONB change is detected)
    if not meeting.data:
        current_data: Dict[str, Any] = {}
    else:
        try:
            current_data = dict(meeting.data)
        except Exception:
            current_data = {}
    
    if new_status == MeetingStatus.COMPLETED:
        if completion_reason:
            current_data['completion_reason'] = completion_reason.value
        meeting.end_time = datetime.utcnow()
        
    elif new_status == MeetingStatus.FAILED:
        if failure_stage:
            current_data['failure_stage'] = failure_stage.value
        if error_details:
            current_data['error_details'] = error_details
        meeting.end_time = datetime.utcnow()
    
    # Add status transition metadata: single canonical list at data['status_transition']
    transition_entry = {
        'from': old_status,
        'to': new_status.value,
        'timestamp': datetime.utcnow().isoformat(),
        'source': get_status_source(current_status, new_status)
    }
    if transition_reason:
        transition_entry['reason'] = transition_reason
    if completion_reason:
        transition_entry['completion_reason'] = completion_reason.value
    if failure_stage:
        transition_entry['failure_stage'] = failure_stage.value
    if error_details:
        transition_entry['error_details'] = error_details
    if isinstance(transition_metadata, dict) and transition_metadata:
        try:
            # Merge without overwriting existing keys
            for k, v in transition_metadata.items():
                if k not in transition_entry:
                    transition_entry[k] = v
        except Exception:
            pass
    try:
        existing = current_data.get('status_transition')
        if isinstance(existing, dict):
            transitions_list = [existing]
        elif isinstance(existing, list):
            transitions_list = existing
        else:
            transitions_list = []
        transitions_list = list(transitions_list) + [transition_entry]
        current_data['status_transition'] = transitions_list
        # Remove deprecated key if present
        if 'status_transitions' in current_data:
            try:
                del current_data['status_transitions']
            except Exception:
                pass
    except Exception:
        current_data['status_transition'] = [transition_entry]

    # Assign back the rebuilt data object so SQLAlchemy marks JSONB as changed
    meeting.data = current_data
    try:
        await db.commit()
    except Exception as commit_error:
        await db.rollback()
        raise
    
    await db.refresh(meeting)
    
    logger.info(f"Meeting {meeting.id} status updated from '{old_status}' to '{new_status.value}'")
    return True

from app.tasks.bot_exit_tasks import run_all_tasks
from app.tasks.webhook_runner import run_status_webhook_task

def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 encoding without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def mint_meeting_token(meeting_id: int, user_id: int, platform: str, native_meeting_id: str, ttl_seconds: int = 3600) -> str:
    """Mint a MeetingToken (HS256 JWT) using ADMIN_TOKEN."""
    secret = os.environ.get("ADMIN_TOKEN")
    if not secret:
        raise ValueError("ADMIN_TOKEN not configured; cannot mint MeetingToken")
    
    now = int(datetime.utcnow().timestamp())
    
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "meeting_id": meeting_id,
        "user_id": user_id,
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "scope": "transcribe:write",
        "iss": "bot-manager",
        "aud": "transcription-collector",
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid_lib.uuid4())
    }
    
    header_b64 = _b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, digestmod='sha256').digest()
    signature_b64 = _b64url_encode(signature)
    
    return f"{header_b64}.{payload_b64}.{signature_b64}"

async def publish_meeting_status_change(meeting_id: int, new_status: str, redis_client: Optional[aioredis.Redis], platform: str, native_meeting_id: str, user_id: int):
    """Publish meeting status changes via Redis Pub/Sub on meeting-ID channel."""
    if not redis_client:
        logger.warning("Redis client not available for publishing meeting status change")
        return
    try:
        payload = {
            "type": "meeting.status",
            "meeting": {"id": meeting_id, "platform": platform, "native_id": native_meeting_id},
            "payload": {"status": new_status},
            "ts": datetime.utcnow().isoformat()
        }
        channel = f"bm:meeting:{meeting_id}:status"
        await redis_client.publish(channel, json.dumps(payload))
        logger.info(f"Published meeting status change to '{channel}': {new_status}")
    except Exception as e:
        logger.error(f"Failed to publish meeting status change for meeting {meeting_id}: {e}")

async def schedule_status_webhook_task(
    meeting: Meeting, 
    background_tasks: BackgroundTasks,
    old_status: str,
    new_status: str,
    reason: Optional[str] = None,
    transition_source: Optional[str] = None
):
    """Schedule a webhook task for meeting status changes."""
    status_change_info = {
        'old_status': old_status,
        'new_status': new_status,
        'reason': reason,
        'timestamp': datetime.utcnow().isoformat(),
        'transition_source': transition_source
    }
    
    # Schedule the webhook task with status change information
    background_tasks.add_task(
        run_status_webhook_task,
        meeting.id,
        status_change_info
    )
    logger.info(f"Scheduled status webhook task for meeting {meeting.id} status change: {old_status} -> {new_status}")


async def send_event_webhook(user_id: int, event_type: str, payload: dict):
    """
    Fire-and-forget webhook for recording/transcription events.
    Looks up user's webhook_url and POSTs the event payload.
    """
    from shared_models.webhook_url import validate_webhook_url

    try:
        async with async_session_local() as db:
            user = await db.get(User, user_id)
            if not user or not user.data or not isinstance(user.data, dict):
                return
            webhook_url = user.data.get('webhook_url')
            if not webhook_url:
                return
            try:
                validate_webhook_url(webhook_url)
            except ValueError:
                return

            headers = {'Content-Type': 'application/json'}
            secret = user.data.get('webhook_secret')
            if secret and isinstance(secret, str) and secret.strip():
                headers['Authorization'] = f'Bearer {secret.strip()}'

        async with httpx.AsyncClient(follow_redirects=True) as client:
            await client.post(
                webhook_url,
                json={'event_type': event_type, **payload},
                timeout=30.0,
                headers=headers,
            )
    except Exception as e:
        logging.getLogger("bot_manager").warning(f"Event webhook ({event_type}) failed for user {user_id}: {e}")


# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("bot_manager")

# Initialize the FastAPI app
app = FastAPI(title="Vexa Bot Manager")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ADD Redis Client Global ---
redis_client: Optional[aioredis.Redis] = None
# --------------------------------

# --- Storage Client (lazy init) ---
_storage_client = None

def get_storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = create_storage_client()
    return _storage_client


def get_recording_metadata_mode() -> str:
    return os.getenv("RECORDING_METADATA_MODE", "meeting_data").strip().lower()


def _new_recording_numeric_id() -> int:
    return int(uuid_lib.uuid4().int % 900000000000 + 100000000000)


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_meeting_recording(recording: Dict[str, Any], meeting_id: int) -> Dict[str, Any]:
    rec = dict(recording or {})
    rec["meeting_id"] = rec.get("meeting_id") or meeting_id
    rec["source"] = rec.get("source") or RecordingSource.BOT.value
    rec["status"] = rec.get("status") or RecordingStatus.COMPLETED.value
    rec["media_files"] = rec.get("media_files") or []
    return rec


async def _list_meeting_data_recordings(
    db: AsyncSession,
    user_id: int,
    meeting_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    stmt = select(Meeting).where(Meeting.user_id == user_id)
    if meeting_id is not None:
        stmt = stmt.where(Meeting.id == meeting_id)
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    recordings: List[Dict[str, Any]] = []
    for meeting in meetings:
        if not isinstance(meeting.data, dict):
            continue
        for rec in (meeting.data.get("recordings") or []):
            if isinstance(rec, dict):
                recordings.append(_normalize_meeting_recording(rec, meeting.id))
    recordings.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return recordings


async def _find_meeting_data_recording(
    db: AsyncSession,
    user_id: int,
    recording_id: int,
) -> tuple[Optional[Meeting], Optional[Dict[str, Any]]]:
    stmt = select(Meeting).where(Meeting.user_id == user_id)
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    for meeting in meetings:
        if not isinstance(meeting.data, dict):
            continue
        for rec in (meeting.data.get("recordings") or []):
            if isinstance(rec, dict) and int(rec.get("id", -1)) == recording_id:
                return meeting, _normalize_meeting_recording(rec, meeting.id)
    return None, None
# ----------------------------------

class BotExitCallbackPayload(BaseModel):
    connection_id: str = Field(..., description="The connectionId (session_uid) of the exiting bot.")
    exit_code: int = Field(..., description="The exit code of the bot process (0 for success, 1 for UI leave failure).")
    reason: Optional[str] = Field("self_initiated_leave", description="Reason for the exit.")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Detailed error information including stack trace, error message, and context.")
    platform_specific_error: Optional[str] = Field(None, description="Platform-specific error message or details.")
    completion_reason: Optional[MeetingCompletionReason] = Field(None, description="Reason for completion if applicable.")
    failure_stage: Optional[MeetingFailureStage] = Field(None, description="Stage where failure occurred if applicable.")

class BotStartupCallbackPayload(BaseModel):
    connection_id: str = Field(..., description="The connection ID of the bot session.")
    container_id: str = Field(..., description="The container ID of the started bot.")

class BotStatusChangePayload(BaseModel):
    """Unified payload for all bot status change callbacks."""
    connection_id: str = Field(..., description="The connection ID of the bot session.")
    container_id: Optional[str] = Field(None, description="The container ID of the bot.")
    status: MeetingStatus = Field(..., description="The new status of the meeting.")
    reason: Optional[str] = Field(None, description="Reason for the status change.")
    exit_code: Optional[int] = Field(None, description="Exit code if applicable.")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Detailed error information.")
    platform_specific_error: Optional[str] = Field(None, description="Platform-specific error message.")
    completion_reason: Optional[MeetingCompletionReason] = Field(None, description="Reason for completion if applicable.")
    failure_stage: Optional[MeetingFailureStage] = Field(None, description="Stage where failure occurred if applicable.")
    timestamp: Optional[str] = Field(None, description="Timestamp of the status change.")

# --- --------------------------------------------- ---

@app.on_event("startup")
async def startup_event():
    global redis_client # <-- Add global reference
    logger.info("Starting up Bot Manager...")
    # await init_db() # Removed - Admin API should handle this
    # await init_redis() # Removed redis init if not used elsewhere
    try:
        get_socket_session()
    except Exception as e:
        logger.error(f"Failed to initialize Docker client on startup: {e}", exc_info=True)

    # --- ADD Redis Client Initialization ---
    try:
        logger.info(f"Connecting to Redis at {REDIS_URL}...")
        redis_client = await aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        await redis_client.ping() # Verify connection
        logger.info("Successfully connected to Redis.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis on startup: {e}", exc_info=True)
        redis_client = None # Ensure client is None if connection fails
    # --------------------------------------

    logger.info("Database, Docker Client (attempted), and Redis Client (attempted) initialized.")
    
    # Start reconciliation scheduler
    logger.info("[Startup] Starting reconciliation scheduler...")
    asyncio.create_task(start_reconciliation_scheduler())
    logger.info("[Startup] Reconciliation scheduler started")

@app.on_event("shutdown")
async def shutdown_event():
    global redis_client # <-- Add global reference
    logger.info("Shutting down Bot Manager...")
    # await close_redis() # Removed redis close if not used

    # --- ADD Redis Client Closing ---
    if redis_client:
        logger.info("Closing Redis connection...")
        try:
            await redis_client.close()
            logger.info("Redis connection closed.")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}", exc_info=True)
    # ---------------------------------

    close_docker_client()
    logger.info("Docker Client closed.")

# --- ADDED: Delayed Stop Task ---
async def _delayed_container_stop(container_id: str, meeting_id: int, delay_seconds: int = BOT_STOP_DELAY_SECONDS):
    """
    Waits for a delay, then attempts to stop the container synchronously in a thread.
    After stopping, checks if meeting is still ACTIVE and finalizes it if needed.
    This ensures meetings are always finalized when stop_bot is called, even if callbacks are missed.
    """
    logger.info(f"[Delayed Stop] Task started for container {container_id} (meeting {meeting_id}). Waiting {delay_seconds}s before stopping.")
    await asyncio.sleep(delay_seconds)
    logger.info(f"[Delayed Stop] Delay finished for {container_id}. Attempting synchronous stop...")
    try:
        # Run the synchronous stop_bot_container in a separate thread
        # to avoid blocking the async event loop.
        await asyncio.to_thread(stop_bot_container, container_id)
        logger.info(f"[Delayed Stop] Successfully stopped container {container_id}.")
    except Exception as e:
        logger.error(f"[Delayed Stop] Error stopping container {container_id}: {e}", exc_info=True)
    
    # Safety finalizer: Check if meeting is still ACTIVE and finalize if needed
    # This ensures meetings are always finalized when stop_bot is called
    try:
        # Wait a short grace period for any pending callbacks to arrive
        grace_period = 1  # seconds
        logger.info(f"[Delayed Stop] Waiting {grace_period}s grace period for pending callbacks before finalizing meeting {meeting_id}...")
        await asyncio.sleep(grace_period)
        
        # Check meeting status in a new DB session
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.warning(f"[Delayed Stop] Meeting {meeting_id} not found in DB. Cannot finalize.")
                return
            
            # Only finalize if meeting is NOT in a terminal state (completed or failed)
            # This ensures we don't overwrite failed meetings with completed status
            terminal_states = [MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]
            if meeting.status not in terminal_states:
                logger.warning(f"[Delayed Stop] Meeting {meeting_id} still in non-terminal state '{meeting.status}' after container stop. Finalizing to COMPLETED (callback missed).")
                success = await update_meeting_status(
                    meeting,
                    MeetingStatus.COMPLETED,
                    db,
                    completion_reason=MeetingCompletionReason.STOPPED,
                    transition_reason="delayed_stop_finalizer",
                    transition_metadata={"container_id": container_id, "finalized_by": "delayed_stop"}
                )
                if success:
                    # Publish status change
                    global redis_client
                    if redis_client:
                        await publish_meeting_status_change(
                            meeting.id,
                            MeetingStatus.COMPLETED.value,
                            redis_client,
                            meeting.platform,
                            meeting.platform_specific_id,
                            meeting.user_id
                        )
                    
                    # Schedule post-meeting tasks
                    # Note: We can't use background_tasks here since we're in a background task
                    # So we'll run it in the background using asyncio.create_task
                    asyncio.create_task(run_all_tasks(meeting.id))
                    logger.info(f"[Delayed Stop] Meeting {meeting_id} finalized to COMPLETED and post-meeting tasks scheduled.")
                else:
                    logger.error(f"[Delayed Stop] Failed to finalize meeting {meeting_id} to COMPLETED.")
            else:
                logger.info(f"[Delayed Stop] Meeting {meeting_id} already in terminal state '{meeting.status}'. No finalization needed.")
    except Exception as e:
        logger.error(f"[Delayed Stop] Error during safety finalizer for meeting {meeting_id}: {e}", exc_info=True)
# --- ------------------------ ---

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Vexa Bot Manager is running"}

@app.post("/bots",
          response_model=MeetingResponse,
          status_code=status.HTTP_201_CREATED,
          summary="Request a new bot instance to join a meeting",
          dependencies=[Depends(get_user_and_token)]) # MODIFIED
async def request_bot(
    req: MeetingCreate,
    auth_data: tuple[str, User] = Depends(get_user_and_token), # MODIFIED
    db: AsyncSession = Depends(get_db)
):
    """Handles requests to launch a new bot container for a meeting.
    Requires a valid API token associated with a user.
    - Constructs the meeting URL from platform and native ID.
    - Creates a Meeting record in the database.
    - Starts a Docker container for the bot, passing user token, internal meeting ID, native meeting ID, and constructed URL.
    - Updates the Meeting record with container details and status.
    - Returns the created Meeting details.
    """
    user_token, current_user = auth_data

    logger.info(f"Received bot request for platform '{req.platform.value}' with native ID '{req.native_meeting_id}' from user {current_user.id}")
    native_meeting_id = req.native_meeting_id

    constructed_url = Platform.construct_meeting_url(req.platform.value, native_meeting_id, req.passcode)
    if not constructed_url:
        logger.error(f"Invalid meeting URL for platform {req.platform.value} and ID {native_meeting_id}. Rejecting request.")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid platform/native_meeting_id combination: cannot construct meeting URL"
        )

    existing_meeting_stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == req.platform.value,
        Meeting.platform_specific_id == native_meeting_id,
        Meeting.status.in_(['requested', 'joining', 'awaiting_admission', 'active']) # Block on all non-terminal states (excluding 'stopping' to allow immediate new bot after stop)
    ).order_by(desc(Meeting.created_at)).limit(1) # Get the latest one if multiple somehow exist
    
    result = await db.execute(existing_meeting_stmt)
    existing_meeting = result.scalars().first()
    if existing_meeting:
        logger.info(f"Found existing meeting record {existing_meeting.id} with status '{existing_meeting.status}' for user {current_user.id}, platform '{req.platform.value}', native ID '{native_meeting_id}'.")
        # Enforce DB-only uniqueness: if there's any non-terminal meeting (requested/joining/awaiting_admission/active), reject immediately.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active or requested meeting already exists for this platform and meeting ID. Platform: {req.platform.value}, Native Meeting ID: {native_meeting_id}"
        )
    
    # --- Fast-fail concurrency limit check (DB-based) ---
    user_limit = int(getattr(current_user, "max_concurrent_bots", 0) or 0)
    if user_limit > 0:
        count_stmt = select(func.count()).select_from(Meeting).where(
            and_(
                Meeting.user_id == current_user.id,
                Meeting.status.in_([
                    MeetingStatus.REQUESTED.value,
                    MeetingStatus.JOINING.value,
                    MeetingStatus.AWAITING_ADMISSION.value,
                    MeetingStatus.ACTIVE.value
                ])
            )
        )
        count_result = await db.execute(count_stmt)
        active_count = int(count_result.scalar() or 0)
        if active_count >= user_limit:
            logger.warning(f"User {current_user.id} reached concurrent bot limit {active_count}/{user_limit}. Rejecting new launch.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User has reached the maximum concurrent bot limit ({user_limit})."
            )
    
    if existing_meeting is None:
        logger.info(f"No active/valid existing meeting found for user {current_user.id}, platform '{req.platform.value}', native ID '{native_meeting_id}'. Proceeding to create a new meeting record.")
        # Create Meeting record in DB
        # Prepare data field with passcode if provided
        meeting_data = {}
        if req.passcode:
            meeting_data['passcode'] = req.passcode
            
        new_meeting = Meeting(
            user_id=current_user.id,
            platform=req.platform.value,
            platform_specific_id=native_meeting_id,
            status=MeetingStatus.REQUESTED.value,
            data=meeting_data,
            # Ensure other necessary fields like created_at are handled by the model or explicitly set
        )
        db.add(new_meeting)
        await db.commit()
        await db.refresh(new_meeting)
        meeting_id_for_bot = new_meeting.id # Use this for the bot
        logger.info(f"Created new meeting record with ID: {meeting_id_for_bot}")
        # Publish initial 'requested' status so clients receive it via WebSocket
        try:
            await publish_meeting_status_change(meeting_id_for_bot, 'requested', redis_client, req.platform.value, native_meeting_id, current_user.id)
            logger.info(f"Published initial meeting.status 'requested' for meeting {meeting_id_for_bot}")
        except Exception as _pub_err:
            logger.warning(f"Failed to publish initial 'requested' status for meeting {meeting_id_for_bot}: {_pub_err}")
    else: # This case should ideally not be reached if the 409 was raised correctly above.
          # This implies existing_meeting was found and its container was running.
        logger.error(f"Logic error: Should have raised 409 for existing meeting {existing_meeting.id}, but proceeding.")
        # To be safe, let's still use the existing meeting's ID if we reach here, though it implies a flaw.
        # However, the goal is to *prevent* duplicate bot launch if one is truly active.
        # The HTTPException should have been raised.
        # For safety, re-raise, as this path indicates an issue if the container was deemed running.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active or requested meeting already exists for this platform and meeting ID. Meeting ID: {existing_meeting.id}"
        )


    # The 'new_meeting' variable might not be defined if we used an existing one that was cleaned up.
    # We need a consistent variable for the meeting ID to pass to the bot.
    # Let's ensure 'new_meeting' is the one we are operating on for starting the container.
    # If existing_meeting was cleared, new_meeting was created.
    # If existing_meeting was NOT cleared (which means it was valid and running), an exception should have been raised.
    # So, at this point, 'new_meeting' should be the definitive meeting record for the new bot.
    # The previous 'meeting_id = new_meeting.id' should now be 'meeting_id_for_bot' as defined above.
    
    # Ensure we are using the correct meeting object for the rest of the process.
    # If existing_meeting was cleared, then new_meeting is the current one.
    current_meeting_for_bot_launch = None
    if 'new_meeting' in locals() and new_meeting is not None:
        current_meeting_for_bot_launch = new_meeting
    else:
        # This state should ideally be unreachable if logic is correct.
        # If existing_meeting was found, verified as running, it should have raised 409.
        # If existing_meeting was found, verified as NOT running, it was set to None, and new_meeting created.
        # If existing_meeting was found, no container_id, it was set to None, and new_meeting created.
        logger.error(f"Critical logic error: Reached container start without a definitive meeting object for platform '{req.platform.value}', native ID '{native_meeting_id}'.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error preparing bot launch.")

    meeting_id = current_meeting_for_bot_launch.id # Internal DB ID for the bot being launched.

    # Preflight validation of required runtime inputs (guard against bad env rendering)
    invalid_fields: list[str] = []

    def _is_invalid(val):
        try:
            if val is None:
                return True
            if isinstance(val, str):
                v = val.strip()
                return v == "" or ("\n" in v) or ("\r" in v)
            return False
        except Exception:
            return True

    if _is_invalid(constructed_url):
        invalid_fields.append("constructed_url")
    if _is_invalid(req.platform.value):
        invalid_fields.append("platform")
    if _is_invalid(native_meeting_id):
        invalid_fields.append("native_meeting_id")
    if _is_invalid(user_token):
        invalid_fields.append("user_token")

    if invalid_fields:
        logger.error(f"Preflight validation failed. Invalid fields: {invalid_fields}")
        try:
            current_meeting_for_bot_launch.status = MeetingStatus.FAILED.value
            await db.commit()
            await publish_meeting_status_change(meeting_id, MeetingStatus.FAILED.value, redis_client, req.platform.value, native_meeting_id, current_user.id)
        except Exception as _:
            pass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid inputs: {', '.join(invalid_fields)}"
        )

    # 4. Start the bot container
    zoom_obf_token_to_use: Optional[str] = None
    container_id = None
    connection_id = None
    try:
        if req.platform.value == Platform.ZOOM.value:
            direct_obf = (req.zoom_obf_token or "").strip()
            if direct_obf:
                zoom_obf_token_to_use = direct_obf
                logger.info(f"Using direct zoom_obf_token for meeting {meeting_id}")
            else:
                try:
                    access_token = resolve_zoom_access_token_from_user_data(current_user.data)

                    # Refresh if absent/expired when a refresh token exists.
                    if not access_token:
                        refresh_token = get_zoom_refresh_token(current_user.data)
                        if refresh_token:
                            client_id, client_secret = get_zoom_oauth_client_credentials()
                            refreshed = await refresh_zoom_access_token(refresh_token, client_id, client_secret)
                            access_token = refreshed["access_token"]

                            # Persist refreshed tokens into users.data
                            user_data = dict(current_user.data) if isinstance(current_user.data, dict) else {}
                            zoom_data = dict(user_data.get("zoom") or {})
                            oauth_data = dict(zoom_data.get("oauth") or {})
                            oauth_data.update({
                                "access_token": refreshed["access_token"],
                                "refresh_token": refreshed["refresh_token"],
                                "expires_at": refreshed["expires_at"],
                            })
                            if refreshed.get("scope") is not None:
                                oauth_data["scope"] = refreshed["scope"]
                            zoom_data["oauth"] = oauth_data
                            user_data["zoom"] = zoom_data
                            current_user.data = user_data
                            await db.commit()
                            await db.refresh(current_user)
                        else:
                            logger.warning(
                                f"Zoom OAuth is not connected for user {current_user.id}; "
                                f"starting meeting {meeting_id} without OBF token."
                            )

                    if access_token:
                        zoom_obf_token_to_use = await mint_zoom_obf_token(access_token, native_meeting_id)
                        logger.info(f"Minted Zoom OBF token for meeting {meeting_id}")
                    else:
                        logger.info(f"No Zoom access token available for meeting {meeting_id}; continuing without OBF token.")

                except ZoomOBFError as zoom_err:
                    logger.warning(
                        f"Zoom OBF flow failed for meeting {meeting_id} ({zoom_err.code}): {zoom_err}. "
                        "Continuing without OBF token."
                    )

        logger.info(f"Attempting to start bot container for meeting {meeting_id} (native: {native_meeting_id})...")
        container_id, connection_id = await start_bot_container(
            user_id=current_user.id,
            meeting_id=meeting_id, # Internal DB ID
            meeting_url=constructed_url,
            platform=req.platform.value,
            bot_name=req.bot_name,
            user_token=user_token,
            native_meeting_id=native_meeting_id,
            language=req.language,
            task=req.task,
            transcription_tier=req.transcription_tier,
            recording_enabled=req.recording_enabled,
            transcribe_enabled=req.transcribe_enabled,
            zoom_obf_token=zoom_obf_token_to_use,
            voice_agent_enabled=req.voice_agent_enabled,
            default_avatar_url=req.default_avatar_url
        )
        container_start_time = datetime.utcnow()
        logger.info(f"Call to start_bot_container completed. Container ID: {container_id}, Connection ID: {connection_id}")

        if not container_id or not connection_id:
            error_msg = "Failed to start bot container."
            if not container_id: error_msg += " Container ID not returned."
            if not connection_id: error_msg += " Connection ID not generated/returned."
            logger.error(f"{error_msg} for meeting {meeting_id}")
            
            current_meeting_for_bot_launch.status = MeetingStatus.FAILED.value
            await db.commit()
            await publish_meeting_status_change(meeting_id, MeetingStatus.FAILED.value, redis_client, req.platform.value, native_meeting_id, current_user.id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"status": "error", "message": error_msg, "meeting_id": meeting_id}
            )

        asyncio.create_task(_record_session_start(meeting_id, connection_id))
        logger.info(f"Scheduled background task to record session start for meeting {meeting_id}, session {connection_id}")

        # REMOVED: Status update to 'active' - now handled by bot startup callback
        # Only set the container ID, keep status as 'requested' until bot confirms it's running
        logger.info(f"Setting container ID {container_id} for meeting {meeting_id} (status remains 'requested' until bot confirms startup)")
        current_meeting_for_bot_launch.bot_container_id = container_id
        # current_meeting_for_bot_launch.status = 'active'  # REMOVED - handled by callback
        # current_meeting_for_bot_launch.start_time = datetime.utcnow()  # REMOVED - handled by callback
        await db.commit()
        await db.refresh(current_meeting_for_bot_launch)
        logger.info(f"Successfully set container ID for meeting {meeting_id}. Status remains 'requested' until bot startup callback.")

        logger.info(f"Successfully started bot container {container_id} for meeting {meeting_id}")
        return MeetingResponse.model_validate(current_meeting_for_bot_launch)

    except HTTPException as http_exc:
        logger.warning(f"HTTPException occurred during bot startup for meeting {meeting_id}: {http_exc.status_code} - {http_exc.detail}")
        try:
            # Fetch again or use current_meeting_for_bot_launch if it's the correct one to update
            meeting_to_update = await db.get(Meeting, meeting_id)  # Re-fetch to be safe with session state
            if meeting_to_update and meeting_to_update.status not in [MeetingStatus.FAILED.value, MeetingStatus.COMPLETED.value]: 
                 logger.warning(f"Updating meeting {meeting_id} status to 'failed' due to HTTPException {http_exc.status_code}.")
                 meeting_to_update.status = MeetingStatus.FAILED.value
                 if container_id: 
                     meeting_to_update.bot_container_id = container_id
                 await db.commit()
                 await publish_meeting_status_change(meeting_id, MeetingStatus.FAILED.value, redis_client, req.platform.value, native_meeting_id, current_user.id)
            elif not meeting_to_update:
                logger.error(f"Could not find meeting {meeting_id} to update status to error after HTTPException.")
        except Exception as db_err:
             logger.error(f"Failed to update meeting {meeting_id} status to error after HTTPException: {db_err}")
        raise http_exc

    except Exception as e:
        logger.error(f"Unexpected exception occurred during bot startup process for meeting {meeting_id} (after DB creation): {e}", exc_info=True)
        try:
            meeting_to_update = await db.get(Meeting, meeting_id) # Re-fetch
            if meeting_to_update and meeting_to_update.status not in [MeetingStatus.FAILED.value, MeetingStatus.COMPLETED.value]:
                 logger.warning(f"Updating meeting {meeting_id} status to 'failed' due to unexpected exception.")
                 meeting_to_update.status = MeetingStatus.FAILED.value
                 if container_id:
                     meeting_to_update.bot_container_id = container_id
                 await db.commit()
                 await publish_meeting_status_change(meeting_id, MeetingStatus.FAILED.value, redis_client, req.platform.value, native_meeting_id, current_user.id)
            elif not meeting_to_update:
                logger.error(f"Could not find meeting {meeting_id} to update status to error after unexpected exception.")
        except Exception as db_err:
             logger.error(f"Failed to update meeting {meeting_id} status to error after unexpected exception: {db_err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": f"An unexpected error occurred during bot startup: {str(e)}", "meeting_id": meeting_id}
        )

# --- ADD PUT Endpoint for Reconfiguration ---
@app.put("/bots/{platform}/{native_meeting_id}/config",
         status_code=status.HTTP_202_ACCEPTED,
         summary="Update configuration for an active bot",
         description="Updates the language and/or task for an active bot associated with the platform and native meeting ID. Sends a command via Redis Pub/Sub.",
         dependencies=[Depends(get_user_and_token)])
async def update_bot_config(
    platform: Platform,
    native_meeting_id: str,
    req: MeetingConfigUpdate,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    global redis_client # Access global redis client
    user_token, current_user = auth_data

    logger.info(f"User {current_user.id} requesting config update for {platform.value}/{native_meeting_id}: lang={req.language}, task={req.task}")

    # 1. Find the LATEST active meeting for this user/platform/native_id
    active_meeting_stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform.value,
        Meeting.platform_specific_id == native_meeting_id,
        Meeting.status == MeetingStatus.ACTIVE.value # Must be active to reconfigure
    ).order_by(Meeting.created_at.desc()) # <-- ADDED: Order by created_at descending
    
    result = await db.execute(active_meeting_stmt)
    active_meeting = result.scalars().first() # Takes the most recent one

    if not active_meeting:
        logger.warning(f"No active meeting found for user {current_user.id}, {platform.value}/{native_meeting_id} to reconfigure.")
        # Check if exists but wrong status
        existing_stmt = select(Meeting.status).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        ).order_by(Meeting.created_at.desc()).limit(1)
        existing_res = await db.execute(existing_stmt)
        existing_status = existing_res.scalars().first()
        if existing_status:
             detail = f"Meeting found but is not active (status: '{existing_status}'). Cannot reconfigure."
             status_code = status.HTTP_409_CONFLICT
        else:
             detail = f"No active meeting found for platform {platform.value} and meeting ID {native_meeting_id}."
             status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=detail)

    internal_meeting_id = active_meeting.id
    logger.info(f"[DEBUG] Found active meeting record with internal ID: {internal_meeting_id}")

    # 2. Construct and Publish command (meeting-based addressing only)
    if not redis_client:
        logger.error("Redis client not available. Cannot publish reconfigure command.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot connect to internal messaging service to send command."
        )

    command_payload = {
        "action": "reconfigure",
        "meeting_id": internal_meeting_id,
        "language": req.language,
        "task": req.task
    }
    # Publish to the meeting-specific channel the bot SUBSCRIBED to
    channel = f"bot_commands:meeting:{internal_meeting_id}"

    try:
        payload_str = json.dumps(command_payload)
        logger.info(f"Publishing command to channel '{channel}': {payload_str}")
        await redis_client.publish(channel, payload_str)
        logger.info(f"Successfully published reconfigure command for meeting {internal_meeting_id}.")
    except Exception as e:
        logger.error(f"Failed to publish reconfigure command to Redis channel {channel}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send reconfiguration command to the bot."
        )

    # 4. Return 202 Accepted
    return {"message": "Reconfiguration request accepted and sent to the bot."}
# -------------------------------------------

@app.delete("/bots/{platform}/{native_meeting_id}",
             status_code=status.HTTP_202_ACCEPTED,
             summary="Request stop for a bot",
             description="Stops a bot from any status (requested, joining, awaiting_admission, active). Sends a 'leave' command to the bot via Redis and schedules a delayed container stop. Returns 202 Accepted immediately.",
             dependencies=[Depends(get_user_and_token)])
async def stop_bot(
    platform: Platform,
    native_meeting_id: str,
    background_tasks: BackgroundTasks, # Keep BackgroundTasks
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Handles requests to stop a bot for a specific meeting.
    Allows stopping from any meeting status (requested, joining, awaiting_admission, active).
    Already completed/failed meetings return idempotent success.
    1. Finds the latest meeting record regardless of status.
    2. Finds the earliest session UID (original connection ID) associated with that meeting.
    3. Publishes a 'leave' command to the bot via Redis Pub/Sub.
    4. Schedules a background task to stop the Docker container after a delay.
    5. Bot will transition to 'completed' via exit callback.
    6. Returns 202 Accepted.
    """
    user_token, current_user = auth_data
    platform_value = platform.value

    logger.info(f"Received stop request for {platform_value}/{native_meeting_id} from user {current_user.id}")# 1. Find all meetings matching the criteria
    stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform_value,
        Meeting.platform_specific_id == native_meeting_id
    ).order_by(desc(Meeting.created_at))

    result = await db.execute(stmt)
    all_meetings = result.scalars().all()

    if not all_meetings:
        logger.warning(f"Stop request: No meeting found for {platform_value}/{native_meeting_id} for user {current_user.id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No meeting found to stop.")

    # Filter to non-terminal meetings
    non_terminal_meetings = [
        m for m in all_meetings 
        if m.status not in [MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]
    ]

    # If all meetings are terminal, return idempotent response
    if not non_terminal_meetings:
        meeting = all_meetings[0]
        logger.info(f"Stop request: Meeting {meeting.id} already in terminal state '{meeting.status}'. Returning 202 idempotently.")
        return {"message": f"Meeting already {meeting.status}."}

    # Process each non-terminal meeting (same logic as before, just in a loop)
    for meeting in non_terminal_meetings:
        # Handle meetings without container ID - can be in any non-terminal status
        if not meeting.bot_container_id:
            logger.info(f"Stop request: Meeting {meeting.id} has no container ID (status: {meeting.status}). Finalizing immediately.")
            success = await update_meeting_status(
                meeting, 
                MeetingStatus.COMPLETED, 
                db,
                completion_reason=MeetingCompletionReason.STOPPED
            )
            if success:
                await publish_meeting_status_change(meeting.id, MeetingStatus.COMPLETED.value, redis_client, platform_value, native_meeting_id, meeting.user_id)
            # Schedule post-meeting tasks even if it never became active
            logger.info(f"Scheduling post-meeting tasks for meeting {meeting.id} (no container case).")
            background_tasks.add_task(run_all_tasks, meeting.id)
            continue

        logger.info(f"Found meeting {meeting.id} (status: {meeting.status}) with container {meeting.bot_container_id} for stop request.")

        # --- SIMPLE FAST-PATH: If very recent and pre-active, finalize immediately and kill container ---
        try:
            seconds_since_created = (datetime.utcnow() - meeting.created_at).total_seconds() if meeting.created_at else None
        except Exception:
            seconds_since_created = None
        if meeting.status in [MeetingStatus.REQUESTED.value, MeetingStatus.JOINING.value, MeetingStatus.AWAITING_ADMISSION.value] and (seconds_since_created is not None and seconds_since_created < 5):
            logger.info(f"Stop request: Meeting {meeting.id} is pre-active and started {seconds_since_created:.2f}s ago. Finalizing immediately and stopping container.")
            # Mark stop intent to ignore late callbacks
            if meeting.data is None:
                meeting.data = {}
            meeting.data["stop_requested"] = True
            await db.commit()
            # Stop container ASAP (no delay) in background
            background_tasks.add_task(_delayed_container_stop, meeting.bot_container_id, meeting.id, 0)
            # Finalize meeting now
            success = await update_meeting_status(
                meeting,
                MeetingStatus.COMPLETED,
                db,
                completion_reason=MeetingCompletionReason.STOPPED
            )
            if success:
                await publish_meeting_status_change(meeting.id, MeetingStatus.COMPLETED.value, redis_client, platform_value, native_meeting_id, meeting.user_id)
            # Schedule post-meeting tasks
            background_tasks.add_task(run_all_tasks, meeting.id)
            continue

        # 2. Publish 'leave' command via Redis Pub/Sub (meeting-based addressing)
        if not redis_client:
            logger.error("Redis client not available. Cannot send leave command.")
            # Proceed with delayed stop, but log the failure to command the bot.
            # Don't raise an error here, as we still want to stop the container eventually.
        else:
            try:
                command_channel = f"bot_commands:meeting:{meeting.id}"
                payload = json.dumps({"action": "leave", "meeting_id": meeting.id})
                logger.info(f"Publishing leave command to Redis channel '{command_channel}': {payload}")
                await redis_client.publish(command_channel, payload)
                logger.info(f"Successfully published leave command for meeting {meeting.id}.")
            except Exception as e:
                logger.error(f"Failed to publish leave command to Redis channel {command_channel}: {e}", exc_info=True)
                # Log error but continue with delayed stop

        # 4. Schedule delayed container stop task
        logger.info(f"Scheduling delayed stop task for container {meeting.bot_container_id} (meeting {meeting.id}).")
        # Pass container_id, meeting_id, and delay
        background_tasks.add_task(_delayed_container_stop, meeting.bot_container_id, meeting.id, BOT_STOP_DELAY_SECONDS) 

        # 5. Update Meeting status to STOPPING immediately (source of truth)
        # This allows users to immediately request a new bot after stopping
        old_status = meeting.status
        success = await update_meeting_status(
            meeting,
            MeetingStatus.STOPPING,
            db,
            transition_reason="User requested stop"
        )
        if success:
            logger.info(f"Stop request accepted for meeting {meeting.id}. Status updated from '{old_status}' to 'stopping'. Bot will transition to completed/failed via callback.")
        else:
            logger.warning(f"Stop request: Failed to update meeting {meeting.id} status to 'stopping' (invalid transition from '{old_status}'). Proceeding anyway.")

        # 5.1. Publish meeting status change via Redis Pub/Sub
        await publish_meeting_status_change(meeting.id, 'stopping', redis_client, platform_value, native_meeting_id, meeting.user_id)
        logger.info(f"Stop request for meeting {meeting.id} accepted. Leave command sent, delayed stop scheduled.")

    # 6. Return 202 Accepted
    return {"message": "Stop request accepted and is being processed."}

# --- NEW Endpoint: Get Running Bot Status --- 
@app.get("/bots/status",
         response_model=BotStatusResponse,
         summary="Get status of running bot containers for the authenticated user",
         dependencies=[Depends(get_user_and_token)])
async def get_user_bots_status(
    auth_data: tuple[str, User] = Depends(get_user_and_token)
):
    """Retrieves a list of currently running bot containers associated with the user's API key."""
    user_token, current_user = auth_data
    user_id = current_user.id
    
    logger.info(f"Fetching running bot status for user {user_id}")
    
    try:
        # Call the function from orchestrator_utils - ADD AWAIT HERE
        running_bots_list = await get_running_bots_status(user_id)
        # Wrap the list in the response model
        return BotStatusResponse(running_bots=running_bots_list)
    except Exception as e:
        # Catch potential errors from get_running_bots_status or session issues
        logger.error(f"Error fetching bot status for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve bot status."
        )
# --- END Endpoint: Get Running Bot Status --- 

# --- ADDED: Endpoint for Vexa-Bot to report its exit status ---
@app.post("/bots/internal/callback/exited",
          status_code=status.HTTP_200_OK,
          summary="Callback for vexa-bot to report its exit status",
          include_in_schema=False) # Hidden from public API docs
async def bot_exit_callback(
    payload: BotExitCallbackPayload,
    background_tasks: BackgroundTasks, # Added BackgroundTasks dependency
    db: AsyncSession = Depends(get_db)
):
    """
    Handles the exit callback from a bot container.
    - Finds the corresponding meeting session and meeting record.
    - Updates the meeting status to 'completed' or 'failed'.
    - **Always schedules post-meeting tasks (like webhooks) regardless of exit code.**
    - If the exit was clean, it's assumed the container will self-terminate.
    - If the exit was due to an error, a delayed stop is scheduled to ensure cleanup.
    """
    logger.info(f"Received bot exit callback: connection_id={payload.connection_id}, exit_code={payload.exit_code}, reason={payload.reason}")
    
    session_uid = payload.connection_id
    exit_code = payload.exit_code

    try:
        # Find the meeting session to get the meeting_id
        session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
        session_result = await db.execute(session_stmt)
        meeting_session = session_result.scalars().first()

        if not meeting_session:
            logger.error(f"Bot exit callback: Could not find meeting session for connection_id {session_uid}. Cannot update meeting status.")
            # Still return 200 OK to the bot, as we can't do anything else.
            return {"status": "error", "detail": "Meeting session not found"}

        meeting_id = meeting_session.meeting_id
        logger.info(f"Bot exit callback: Found meeting_id {meeting_id} for connection_id {session_uid}")

        # Now get the full meeting object
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            logger.error(f"Bot exit callback: Found session but could not find meeting {meeting_id} itself.")
            return {"status": "error", "detail": f"Meeting {meeting_id} not found"}

        # Update meeting status based on exit code
        new_status = None
        if exit_code == 0:
            # Prefer bot-provided completion_reason, fallback to STOPPED
            provided_reason = payload.completion_reason or MeetingCompletionReason.STOPPED
            transition_meta = {
                "exit_code": exit_code
            }
            if payload.platform_specific_error:
                transition_meta["platform_specific_error"] = payload.platform_specific_error
            success = await update_meeting_status(
                meeting, 
                MeetingStatus.COMPLETED, 
                db,
                completion_reason=provided_reason,
                error_details=payload.error_details if isinstance(payload.error_details, str) else (json.dumps(payload.error_details) if payload.error_details else None),
                transition_reason=payload.reason,
                transition_metadata=transition_meta
            )
            if success:
                new_status = MeetingStatus.COMPLETED.value
                logger.info(f"Bot exit callback: Meeting {meeting_id} status updated to 'completed'.")
            else:
                logger.error(f"Bot exit callback: Failed to update meeting {meeting_id} status to 'completed'")
                return {"status": "error", "detail": "Failed to update meeting status"}
        else:
            # Prefer bot-provided failure_stage, fallback to ACTIVE
            provided_stage = payload.failure_stage or MeetingFailureStage.ACTIVE
            error_msg = f"Bot exited with code {exit_code}"
            if payload.reason:
                error_msg += f"; reason: {payload.reason}"
            transition_meta = {
                "exit_code": exit_code
            }
            if payload.platform_specific_error:
                transition_meta["platform_specific_error"] = payload.platform_specific_error
            success = await update_meeting_status(
                meeting, 
                MeetingStatus.FAILED, 
                db,
                failure_stage=provided_stage,
                error_details=error_msg,
                transition_reason=payload.reason,
                transition_metadata=transition_meta
            )
            if success:
                new_status = MeetingStatus.FAILED.value
                logger.warning(f"Bot exit callback: Meeting {meeting_id} status updated to 'failed' due to exit_code {exit_code}.")
            else:
                logger.error(f"Bot exit callback: Failed to update meeting {meeting_id} status to 'failed'")
                return {"status": "error", "detail": "Failed to update meeting status"}
            
            # Store detailed error information in the meeting's data field
            if payload.error_details or payload.platform_specific_error:
                if not meeting.data:
                    meeting.data = {}
                
                error_data = {
                    "exit_code": exit_code,
                    "reason": payload.reason,
                    "timestamp": datetime.utcnow().isoformat(),
                    "error_details": payload.error_details,
                    "platform_specific_error": payload.platform_specific_error
                }
                
                # Store in data field for debugging and analysis
                meeting.data["last_error"] = error_data
                logger.info(f"Bot exit callback: Stored error details in meeting {meeting_id} data: {error_data}")
        
        meeting.end_time = datetime.utcnow()
        await db.commit()
        await db.refresh(meeting)
        logger.info(f"Bot exit callback: Meeting {meeting.id} successfully updated in DB.")

        # Publish meeting status change via Redis Pub/Sub
        if new_status:
            await publish_meeting_status_change(meeting.id, new_status, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

        # ALWAYS schedule post-meeting tasks, regardless of exit code
        logger.info(f"Bot exit callback: Scheduling post-meeting tasks for meeting {meeting.id}.")
        background_tasks.add_task(run_all_tasks, meeting.id)

        # If the bot exited with an error, it might not have cleaned itself up.
        # Schedule a delayed stop as a safeguard.
        if exit_code != 0 and meeting.bot_container_id:
            logger.warning(f"Bot exit callback: Scheduling delayed stop for container {meeting.bot_container_id} of failed meeting {meeting.id}.")
            background_tasks.add_task(_delayed_container_stop, meeting.bot_container_id, meeting.id, 10)

        return {"status": "callback processed", "meeting_id": meeting.id, "final_status": meeting.status}

    except Exception as e:
        logger.error(f"Bot exit callback: An unexpected error occurred: {e}", exc_info=True)
        # Attempt to rollback any partial changes
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while processing the bot exit callback."
        )

# --- ADDED: Endpoint for Vexa-Bot to report its startup status ---
@app.post("/bots/internal/callback/started",
          status_code=status.HTTP_200_OK,
          summary="Callback for vexa-bot to report its startup status",
          include_in_schema=False) # Hidden from public API docs
async def bot_startup_callback(
    payload: BotStartupCallbackPayload,
    db: AsyncSession = Depends(get_db)
):
    """
    Handles the startup callback from a bot container.
    - Finds the corresponding meeting record using connection_id.
    - Updates the meeting status to 'active' when the bot confirms it's running.
    - Sets the start_time when the bot is actually ready.
    - Ensures database consistency when containers are automatically restarted.
    """
    logger.info(f"Received bot startup callback: connection_id={payload.connection_id}, container_id={payload.container_id}")
    
    session_uid = payload.connection_id
    container_id = payload.container_id

    try:
        # Find the meeting session to get the meeting_id
        session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
        session_result = await db.execute(session_stmt)
        meeting_session = session_result.scalars().first()

        if not meeting_session:
            logger.error(f"Bot startup callback: Could not find meeting session for connection_id {session_uid}. Cannot update meeting status.")
            return {"status": "error", "detail": "Meeting session not found"}

        meeting_id = meeting_session.meeting_id
        logger.info(f"Bot startup callback: Found meeting_id {meeting_id} for connection_id {session_uid}")

        # Now get the full meeting object
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            logger.error(f"Bot startup callback: Found session but could not find meeting {meeting_id} itself.")
            return {"status": "error", "detail": f"Meeting {meeting_id} not found"}

        # If user stopped early, ignore startup transition
        if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
            logger.info(f"Bot startup callback: stop_requested set for meeting {meeting_id}. Ignoring startup transition.")
            return {"status": "ignored", "detail": "stop requested"}

        # Update meeting status to active and set start time
        old_status = meeting.status
        if meeting.status in [MeetingStatus.REQUESTED.value, MeetingStatus.JOINING.value, MeetingStatus.AWAITING_ADMISSION.value, MeetingStatus.FAILED.value]:
            success = await update_meeting_status(
                meeting, 
                MeetingStatus.ACTIVE, 
                db
            )
            if success:
                meeting.bot_container_id = container_id
                meeting.start_time = datetime.utcnow()
                await db.commit()
                await db.refresh(meeting)
                logger.info(f"Bot startup callback: Meeting {meeting_id} status updated from '{old_status}' to 'active' with container {container_id}.")
                # No manual transition writes here; update_meeting_status already recorded the transition
            else:
                logger.error(f"Bot startup callback: Failed to update meeting {meeting_id} status to 'active'")
                return {"status": "error", "detail": "Failed to update meeting status"}
        elif meeting.status == MeetingStatus.ACTIVE.value:
            # Container restarted but meeting was already active - just update container ID
            meeting.bot_container_id = container_id
            await db.commit()
            await db.refresh(meeting)
            logger.info(f"Bot startup callback: Meeting {meeting_id} already active, updated container ID to {container_id}.")
        else:
            logger.warning(f"Bot startup callback: Meeting {meeting_id} has unexpected status '{meeting.status}', not updating.")
            return {"status": "warning", "detail": f"Meeting status '{meeting.status}' not updated"}

        # Publish meeting status change via Redis Pub/Sub (only if status changed to 'active')
        if meeting.status == MeetingStatus.ACTIVE.value and old_status != MeetingStatus.ACTIVE.value:
            await publish_meeting_status_change(meeting.id, MeetingStatus.ACTIVE.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

        return {"status": "startup processed", "meeting_id": meeting.id, "meeting_status": meeting.status}

    except Exception as e:
        logger.error(f"Bot startup callback: An unexpected error occurred: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while processing the bot startup callback."
        )

# --- ADDED: Endpoint for Vexa-Bot to report joining status ---
@app.post("/bots/internal/callback/joining",
          status_code=status.HTTP_200_OK,
          summary="Callback for vexa-bot to report joining status",
          include_in_schema=False) # Hidden from public API docs
async def bot_joining_callback(
    payload: BotStartupCallbackPayload,  # Reuse same payload structure
    db: AsyncSession = Depends(get_db)
):
    """
    Handles the joining callback from a bot container.
    - Finds the corresponding meeting record using connection_id.
    - Updates the meeting status to 'joining' when the bot starts joining.
    """
    logger.info(f"Received bot joining callback: connection_id={payload.connection_id}, container_id={payload.container_id}")
    
    session_uid = payload.connection_id
    container_id = payload.container_id

    try:
        # Find the meeting session to get the meeting_id
        session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
        session_result = await db.execute(session_stmt)
        meeting_session = session_result.scalars().first()

        if not meeting_session:
            logger.error(f"Bot joining callback: Could not find meeting session for connection_id {session_uid}. Cannot update meeting status.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting session not found for connection_id: {session_uid}"
            )

        # Find the meeting record
        meeting_stmt = select(Meeting).where(Meeting.id == meeting_session.meeting_id)
        meeting_result = await db.execute(meeting_stmt)
        meeting = meeting_result.scalars().first()

        if not meeting:
            logger.error(f"Bot joining callback: Could not find meeting for session {meeting_session.meeting_id}. Cannot update meeting status.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for session: {meeting_session.meeting_id}"
            )

        # If user stopped early, ignore joining transition
        if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
            logger.info(f"Bot joining callback: stop_requested set for meeting {meeting.id}. Ignoring joining transition.")
            return {"status": "ignored", "detail": "stop requested"}

        old_status = meeting.status
        # Update meeting status to joining
        success = await update_meeting_status(
            meeting=meeting,
            new_status=MeetingStatus.JOINING,
            db=db
        )
        if success:
            logger.info(f"Bot joining callback: Successfully updated meeting {meeting.id} status to 'joining'")
            # Publish status change to Redis
            await publish_meeting_status_change(meeting.id, MeetingStatus.JOINING.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)
            # No manual transition writes here; update_meeting_status already recorded the transition

        return {"status": "joining processed", "meeting_id": meeting.id, "meeting_status": meeting.status}

    except Exception as e:
        logger.error(f"Bot joining callback: An unexpected error occurred: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while processing the bot joining callback."
        )

# --- ADDED: Endpoint for Vexa-Bot to report awaiting admission status ---
@app.post("/bots/internal/callback/awaiting_admission",
          status_code=status.HTTP_200_OK,
          summary="Callback for vexa-bot to report awaiting admission status",
          include_in_schema=False) # Hidden from public API docs
async def bot_awaiting_admission_callback(
    payload: BotStartupCallbackPayload,  # Reuse same payload structure
    db: AsyncSession = Depends(get_db)
):
    """
    Handles the awaiting admission callback from a bot container.
    - Finds the corresponding meeting record using connection_id.
    - Updates the meeting status to 'awaiting_admission' when the bot is in waiting room.
    """
    logger.info(f"Received bot awaiting admission callback: connection_id={payload.connection_id}, container_id={payload.container_id}")
    
    session_uid = payload.connection_id
    container_id = payload.container_id

    try:
        # Find the meeting session to get the meeting_id
        session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
        session_result = await db.execute(session_stmt)
        meeting_session = session_result.scalars().first()

        if not meeting_session:
            logger.error(f"Bot awaiting admission callback: Could not find meeting session for connection_id {session_uid}. Cannot update meeting status.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting session not found for connection_id: {session_uid}"
            )

        # Find the meeting record
        meeting_stmt = select(Meeting).where(Meeting.id == meeting_session.meeting_id)
        meeting_result = await db.execute(meeting_stmt)
        meeting = meeting_result.scalars().first()

        if not meeting:
            logger.error(f"Bot awaiting admission callback: Could not find meeting for session {meeting_session.meeting_id}. Cannot update meeting status.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for session: {meeting_session.meeting_id}"
            )

        # If user stopped early, ignore awaiting admission transition
        if meeting.data and isinstance(meeting.data, dict) and meeting.data.get("stop_requested"):
            logger.info(f"Bot awaiting admission callback: stop_requested set for meeting {meeting.id}. Ignoring waiting room transition.")
            return {"status": "ignored", "detail": "stop requested"}

        # Update meeting status to awaiting_admission
        success = await update_meeting_status(
            meeting=meeting,
            new_status=MeetingStatus.AWAITING_ADMISSION,
            db=db
        )

        if success:
            logger.info(f"Bot awaiting admission callback: Successfully updated meeting {meeting.id} status to 'awaiting_admission'")
            # Publish status change to Redis
            await publish_meeting_status_change(meeting.id, MeetingStatus.AWAITING_ADMISSION.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)
            # No manual transition writes here; update_meeting_status already recorded the transition

        return {"status": "awaiting_admission processed", "meeting_id": meeting.id, "meeting_status": meeting.status}

    except Exception as e:
        logger.error(f"Bot awaiting admission callback: An unexpected error occurred: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while processing the bot awaiting admission callback."
        )

# --- UNIFIED CALLBACK ENDPOINT ---
@app.post("/bots/internal/callback/status_change",
          status_code=status.HTTP_200_OK,
          summary="Unified callback for all bot status changes",
          description="Handles all bot status changes (joining, awaiting_admission, active, completed, failed) with webhook notifications",
          include_in_schema=False) # Hidden from public API docs
async def bot_status_change_callback(
    payload: BotStatusChangePayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Unified callback endpoint for all bot status changes.
    
    This endpoint handles:
    - joining: Bot starts joining the meeting
    - awaiting_admission: Bot is in waiting room
    - active: Bot is admitted and active in meeting
    - completed: Bot successfully completed the meeting
    - failed: Bot failed for some reason
    
    All status changes trigger webhook notifications if user has webhook URL configured.
    """
    logger.info(f"Received unified bot status change callback: connection_id={payload.connection_id}, status={payload.status.value}, reason={payload.reason}")
    
    session_uid = payload.connection_id
    new_status = payload.status
    reason = payload.reason

    # #region agent log
    try:
        with open('/home/dima/dev/.cursor/debug.log', 'a') as f:
            import json
            f.write(json.dumps({"location": "bot-manager/main.py:1320", "message": "Unified callback received", "data": {"connection_id": session_uid, "new_status": new_status.value, "reason": reason}, "timestamp": __import__('time').time(), "sessionId": "debug-session", "runId": "run1", "hypothesisId": "A"}) + "\n")
    except: pass
    # #endregion

    try:
        # Find the meeting session to get the meeting_id
        session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
        session_result = await db.execute(session_stmt)
        meeting_session = session_result.scalars().first()

        if not meeting_session:
            logger.error(f"Bot status change callback: Could not find meeting session for connection_id {session_uid}")
            return {"status": "error", "detail": f"Meeting session not found for connection_id: {session_uid}"}

        meeting_id = meeting_session.meeting_id
        logger.info(f"Bot status change callback: Found meeting_id {meeting_id} for connection_id {session_uid}")

        # Get the full meeting object and refresh to ensure we have latest status
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            logger.error(f"Bot status change callback: Could not find meeting {meeting_id}")
            return {"status": "error", "detail": f"Meeting {meeting_id} not found"}
        
        # Refresh meeting to get latest status from database
        await db.refresh(meeting)
        logger.info(f"[DEBUG] Bot status change callback: Meeting {meeting_id} current status='{meeting.status}', requested status='{new_status.value}'")

        # Check if user stopped early (ignore transitions except for completed/failed)
        if (meeting.data and isinstance(meeting.data, dict) and 
            meeting.data.get("stop_requested") and 
            new_status not in [MeetingStatus.COMPLETED, MeetingStatus.FAILED]):
            logger.info(f"Bot status change callback: stop_requested set for meeting {meeting.id}. Ignoring {new_status.value} transition.")
            return {"status": "ignored", "detail": "stop requested"}

        old_status = meeting.status
        
        # Handle different status changes
        success = None  # Initialize success variable
        if new_status == MeetingStatus.COMPLETED:
            # Handle completion
            success = await update_meeting_status(
                meeting=meeting,
                new_status=MeetingStatus.COMPLETED,
                db=db,
                completion_reason=payload.completion_reason
            )
            
            if success:
                meeting.end_time = datetime.utcnow()
                await db.commit()
                await db.refresh(meeting)
                
                # Schedule post-meeting tasks (including original webhook)
                background_tasks.add_task(run_all_tasks, meeting.id)
                
        elif new_status == MeetingStatus.FAILED:
            # Handle failure
            success = await update_meeting_status(
                meeting=meeting,
                new_status=MeetingStatus.FAILED,
                db=db,
                failure_stage=payload.failure_stage,
                error_details=str(payload.error_details) if payload.error_details else None
            )
            
            if success:
                meeting.end_time = datetime.utcnow()
                
                # Store detailed error information
                if payload.error_details or payload.platform_specific_error:
                    if not meeting.data:
                        meeting.data = {}
                    meeting.data["last_error"] = {
                        "exit_code": payload.exit_code,
                        "reason": payload.reason,
                        "timestamp": datetime.utcnow().isoformat(),
                        "error_details": payload.error_details,
                        "platform_specific_error": payload.platform_specific_error
                    }
                
                await db.commit()
                await db.refresh(meeting)
                
                # Schedule post-meeting tasks (including original webhook)
                background_tasks.add_task(run_all_tasks, meeting.id)
                
        elif new_status == MeetingStatus.ACTIVE:
            # Handle activation
            if meeting.status in [MeetingStatus.REQUESTED.value, MeetingStatus.JOINING.value, MeetingStatus.AWAITING_ADMISSION.value, MeetingStatus.FAILED.value]:
                success = await update_meeting_status(meeting, MeetingStatus.ACTIVE, db)
                if success:
                    meeting.bot_container_id = payload.container_id
                    meeting.start_time = datetime.utcnow()
                    await db.commit()
                    await db.refresh(meeting)
            elif meeting.status == MeetingStatus.ACTIVE.value:
                # Container restarted but meeting was already active
                meeting.bot_container_id = payload.container_id
                await db.commit()
                await db.refresh(meeting)
                logger.info(f"Bot status change callback: Meeting {meeting_id} already active, updated container ID to {payload.container_id}")
                return {"status": "container_updated", "meeting_id": meeting.id, "meeting_status": meeting.status}
            else:
                logger.warning(f"Bot status change callback: Meeting {meeting_id} has unexpected status '{meeting.status}', not updating to active")
                success = False
                return {"status": "warning", "detail": f"Meeting status '{meeting.status}' not updated to active"}
                
        else:
            # Handle other status changes (joining, awaiting_admission)
            # #region agent log
            try:
                with open('/home/dima/dev/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"location": "bot-manager/main.py:1423", "message": "Before update_meeting_status", "data": {"meeting_id": meeting_id, "old_status": meeting.status, "new_status": new_status.value}, "timestamp": __import__('time').time(), "sessionId": "debug-session", "runId": "run1", "hypothesisId": "B"}) + "\n")
            except: pass
            # #endregion
            
            success = await update_meeting_status(meeting, new_status, db)
            
            # #region agent log
            try:
                with open('/home/dima/dev/.cursor/debug.log', 'a') as f:
                    import json
                    f.write(json.dumps({"location": "bot-manager/main.py:1429", "message": "After update_meeting_status", "data": {"meeting_id": meeting_id, "success": success, "meeting_status": meeting.status}, "timestamp": __import__('time').time(), "sessionId": "debug-session", "runId": "run1", "hypothesisId": "B"}) + "\n")
            except: pass
            # #endregion
            
            if not success:
                logger.error(f"Bot status change callback: Failed to update meeting {meeting_id} status to '{new_status.value}'")
                # #region agent log
                try:
                    with open('/home/dima/dev/.cursor/debug.log', 'a') as f:
                        import json
                        f.write(json.dumps({"location": "bot-manager/main.py:1435", "message": "Status update failed", "data": {"meeting_id": meeting_id, "old_status": old_status, "new_status": new_status.value}, "timestamp": __import__('time').time(), "sessionId": "debug-session", "runId": "run1", "hypothesisId": "C"}) + "\n")
                except: pass
                # #endregion
                return {"status": "error", "detail": "Failed to update meeting status"}

        # Publish meeting status change via Redis Pub/Sub
        if success or (new_status == MeetingStatus.ACTIVE and meeting.status == MeetingStatus.ACTIVE.value):
            await publish_meeting_status_change(meeting.id, new_status.value, redis_client, meeting.platform, meeting.platform_specific_id, meeting.user_id)

        # Schedule webhook task for status change (for all status changes)
        await schedule_status_webhook_task(
            meeting=meeting,
            background_tasks=background_tasks,
            old_status=old_status,
            new_status=new_status.value,
            reason=reason,
            transition_source="bot_callback"
        )

        return {"status": "processed", "meeting_id": meeting.id, "meeting_status": meeting.status}

    except Exception as e:
        logger.error(f"Bot status change callback: An unexpected error occurred: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while processing the bot status change callback."
        )

# --- RECORDING ENDPOINTS ---

@app.post("/internal/recordings/upload",
          status_code=status.HTTP_201_CREATED,
          summary="Internal: Bot uploads a finalized recording",
          include_in_schema=False)
async def internal_upload_recording(
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(default=None),
    session_uid: Optional[str] = Form(default=None),
    media_type: str = Form(default="audio"),
    media_format: str = Form(default="wav"),
    duration_seconds: Optional[float] = Form(default=None),
    sample_rate: Optional[int] = Form(default=None),
    is_final: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
):
    """
    Internal endpoint called by bots to upload finalized recordings.
    Creates a Recording + MediaFile row and stores the file in object storage.
    """
    # Support both payload styles:
    # 1) Flat multipart fields (session_uid, media_format, etc.)
    # 2) metadata JSON field produced by RecordingService.upload()
    if metadata:
        try:
            metadata_obj = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="Invalid JSON in metadata form field")

        session_uid = session_uid or metadata_obj.get("session_uid")
        media_type = metadata_obj.get("media_type", media_type)
        media_format = metadata_obj.get("format", media_format)
        duration_seconds = metadata_obj.get("duration_seconds", duration_seconds)
        sample_rate = metadata_obj.get("sample_rate", sample_rate)
        if "is_final" in metadata_obj:
            is_final = _to_bool(metadata_obj.get("is_final"), default=True)

    if not session_uid:
        raise HTTPException(status_code=422, detail="session_uid is required")

    logger.info(f"Recording upload received: session_uid={session_uid}, type={media_type}, format={media_format}")

    # Find meeting session to resolve meeting_id and user_id
    session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
    session_result = await db.execute(session_stmt)
    meeting_session = session_result.scalars().first()

    if not meeting_session:
        if not is_final:
            return {
                "status": "pending",
                "detail": f"Meeting session not ready yet: {session_uid}",
            }
        raise HTTPException(status_code=404, detail=f"Meeting session not found: {session_uid}")

    meeting = await db.get(Meeting, meeting_session.meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting not found for session: {session_uid}")

    user_id = meeting.user_id

    # Read file content
    file_data = await file.read()
    file_size = len(file_data)
    logger.info(f"Read {file_size} bytes from uploaded file for session {session_uid}")

    use_meeting_data_mode = get_recording_metadata_mode() == "meeting_data"
    meeting_data = dict(meeting.data or {}) if use_meeting_data_mode else {}
    recordings = list(meeting_data.get("recordings") or []) if use_meeting_data_mode else []
    existing_recording_payload = None
    existing_recording_index = None
    legacy_recording_id = _new_recording_numeric_id() if use_meeting_data_mode else None
    if use_meeting_data_mode:
        for idx, rec in enumerate(recordings):
            if (
                isinstance(rec, dict)
                and rec.get("session_uid") == session_uid
                and rec.get("source") == RecordingSource.BOT.value
            ):
                existing_recording_payload = rec
                existing_recording_index = idx
                legacy_recording_id = rec.get("id") or legacy_recording_id
                break
    recording = None
    if not use_meeting_data_mode:
        recording = Recording(
            meeting_id=meeting.id,
            user_id=user_id,
            session_uid=session_uid,
            source="bot",
            status="uploading",
        )
        db.add(recording)
        await db.flush()  # get recording.id
    storage_recording_id = legacy_recording_id if use_meeting_data_mode else recording.id

    # Upload to object storage
    storage_path = f"recordings/{user_id}/{storage_recording_id}/{session_uid}.{media_format}"
    content_type_map = {
        "wav": "audio/wav",
        "webm": "video/webm",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
    }
    content_type = content_type_map.get(media_format, "application/octet-stream")

    try:
        storage = get_storage_client()
        storage.upload_file(storage_path, file_data, content_type=content_type)
    except Exception as e:
        logger.error(f"Storage upload failed for session {session_uid}: {e}", exc_info=True)
        if recording is not None:
            recording.status = "failed"
            recording.error_message = str(e)
            await db.commit()
        raise HTTPException(status_code=500, detail="Failed to upload recording to storage")

    if get_recording_metadata_mode() == "meeting_data":
        existing_media = (
            existing_recording_payload.get("media_files", [{}])[0]
            if existing_recording_payload else {}
        )
        media_file_id = existing_media.get("id") or _new_recording_numeric_id()
        created_at = (
            existing_recording_payload.get("created_at")
            if existing_recording_payload else datetime.utcnow().isoformat()
        )
        recording_payload = {
            "id": legacy_recording_id,
            "meeting_id": meeting.id,
            "user_id": user_id,
            "session_uid": session_uid,
            "source": RecordingSource.BOT.value,
            "status": RecordingStatus.COMPLETED.value if is_final else RecordingStatus.IN_PROGRESS.value,
            "created_at": created_at,
            "completed_at": datetime.utcnow().isoformat() if is_final else None,
            "media_files": [
                {
                    "id": media_file_id,
                    "type": media_type,
                    "format": media_format,
                    "storage_path": storage_path,
                    "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
                    "file_size_bytes": file_size,
                    "duration_seconds": duration_seconds,
                    "metadata": {"sample_rate": sample_rate} if sample_rate else {},
                    "created_at": datetime.utcnow().isoformat(),
                }
            ],
        }
        if existing_recording_index is None:
            recordings.append(recording_payload)
        else:
            recordings[existing_recording_index] = recording_payload
        meeting_data["recordings"] = recordings
        meeting.data = meeting_data
        attributes.flag_modified(meeting, "data")
        await db.commit()
        if is_final:
            asyncio.create_task(send_event_webhook(user_id, "recording.completed", {"recording": recording_payload}))
        return {
            "recording_id": recording_payload["id"],
            "media_file_id": media_file_id,
            "storage_path": storage_path,
            "status": recording_payload["status"],
        }

    # Build metadata dict
    file_metadata = {}
    if sample_rate:
        file_metadata["sample_rate"] = sample_rate

    # Create MediaFile row
    media_file = MediaFile(
        recording_id=recording.id,
        type=media_type,
        format=media_format,
        storage_path=storage_path,
        storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
        file_size_bytes=file_size,
        duration_seconds=duration_seconds,
        extra_metadata=file_metadata if file_metadata else {},
    )
    db.add(media_file)

    # Mark recording as completed
    recording.status = "completed"
    recording.completed_at = datetime.utcnow()

    await db.commit()
    await db.refresh(recording)
    await db.refresh(media_file)

    logger.info(f"Recording {recording.id} created with media file {media_file.id} for session {session_uid}")

    # Fire webhook for recording completion
    asyncio.create_task(send_event_webhook(user_id, "recording.completed", {
        "recording": {
            "id": recording.id,
            "meeting_id": recording.meeting_id,
            "session_uid": session_uid,
            "status": recording.status,
            "media_file_id": media_file.id,
            "file_size_bytes": file_size,
            "media_type": media_type,
            "media_format": media_format,
        }
    }))

    return {
        "recording_id": recording.id,
        "media_file_id": media_file.id,
        "storage_path": storage_path,
        "status": recording.status,
    }


@app.get("/recordings",
         response_model=RecordingListResponse,
         summary="List recordings for the authenticated user")
async def list_recordings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    meeting_id: Optional[int] = Query(default=None),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """List recordings owned by the authenticated user, with optional meeting_id filter."""
    token, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        recordings = await _list_meeting_data_recordings(db, user.id, meeting_id=meeting_id)
        page = recordings[offset:offset + limit]
        return RecordingListResponse(
            recordings=[RecordingResponse.model_validate(r) for r in page]
        )

    stmt = select(Recording).where(Recording.user_id == user.id)
    if meeting_id is not None:
        stmt = stmt.where(Recording.meeting_id == meeting_id)
    stmt = stmt.order_by(desc(Recording.created_at)).offset(offset).limit(limit)

    result = await db.execute(stmt)
    recordings = result.scalars().all()

    # Eagerly load media files for each recording
    recording_responses = []
    for rec in recordings:
        await db.refresh(rec, ["media_files"])
        recording_responses.append(RecordingResponse.model_validate(rec))

    return RecordingListResponse(recordings=recording_responses)


@app.get("/recordings/{recording_id}",
         response_model=RecordingResponse,
         summary="Get a single recording with media file details")
async def get_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Get recording details including all media files."""
    token, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        _meeting, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return RecordingResponse.model_validate(rec)

    recording = await db.get(Recording, recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    if recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    await db.refresh(recording, ["media_files"])
    return RecordingResponse.model_validate(recording)


@app.get("/recordings/{recording_id}/media/{media_file_id}/download",
         summary="Get a presigned download URL for a media file")
async def download_media_file(
    recording_id: int,
    media_file_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Generate a presigned URL to download a specific media file."""
    token, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        _meeting, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        media_file = None
        for mf in rec.get("media_files") or []:
            if int(mf.get("id", -1)) == media_file_id:
                media_file = mf
                break
        if media_file is None:
            raise HTTPException(status_code=404, detail="Media file not found")
        fmt = str(media_file.get("format", "bin")).lower()
        media_type = str(media_file.get("type", "audio")).lower()
        content_type_map = {
            "wav": "audio/wav",
            "webm": "video/webm" if media_type == "video" else "audio/webm",
            "opus": "audio/opus",
            "mp3": "audio/mpeg",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
        }
        content_type = content_type_map.get(fmt, "application/octet-stream")
        try:
            if media_file.get("storage_backend") == "local":
                url = f"/recordings/{recording_id}/media/{media_file_id}/raw"
            else:
                storage = get_storage_client()
                url = storage.get_presigned_url(media_file["storage_path"], expires=3600)
        except Exception as e:
            logger.error(f"Failed to generate download URL for media file {media_file_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to generate download URL")
        return {
            "download_url": url,
            "filename": f"{recording_id}_{media_type}.{fmt}",
            "content_type": content_type,
            "file_size_bytes": media_file.get("file_size_bytes"),
        }

    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Find the media file
    stmt = select(MediaFile).where(
        and_(MediaFile.id == media_file_id, MediaFile.recording_id == recording_id)
    )
    result = await db.execute(stmt)
    media_file = result.scalars().first()

    if not media_file:
        raise HTTPException(status_code=404, detail="Media file not found")

    content_type_map = {
        "wav": "audio/wav",
        "webm": "video/webm" if media_file.type == "video" else "audio/webm",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }
    content_type = content_type_map.get(media_file.format.lower(), "application/octet-stream")

    try:
        if media_file.storage_backend == "local":
            # For local backend, use authenticated API streaming endpoint instead of file:// URLs.
            url = f"/recordings/{recording_id}/media/{media_file_id}/raw"
        else:
            storage = get_storage_client()
            url = storage.get_presigned_url(media_file.storage_path, expires=3600)
    except Exception as e:
        logger.error(f"Failed to generate download URL for media file {media_file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate download URL")

    return {
        "download_url": url,
        "filename": f"{media_file.recording_id}_{media_file.type}.{media_file.format}",
        "content_type": content_type,
        "file_size_bytes": media_file.file_size_bytes,
    }


@app.get("/recordings/{recording_id}/media/{media_file_id}/raw",
         summary="Download media file content via API (local storage backend)")
async def download_media_file_raw(
    recording_id: int,
    media_file_id: int,
    request: Request,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream media bytes via authenticated API.
    Used primarily for local filesystem backend where presigned URLs are not available.
    """
    token, user = auth
    def _parse_range_header(range_header: Optional[str], total_length: int) -> Optional[tuple[int, int]]:
        if not range_header:
            return None
        if not range_header.startswith("bytes="):
            raise HTTPException(status_code=416, detail="Invalid Range header")
        spec = range_header[len("bytes="):].strip()
        if "," in spec:
            raise HTTPException(status_code=416, detail="Multiple ranges are not supported")
        start_s, sep, end_s = spec.partition("-")
        if sep != "-":
            raise HTTPException(status_code=416, detail="Invalid Range header")
        if start_s == "" and end_s == "":
            raise HTTPException(status_code=416, detail="Invalid Range header")
        if start_s == "":
            suffix_len = int(end_s)
            if suffix_len <= 0:
                raise HTTPException(status_code=416, detail="Invalid Range header")
            if suffix_len > total_length:
                suffix_len = total_length
            start = total_length - suffix_len
            end = total_length - 1
            return start, end
        start = int(start_s)
        if start < 0 or start >= total_length:
            raise HTTPException(status_code=416, detail="Range start out of bounds")
        if end_s == "":
            end = total_length - 1
        else:
            end = int(end_s)
            if end < start:
                raise HTTPException(status_code=416, detail="Invalid Range header")
            if end >= total_length:
                end = total_length - 1
        return start, end

    def _build_media_response(payload: bytes, content_type: str, filename: str) -> Response:
        total = len(payload)
        range_header = request.headers.get("range")
        base_headers = {
            "Content-Disposition": f'inline; filename="{filename}"',
            "Accept-Ranges": "bytes",
        }
        if not range_header:
            return Response(content=payload, media_type=content_type, headers=base_headers)
        try:
            parsed = _parse_range_header(range_header, total)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=416, detail="Invalid Range header")
        if parsed is None:
            return Response(content=payload, media_type=content_type, headers=base_headers)
        start, end = parsed
        chunk = payload[start:end + 1]
        headers = dict(base_headers)
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(len(chunk))
        return Response(content=chunk, media_type=content_type, status_code=206, headers=headers)

    if get_recording_metadata_mode() == "meeting_data":
        _meeting, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        media_file = None
        for mf in rec.get("media_files") or []:
            if int(mf.get("id", -1)) == media_file_id:
                media_file = mf
                break
        if media_file is None:
            raise HTTPException(status_code=404, detail="Media file not found")
        fmt = str(media_file.get("format", "bin")).lower()
        media_type = str(media_file.get("type", "audio")).lower()
        content_type_map = {
            "wav": "audio/wav",
            "webm": "video/webm" if media_type == "video" else "audio/webm",
            "opus": "audio/opus",
            "mp3": "audio/mpeg",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
        }
        content_type = content_type_map.get(fmt, "application/octet-stream")
        filename = f"{recording_id}_{media_type}.{fmt}"
        try:
            storage = get_storage_client()
            data = storage.download_file(media_file["storage_path"])
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Media file content not found in storage")
        except Exception as e:
            logger.error(f"Failed raw media download for media file {media_file_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to read media file from storage")
        return _build_media_response(data, content_type, filename)

    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    stmt = select(MediaFile).where(
        and_(MediaFile.id == media_file_id, MediaFile.recording_id == recording_id)
    )
    result = await db.execute(stmt)
    media_file = result.scalars().first()
    if not media_file:
        raise HTTPException(status_code=404, detail="Media file not found")

    content_type_map = {
        "wav": "audio/wav",
        "webm": "video/webm" if media_file.type == "video" else "audio/webm",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }
    content_type = content_type_map.get(media_file.format.lower(), "application/octet-stream")
    filename = f"{media_file.recording_id}_{media_file.type}.{media_file.format}"

    try:
        storage = get_storage_client()
        data = storage.download_file(media_file.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file content not found in storage")
    except Exception as e:
        logger.error(f"Failed raw media download for media file {media_file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read media file from storage")

    return _build_media_response(data, content_type, filename)


@app.delete("/recordings/{recording_id}",
            summary="Delete a recording and its media files")
async def delete_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete a recording, its media files from storage, and all database rows."""
    token, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        meeting, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if meeting is None or rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        storage = get_storage_client()
        for mf in rec.get("media_files") or []:
            path = mf.get("storage_path")
            if not path:
                continue
            try:
                storage.delete_file(path)
            except Exception as e:
                logger.warning(f"Failed to delete storage file {path}: {e}")
        current_data = dict(meeting.data or {})
        existing = list(current_data.get("recordings") or [])
        current_data["recordings"] = [
            r for r in existing
            if not (isinstance(r, dict) and int(r.get("id", -1)) == recording_id)
        ]
        meeting.data = current_data
        attributes.flag_modified(meeting, "data")
        await db.commit()
        return {"status": "deleted", "recording_id": recording_id}

    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    await db.refresh(recording, ["media_files"])

    # Delete files from object storage
    storage = get_storage_client()
    for mf in recording.media_files:
        try:
            storage.delete_file(mf.storage_path)
        except Exception as e:
            logger.warning(f"Failed to delete storage file {mf.storage_path}: {e}")

    # Delete from database (cascade deletes media_files and transcription_jobs)
    await db.delete(recording)
    await db.commit()

    return {"status": "deleted", "recording_id": recording_id}


# --- RECORDING CONFIG ENDPOINTS ---

class RecordingConfigUpdate(BaseModel):
    enabled: Optional[bool] = Field(None, description="Enable or disable recording for this user's bots")
    capture_modes: Optional[List[str]] = Field(None, description="Capture modes: ['audio'], ['audio', 'video'], etc.")


@app.get("/recording-config",
         summary="Get recording configuration for the authenticated user",
         tags=["Recordings"])
async def get_recording_config(
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Returns the user's recording configuration, with defaults from environment."""
    token, user = auth
    user_config = {}
    if user.data and isinstance(user.data, dict):
        user_config = user.data.get("recording_config", {})

    return {
        "enabled": user_config.get("enabled", os.environ.get("RECORDING_ENABLED", "false").lower() == "true"),
        "capture_modes": user_config.get("capture_modes", os.environ.get("CAPTURE_MODES", "audio").split(",")),
    }


@app.put("/recording-config",
         summary="Update recording configuration for the authenticated user",
         tags=["Recordings"])
async def update_recording_config(
    config: RecordingConfigUpdate,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Update the user's recording configuration. Only provided fields are updated."""
    token, user = auth

    if not user.data:
        user.data = {}

    # Ensure we create a new dict to trigger SQLAlchemy change detection
    new_data = dict(user.data)
    recording_config = new_data.get("recording_config", {})

    if config.enabled is not None:
        recording_config["enabled"] = config.enabled
    if config.capture_modes is not None:
        valid_modes = {"audio", "video", "screenshot"}
        for mode in config.capture_modes:
            if mode not in valid_modes:
                raise HTTPException(status_code=400, detail=f"Invalid capture mode: {mode}. Valid: {sorted(valid_modes)}")
        recording_config["capture_modes"] = config.capture_modes

    new_data["recording_config"] = recording_config
    user.data = new_data

    from sqlalchemy.orm import attributes
    attributes.flag_modified(user, "data")

    await db.commit()
    await db.refresh(user)

    return {
        "enabled": recording_config.get("enabled", False),
        "capture_modes": recording_config.get("capture_modes", ["audio"]),
    }


# --- RECONCILIATION TASK: Detect and fix zombie meetings and orphan containers ---
async def reconcile_meetings_and_containers():
    """
    Periodic reconciliation task to detect and fix:
    1. Zombie meetings: ACTIVE/STOPPING/JOINING/AWAITING_ADMISSION but container doesn't exist
    2. Orphan containers: Container running but meeting is COMPLETED/FAILED
    
    This ensures no situation where:
    - No container but zombie bot participant in meeting
    - Stopped bot but container still running
    """
    logger.info("[Reconciliation] Starting reconciliation task...")
    zombie_meetings_fixed = 0
    orphan_containers_killed = 0
    
    try:
        async with async_session_local() as db:
            # --- PART 1: Find zombie meetings (non-terminal but no container) ---
            non_terminal_statuses = [
                MeetingStatus.ACTIVE.value,
                MeetingStatus.STOPPING.value,
                MeetingStatus.JOINING.value,
                MeetingStatus.AWAITING_ADMISSION.value,
                MeetingStatus.REQUESTED.value
            ]
            
            stmt = select(Meeting).where(
                Meeting.status.in_(non_terminal_statuses)
            )
            result = await db.execute(stmt)
            non_terminal_meetings = result.scalars().all()
            
            logger.info(f"[Reconciliation] Found {len(non_terminal_meetings)} non-terminal meetings to check")
            
            for meeting in non_terminal_meetings:
                if not meeting.bot_container_id:
                    # Meeting has no container ID - skip (might be in REQUESTED state)
                    continue
                
                # Check if container/process actually exists and is running
                try:
                    container_exists = await verify_container_running(meeting.bot_container_id)
                except Exception as e:
                    # Error during verification - log but don't mark as zombie
                    # This could happen if orchestrator is unavailable or misconfigured
                    logger.error(
                        f"[Reconciliation] Error verifying container/process {meeting.bot_container_id} "
                        f"for meeting {meeting.id}: {e}. Skipping this meeting."
                    )
                    continue
                
                if not container_exists:
                    # Additional safety check: if container_id looks like a container name (not a PID),
                    # and we couldn't find it, check if there are any running processes at all
                    # This prevents false positives when the registry is empty but processes are running
                    is_likely_name = not meeting.bot_container_id.isdigit()
                    
                    if is_likely_name:
                        # For container names, be more conservative - check if any processes are running
                        # If registry is empty but meeting is active, might be a timing issue
                        try:
                            all_running = await get_running_bots_status(meeting.user_id)
                            if len(all_running) > 0:
                                logger.warning(
                                    f"[Reconciliation] Meeting {meeting.id} has container name '{meeting.bot_container_id}' "
                                    f"not found in registry, but {len(all_running)} processes are running. "
                                    f"Skipping zombie detection to avoid false positive."
                                )
                                continue
                        except Exception as e:
                            logger.error(f"[Reconciliation] Error checking running bots for safety check: {e}")
                    
                    logger.warning(
                        f"[Reconciliation] ZOMBIE MEETING detected: Meeting {meeting.id} "
                        f"(status: {meeting.status}, container/process: {meeting.bot_container_id}) "
                        f"has no running container/process. Finalizing..."
                    )
                    
                    # Finalize the meeting
                    success = await update_meeting_status(
                        meeting,
                        MeetingStatus.COMPLETED,
                        db,
                        completion_reason=MeetingCompletionReason.STOPPED,
                        transition_reason="reconciliation_zombie_meeting",
                        transition_metadata={
                            "detected_by": "reconciliation_task",
                            "original_status": meeting.status,
                            "container_id": meeting.bot_container_id
                        }
                    )
                    
                    if success:
                        await publish_meeting_status_change(
                            meeting.id, 
                            MeetingStatus.COMPLETED.value, 
                            redis_client, 
                            meeting.platform, 
                            meeting.platform_specific_id, 
                            meeting.user_id
                        )
                        zombie_meetings_fixed += 1
                        logger.info(f"[Reconciliation] Fixed zombie meeting {meeting.id}")
                    else:
                        logger.error(
                            f"[Reconciliation] Failed to finalize zombie meeting {meeting.id} "
                            f"(status transition may be invalid)"
                        )
            
            await db.commit()
            
            # --- PART 2: Find orphan containers/processes (running but meeting is terminal) ---
            # Get all running bots using the abstracted orchestrator function
            # This works for both Docker containers and process orchestrator (Lite setup)
            all_running_bots = []
            try:
                # Get all unique user IDs from ALL meetings (not just non-terminal)
                # This ensures we catch orphan containers/processes even if user has no active meetings
                async with async_session_local() as db_users:
                    user_stmt = select(Meeting.user_id).distinct()
                    user_result = await db_users.execute(user_stmt)
                    user_ids = [row[0] for row in user_result.all()]
                
                logger.info(f"[Reconciliation] Checking running bots for {len(user_ids)} users")
                
                # For each user, get their running bots
                for user_id in user_ids:
                    try:
                        user_bots = await get_running_bots_status(user_id)
                        all_running_bots.extend(user_bots)
                    except Exception as e:
                        logger.error(f"[Reconciliation] Error getting running bots for user {user_id}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"[Reconciliation] Error listing running bots: {e}", exc_info=True)
            
            logger.info(f"[Reconciliation] Found {len(all_running_bots)} running bots to check")
            
            # Check each bot's meeting status
            async with async_session_local() as db2:
                for bot_info in all_running_bots:
                    container_id = bot_info.get('container_id')
                    # Try to get meeting_id from labels or from the bot_info dict
                    labels = bot_info.get('labels', {})
                    meeting_id_str = labels.get('vexa.meeting_id') or bot_info.get('meeting_id_from_name')
                    
                    if not meeting_id_str:
                        continue
                    
                    try:
                        meeting_id = int(meeting_id_str)
                    except (ValueError, TypeError):
                        continue
                    
                    meeting = await db2.get(Meeting, meeting_id)
                    if not meeting:
                        # Container has meeting_id label but meeting doesn't exist - kill container
                        logger.warning(f"[Reconciliation] ORPHAN CONTAINER detected: Container {container_id} has meeting_id {meeting_id} but meeting doesn't exist. Killing container...")
                        try:
                            stop_bot_container(container_id)
                            orphan_containers_killed += 1
                            logger.info(f"[Reconciliation] Killed orphan container {container_id}")
                        except Exception as e:
                            logger.error(f"[Reconciliation] Failed to kill orphan container {container_id}: {e}")
                        continue
                    
                    # Check if meeting is in terminal state
                    terminal_states = [MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]
                    if meeting.status in terminal_states:
                        logger.warning(f"[Reconciliation] ORPHAN CONTAINER detected: Container {container_id} is running but meeting {meeting_id} is {meeting.status}. Killing container...")
                        try:
                            stop_bot_container(container_id)
                            orphan_containers_killed += 1
                            logger.info(f"[Reconciliation] Killed orphan container {container_id} for terminal meeting {meeting_id}")
                        except Exception as e:
                            logger.error(f"[Reconciliation] Failed to kill orphan container {container_id}: {e}")
            
            logger.info(f"[Reconciliation] Reconciliation complete: {zombie_meetings_fixed} zombie meetings fixed, {orphan_containers_killed} orphan containers killed")
    except Exception as e:
        logger.error(f"[Reconciliation] Error during reconciliation: {e}", exc_info=True)

# Schedule reconciliation task to run periodically (every 5 minutes)
async def start_reconciliation_scheduler():
    """Start periodic reconciliation task"""
    while True:
        try:
            await asyncio.sleep(300)  # Run every 5 minutes
            await reconcile_meetings_and_containers()
        except Exception as e:
            logger.error(f"[Reconciliation Scheduler] Error: {e}", exc_info=True)
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error

# --- --------------------------------------------------------- ---


# ==================== Voice Agent / Meeting Interaction Endpoints ====================

@app.post("/bots/{platform}/{native_meeting_id}/speak",
          status_code=status.HTTP_202_ACCEPTED,
          summary="Make the bot speak in the meeting",
          description="Accepts text (for TTS) or pre-rendered audio (URL/base64) and plays it through the bot's microphone into the meeting.",
          dependencies=[Depends(get_user_and_token)])
async def bot_speak(
    platform: Platform,
    native_meeting_id: str,
    req: dict,  # Using dict to accept flexible body
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    # Find active meeting
    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    # Build Redis command
    if req.get("text"):
        command = {
            "action": "speak",
            "meeting_id": meeting.id,
            "text": req["text"],
            "provider": req.get("provider", "openai"),
            "voice": req.get("voice", "alloy")
        }
    elif req.get("audio_url") or req.get("audio_base64"):
        command = {
            "action": "speak_audio",
            "meeting_id": meeting.id,
            "audio_url": req.get("audio_url"),
            "audio_base64": req.get("audio_base64"),
            "format": req.get("format", "wav"),
            "sample_rate": req.get("sample_rate", 24000)
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide one of: text, audio_url, or audio_base64"
        )

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps(command))
    logger.info(f"[VoiceAgent] Published speak command to {channel}")

    return {"message": "Speak command sent", "meeting_id": meeting.id}


@app.delete("/bots/{platform}/{native_meeting_id}/speak",
            status_code=status.HTTP_202_ACCEPTED,
            summary="Interrupt bot speech",
            description="Immediately stops any TTS audio currently being played by the bot.",
            dependencies=[Depends(get_user_and_token)])
async def bot_speak_stop(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "speak_stop",
        "meeting_id": meeting.id
    }))

    return {"message": "Speak stop command sent", "meeting_id": meeting.id}


@app.post("/bots/{platform}/{native_meeting_id}/chat",
          status_code=status.HTTP_202_ACCEPTED,
          summary="Send a chat message in the meeting",
          description="Sends a text message to the meeting chat via the bot.",
          dependencies=[Depends(get_user_and_token)])
async def bot_chat_send(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    text = req.get("text")
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text is required")

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "chat_send",
        "meeting_id": meeting.id,
        "text": text
    }))

    return {"message": "Chat message sent", "meeting_id": meeting.id}


@app.get("/bots/{platform}/{native_meeting_id}/chat",
         summary="Get chat messages from the meeting",
         description="Returns all chat messages captured by the bot from the meeting chat.",
         dependencies=[Depends(get_user_and_token)])
async def bot_chat_read(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    # Read chat messages from Redis
    messages_raw = await redis_client.lrange(f"meeting:{meeting.id}:chat_messages", 0, -1)
    messages = []
    for raw in messages_raw:
        try:
            messages.append(json.loads(raw))
        except json.JSONDecodeError:
            pass

    return {"messages": messages, "meeting_id": meeting.id}


@app.post("/bots/{platform}/{native_meeting_id}/screen",
          status_code=status.HTTP_202_ACCEPTED,
          summary="Show content on screen (screen share)",
          description="Displays an image, video, or web page via the bot's screen share. Types: 'image', 'video', 'url', 'html'.",
          dependencies=[Depends(get_user_and_token)])
async def bot_screen_show(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    content_type = req.get("type")
    if content_type not in ("image", "video", "url", "html"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type must be one of: image, video, url, html"
        )

    if content_type == "html" and not req.get("html"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="html content is required for type=html")
    elif content_type != "html" and not req.get("url"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url is required for type=" + content_type)

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "screen_show",
        "meeting_id": meeting.id,
        "type": content_type,
        "url": req.get("url"),
        "html": req.get("html"),
        "start_share": req.get("start_share", True)
    }))

    return {"message": "Screen content command sent", "meeting_id": meeting.id}


@app.delete("/bots/{platform}/{native_meeting_id}/screen",
            status_code=status.HTTP_202_ACCEPTED,
            summary="Stop screen sharing",
            description="Stops screen sharing and clears the displayed content.",
            dependencies=[Depends(get_user_and_token)])
async def bot_screen_stop(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "screen_stop",
        "meeting_id": meeting.id
    }))

    return {"message": "Screen stop command sent", "meeting_id": meeting.id}


# ─── Avatar (Profile Pic) Endpoints ────────────────────────────────

@app.put("/bots/{platform}/{native_meeting_id}/avatar",
         status_code=status.HTTP_202_ACCEPTED,
         summary="Set bot avatar image",
         description="Sets a custom avatar image for the bot's camera feed. Shown when no screen content is active. Provide 'url' (image URL) or 'image_base64' (data URI).",
         dependencies=[Depends(get_user_and_token)])
async def bot_avatar_set(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    image_url = req.get("url")
    image_base64 = req.get("image_base64")
    if not image_url and not image_base64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'url' or 'image_base64' must be provided"
        )

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "avatar_set",
        "meeting_id": meeting.id,
        "url": image_url,
        "image_base64": image_base64
    }))

    return {"message": "Avatar set command sent", "meeting_id": meeting.id}


@app.delete("/bots/{platform}/{native_meeting_id}/avatar",
            status_code=status.HTTP_202_ACCEPTED,
            summary="Reset bot avatar to default",
            description="Resets the bot's avatar to the default Vexa logo.",
            dependencies=[Depends(get_user_and_token)])
async def bot_avatar_reset(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db)
):
    user_token, current_user = auth_data
    platform_value = platform.value

    meeting = await _find_active_meeting(db, current_user.id, platform_value, native_meeting_id)

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "avatar_reset",
        "meeting_id": meeting.id
    }))

    return {"message": "Avatar reset command sent", "meeting_id": meeting.id}


async def _find_active_meeting(
    db: AsyncSession,
    user_id: int,
    platform_value: str,
    native_meeting_id: str
) -> Meeting:
    """Find the latest active meeting for the given platform/native_id combination."""
    stmt = select(Meeting).where(
        Meeting.user_id == user_id,
        Meeting.platform == platform_value,
        Meeting.platform_specific_id == native_meeting_id,
        Meeting.status == MeetingStatus.ACTIVE.value
    ).order_by(desc(Meeting.created_at)).limit(1)

    result = await db.execute(stmt)
    meeting = result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active meeting found for {platform_value}/{native_meeting_id}"
        )

    return meeting


# --- END Voice Agent Endpoints ---


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080, # Default port for bot-manager
        reload=True # Enable reload for development if needed
    ) 
