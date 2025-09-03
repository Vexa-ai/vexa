import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, status
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

# Local imports - Remove unused ones
# from app.database.models import init_db # Using local init_db now
# from app.database.service import TranscriptionService # Not used here
# from app.tasks.monitoring import celery_app # Not used here

from .config import BOT_IMAGE_NAME, REDIS_URL
from app.orchestrators import (
    get_socket_session, close_docker_client, start_bot_container,
    stop_bot_container, _record_session_start, get_running_bots_status,
)
from shared_models.database import init_db, get_db, async_session_local
from shared_models.models import User, Meeting, MeetingSession, Transcription # <--- ADD MeetingSession and Transcription import
from shared_models.schemas import MeetingCreate, MeetingResponse, Platform, BotStatusResponse, MeetingConfigUpdate # Import new schemas and Platform
from app.auth import get_user_and_token # MODIFIED
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, desc, func
from datetime import datetime # For start_time

from app.tasks.bot_exit_tasks import run_all_tasks

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

# Pydantic models - Use schemas from shared_models
# class BotRequest(BaseModel): ... -> Replaced by MeetingCreate
# class BotResponse(BaseModel): ... -> Replaced by MeetingResponse

# --- REMOVED: Local MeetingConfigUpdate class - now using shared_models.schemas.MeetingConfigUpdate ---

# --- ADDED: Pydantic Model for Bot Exit Callback ---
class BotExitCallbackPayload(BaseModel):
    connection_id: str = Field(..., description="The connectionId (session_uid) of the exiting bot.")
    exit_code: int = Field(..., description="The exit code of the bot process (0 for success, 1 for UI leave failure).")
    reason: Optional[str] = Field("self_initiated_leave", description="Reason for the exit.")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Detailed error information including stack trace, error message, and context.")
    platform_specific_error: Optional[str] = Field(None, description="Platform-specific error message or details.")

class BotStartupCallbackPayload(BaseModel):
    connection_id: str = Field(..., description="The connection ID of the bot session.")
    container_id: str = Field(..., description="The container ID of the started bot.")
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
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping() # Verify connection
        logger.info("Successfully connected to Redis.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis on startup: {e}", exc_info=True)
        redis_client = None # Ensure client is None if connection fails
    # --------------------------------------

    logger.info("Database, Docker Client (attempted), and Redis Client (attempted) initialized.")

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
async def _delayed_container_stop(container_id: str, delay_seconds: int = 30):
    """Waits for a delay, then attempts to stop the container synchronously in a thread."""
    logger.info(f"[Delayed Stop] Task started for container {container_id}. Waiting {delay_seconds}s before stopping.")
    await asyncio.sleep(delay_seconds)
    logger.info(f"[Delayed Stop] Delay finished for {container_id}. Attempting synchronous stop...")
    try:
        # Run the synchronous stop_bot_container in a separate thread
        # to avoid blocking the async event loop.
        await asyncio.to_thread(stop_bot_container, container_id)
        logger.info(f"[Delayed Stop] Successfully stopped container {container_id}.")
    except Exception as e:
        logger.error(f"[Delayed Stop] Error stopping container {container_id}: {e}", exc_info=True)
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

    constructed_url = Platform.construct_meeting_url(req.platform.value, native_meeting_id)
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
        Meeting.status.in_(['requested', 'active']) # Do NOT block on 'stopping' to allow immediate new bot
    ).order_by(desc(Meeting.created_at)).limit(1) # Get the latest one if multiple somehow exist
    
    result = await db.execute(existing_meeting_stmt)
    existing_meeting = result.scalars().first()

    if existing_meeting:
        logger.info(f"Found existing meeting record {existing_meeting.id} with status '{existing_meeting.status}' for user {current_user.id}, platform '{req.platform.value}', native ID '{native_meeting_id}'.")
        # Enforce DB-only uniqueness: if there's any requested/active meeting, reject immediately.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active or requested meeting already exists for this platform and meeting ID. Meeting ID: {existing_meeting.id}"
        )
    
    # --- Fast-fail concurrency limit check (DB-based) ---
    user_limit = int(getattr(current_user, "max_concurrent_bots", 0) or 0)
    if user_limit > 0:
        count_stmt = select(func.count()).select_from(Meeting).where(
            and_(
                Meeting.user_id == current_user.id,
                Meeting.status.in_(['requested', 'active'])
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
        new_meeting = Meeting(
            user_id=current_user.id,
            platform=req.platform.value,
            platform_specific_id=native_meeting_id,
            status='requested',
            # Ensure other necessary fields like created_at are handled by the model or explicitly set
        )
        db.add(new_meeting)
        await db.commit()
        await db.refresh(new_meeting)
        meeting_id_for_bot = new_meeting.id # Use this for the bot
        logger.info(f"Created new meeting record with ID: {meeting_id_for_bot}")
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
            current_meeting_for_bot_launch.status = 'error'
            await db.commit()
        except Exception as _:
            pass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid inputs: {', '.join(invalid_fields)}"
        )

    # 4. Start the bot container
    container_id = None
    connection_id = None
    try:
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
            task=req.task
        )
        logger.info(f"Call to start_bot_container completed. Container ID: {container_id}, Connection ID: {connection_id}")

        if not container_id or not connection_id:
            error_msg = "Failed to start bot container."
            if not container_id: error_msg += " Container ID not returned."
            if not connection_id: error_msg += " Connection ID not generated/returned."
            logger.error(f"{error_msg} for meeting {meeting_id}")
            
            current_meeting_for_bot_launch.status = 'error'
            await db.commit()
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
        return MeetingResponse.from_orm(current_meeting_for_bot_launch)

    except HTTPException as http_exc:
        logger.warning(f"HTTPException occurred during bot startup for meeting {meeting_id}: {http_exc.status_code} - {http_exc.detail}")
        try:
            # Fetch again or use current_meeting_for_bot_launch if it's the correct one to update
            meeting_to_update = await db.get(Meeting, meeting_id) # Re-fetch to be safe with session state
            if meeting_to_update and meeting_to_update.status not in ['error', 'failed', 'completed']: 
                 logger.warning(f"Updating meeting {meeting_id} status to 'error' due to HTTPException {http_exc.status_code}.")
                 meeting_to_update.status = 'error'
                 if container_id: 
                     meeting_to_update.bot_container_id = container_id
                 await db.commit()
            elif not meeting_to_update:
                logger.error(f"Could not find meeting {meeting_id} to update status to error after HTTPException.")
        except Exception as db_err:
             logger.error(f"Failed to update meeting {meeting_id} status to error after HTTPException: {db_err}")
        raise http_exc

    except Exception as e:
        logger.error(f"Unexpected exception occurred during bot startup process for meeting {meeting_id} (after DB creation): {e}", exc_info=True)
        try:
            meeting_to_update = await db.get(Meeting, meeting_id) # Re-fetch
            if meeting_to_update and meeting_to_update.status not in ['error', 'failed', 'completed']:
                 logger.warning(f"Updating meeting {meeting_id} status to 'error' due to unexpected exception.")
                 meeting_to_update.status = 'error'
                 if container_id:
                     meeting_to_update.bot_container_id = container_id
                 await db.commit()
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
        Meeting.status == 'active' # Must be active to reconfigure
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

    # 2. Find the LATEST session_uid (connectionId) for this meeting - CHANGED TO EARLIEST
    # latest_session_stmt = select(MeetingSession.session_uid).where(
    #     MeetingSession.meeting_id == internal_meeting_id
    # ).order_by(MeetingSession.session_start_time.desc()).limit(1)
    # --- Get the EARLIEST session for this meeting ID --- 
    earliest_session_stmt = select(MeetingSession.session_uid).where(
        MeetingSession.meeting_id == internal_meeting_id
    ).order_by(MeetingSession.session_start_time.asc()).limit(1) # Order ASC, take first

    session_result = await db.execute(earliest_session_stmt)
    # Rename variable for clarity
    original_session_uid = session_result.scalars().first() 

    # ++ ADDED: Log the specific session UID found (changed var name) ++
    logger.info(f"[DEBUG] Found earliest session UID (should be original connectionId) '{original_session_uid}' for meeting {internal_meeting_id}")
    # +++++++++++++++++++++++++++++++++++++++++++++

    if not original_session_uid:
        logger.error(f"Active meeting {internal_meeting_id} found, but no associated session UID in MeetingSession table. Cannot send command.")
        # This indicates an inconsistent state
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Meeting is active but session information is missing. Cannot process reconfiguration."
        )

    # logger.info(f"Found latest session UID {latest_session_uid} for meeting {internal_meeting_id}.") # Removed old log

    # 3. Construct and Publish command
    if not redis_client:
        logger.error("Redis client not available. Cannot publish reconfigure command.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot connect to internal messaging service to send command."
        )

    command_payload = {
        "action": "reconfigure",
        "uid": original_session_uid, # Use the original UID in the payload (for the bot handler, if needed? Seems unused there now)
        "language": req.language,
        "task": req.task
    }
    # Publish to the channel the bot SUBSCRIBED to (using original UID)
    channel = f"bot_commands:{original_session_uid}"

    try:
        payload_str = json.dumps(command_payload)
        logger.info(f"Publishing command to channel '{channel}': {payload_str}")
        await redis_client.publish(channel, payload_str)
        logger.info(f"Successfully published reconfigure command for session {original_session_uid}.") # Log original UID
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
             summary="Request stop for a running bot",
             description="Sends a 'leave' command to the bot via Redis and schedules a delayed container stop. Returns 202 Accepted immediately.",
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
    1. Finds the latest active meeting record.
    2. Finds the earliest session UID (original connection ID) associated with that meeting.
    3. Publishes a 'leave' command to the bot via Redis Pub/Sub.
    4. Schedules a background task to stop the Docker container after a delay.
    5. Updates the meeting status to 'stopping' (or keeps 'active' until confirmed).
    6. Returns 202 Accepted.
    """
    user_token, current_user = auth_data
    platform_value = platform.value

    logger.info(f"Received stop request for {platform_value}/{native_meeting_id} from user {current_user.id}")

    # 1. Find the latest meeting in a stoppable state (pre-active or active)
    #    Allow stopping at pre-active states too: 'requested' | 'active' | 'stopping'
    stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform_value,
        Meeting.platform_specific_id == native_meeting_id,
        Meeting.status.in_(['requested', 'active', 'stopping'])
    ).order_by(desc(Meeting.created_at))

    result = await db.execute(stmt)
    meeting = result.scalars().first()

    if not meeting:
        logger.warning(f"Stop request: No meeting in stoppable state for {platform_value}/{native_meeting_id} for user {current_user.id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No meeting found to stop.")

    # Idempotency: already stopping → accept
    if meeting.status == 'stopping':
        logger.info(f"Stop request: Meeting {meeting.id} already in 'stopping'. Returning 202 idempotently.")
        return {"message": "Stop already in progress."}

    if not meeting.bot_container_id:
         logger.info(f"Stop request: Meeting {meeting.id} has no container ID. Finalizing immediately.")
         meeting.status = 'completed'
         meeting.end_time = datetime.utcnow()
         await db.commit()
         # Schedule post-meeting tasks even if it never became active
         logger.info(f"Scheduling post-meeting tasks for meeting {meeting.id} (no container case).")
         background_tasks.add_task(run_all_tasks, meeting.id)
         return {"message": "Stop request accepted; meeting finalized (no running container)."}

    logger.info(f"Found active meeting {meeting.id} with container {meeting.bot_container_id} for stop request.")

    # 2. Find the earliest session UID for this meeting (may not exist yet at pre-active)
    session_stmt = select(MeetingSession.session_uid).where(
        MeetingSession.meeting_id == meeting.id
    ).order_by(MeetingSession.session_start_time.asc()) # Order by start time ascending

    session_result = await db.execute(session_stmt)
    earliest_session_uid = session_result.scalars().first()

    if not earliest_session_uid:
        logger.warning(f"Stop request: No session UID for meeting {meeting.id} (pre-active). Skipping leave command.")

    logger.info(f"Found earliest session UID '{earliest_session_uid}' for meeting {meeting.id}. Preparing to send leave command.")

    # 3. Publish 'leave' command via Redis Pub/Sub
    if not redis_client:
        logger.error("Redis client not available. Cannot send leave command.")
        # Proceed with delayed stop, but log the failure to command the bot.
        # Don't raise an error here, as we still want to stop the container eventually.
    else:
        try:
            command_channel = f"bot_commands:{earliest_session_uid}"
            payload = json.dumps({"action": "leave"})
            logger.info(f"Publishing leave command to Redis channel '{command_channel}': {payload}")
            await redis_client.publish(command_channel, payload)
            logger.info(f"Successfully published leave command for session {earliest_session_uid}.")
        except Exception as e:
            logger.error(f"Failed to publish leave command to Redis channel {command_channel}: {e}", exc_info=True)
            # Log error but continue with delayed stop

    # 4. Schedule delayed container stop task
    logger.info(f"Scheduling delayed stop task for container {meeting.bot_container_id} (meeting {meeting.id}).")
    # Pass container_id and delay
    background_tasks.add_task(_delayed_container_stop, meeting.bot_container_id, 30) 

    # 5. Update Meeting status (Consider 'stopping' or keep 'active')
    # Option A: Keep 'active' - relies on collector/other process to detect actual stop
    # Option B: Change to 'stopping' - indicates intent
    # Let's use 'stopping' for now to show intent.
    logger.info(f"Updating meeting {meeting.id} status to 'stopping'.")
    meeting.status = 'stopping'
    # Optionally clear container ID here or when stop is confirmed?
    # meeting.bot_container_id = None 
    # Don't set end_time here, let the stop confirmation (or lack thereof) handle it.
    await db.commit()
    logger.info(f"Meeting {meeting.id} status updated.")

    # 6. Return 202 Accepted
    logger.info(f"Stop request for meeting {meeting.id} accepted. Leave command sent, delayed stop scheduled.")
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
        if exit_code == 0:
            meeting.status = 'completed'
            logger.info(f"Bot exit callback: Meeting {meeting_id} status updated to 'completed'.")
        else:
            meeting.status = 'failed'
            logger.warning(f"Bot exit callback: Meeting {meeting_id} status updated to 'failed' due to exit_code {exit_code}.")
            
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

        # ALWAYS schedule post-meeting tasks, regardless of exit code
        logger.info(f"Bot exit callback: Scheduling post-meeting tasks for meeting {meeting.id}.")
        background_tasks.add_task(run_all_tasks, meeting.id)

        # If the bot exited with an error, it might not have cleaned itself up.
        # Schedule a delayed stop as a safeguard.
        if exit_code != 0 and meeting.bot_container_id:
            logger.warning(f"Bot exit callback: Scheduling delayed stop for container {meeting.bot_container_id} of failed meeting {meeting.id}.")
            background_tasks.add_task(_delayed_container_stop, meeting.bot_container_id, delay_seconds=10)

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

        # Update meeting status to active and set start time
        if meeting.status in ['requested', 'failed', 'error']:
            old_status = meeting.status
            meeting.status = 'active'
            meeting.bot_container_id = container_id
            meeting.start_time = datetime.utcnow()
            logger.info(f"Bot startup callback: Meeting {meeting_id} status updated from '{old_status}' to 'active' with container {container_id}.")
        elif meeting.status == 'active':
            # Container restarted but meeting was already active - just update container ID
            meeting.bot_container_id = container_id
            logger.info(f"Bot startup callback: Meeting {meeting_id} already active, updated container ID to {container_id}.")
        else:
            logger.warning(f"Bot startup callback: Meeting {meeting_id} has unexpected status '{meeting.status}', not updating.")
            return {"status": "warning", "detail": f"Meeting status '{meeting.status}' not updated"}

        await db.commit()
        await db.refresh(meeting)
        logger.info(f"Bot startup callback: Meeting {meeting.id} successfully updated in DB.")

        return {"status": "startup processed", "meeting_id": meeting.id, "status": meeting.status}

    except Exception as e:
        logger.error(f"Bot startup callback: An unexpected error occurred: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while processing the bot startup callback."
        )

# --- --------------------------------------------------------- ---

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080, # Default port for bot-manager
        reload=True # Enable reload for development if needed
    ) 