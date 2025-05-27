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

# Local imports - Remove unused ones
# from app.database.models import init_db # Using local init_db now
# from app.database.service import TranscriptionService # Not used here
# from app.tasks.monitoring import celery_app # Not used here

from config import BOT_IMAGE_NAME, REDIS_URL
from docker_utils import get_socket_session, close_docker_client, start_bot_container, stop_bot_container, _record_session_start, get_running_bots_status
from shared_models.database import init_db, get_db, async_session_local
from shared_models.models import User, Meeting, MeetingSession # <--- ADD MeetingSession import
from shared_models.schemas import MeetingCreate, MeetingResponse, Platform, BotStatusResponse # Import new schemas and Platform
from auth import get_user_and_token # Import the new dependency
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, desc
from datetime import datetime # For start_time

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

# --- ADD Pydantic Model for Config Update ---
class MeetingConfigUpdate(BaseModel):
    language: Optional[str] = Field(None, description="New language code (e.g., 'en', 'es')")
    task: Optional[str] = Field(None, description="New task ('transcribe' or 'translate')")
# -------------------------------------------

# --- ADDED: Pydantic Model for Bot Exit Callback ---
class BotExitCallbackPayload(BaseModel):
    connection_id: str = Field(..., description="The connectionId (session_uid) of the exiting bot.")
    exit_code: int = Field(..., description="The exit code of the bot process (0 for success, 1 for UI leave failure).")
    reason: Optional[str] = Field("self_initiated_leave", description="Reason for the exit.")
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
          dependencies=[Depends(get_user_and_token)])
async def request_bot(
    req: MeetingCreate,
    auth_data: tuple[str, User] = Depends(get_user_and_token),
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
    # Unpack the token and user from the dependency result
    user_token, current_user = auth_data

    logger.info(f"Received bot request for platform '{req.platform.value}' with native ID '{req.native_meeting_id}' from user {current_user.id}")
    native_meeting_id = req.native_meeting_id # Store native_meeting_id for clarity

    # 1. Construct meeting URL
    constructed_url = Platform.construct_meeting_url(req.platform.value, native_meeting_id)
    if not constructed_url:
        # Handle cases where URL construction isn't possible (e.g., Teams, invalid ID format)
        # Depending on policy, either reject or proceed without a URL for the bot if it can handle it
        logger.warning(f"Could not construct meeting URL for platform {req.platform.value} and ID {native_meeting_id}. Proceeding without URL for bot.")
        # Or raise HTTPException: 
        # raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not construct URL for platform {req.platform.value} with ID {native_meeting_id}. Invalid ID or unsupported construction.")

    # 2. Check for existing active meeting for this user/platform/native_id
    existing_meeting_stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == req.platform.value,
        Meeting.platform_specific_id == native_meeting_id,
        Meeting.status.in_(['requested', 'active'])
    )
    result = await db.execute(existing_meeting_stmt)
    existing_meeting = result.scalars().first()

    if existing_meeting:
        logger.warning(f"User {current_user.id} requested duplicate bot for active/requested meeting {existing_meeting.id} ({req.platform.value} / {native_meeting_id})")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active or requested meeting already exists for this platform and meeting ID. Meeting ID: {existing_meeting.id}"
        )

    # 3. Create Meeting record in DB
    new_meeting = Meeting(
        user_id=current_user.id,
        platform=req.platform.value,
        platform_specific_id=native_meeting_id, # Correct field name
        status='requested'
    )
    db.add(new_meeting)
    await db.commit()
    await db.refresh(new_meeting)
    meeting_id = new_meeting.id # Internal DB ID
    logger.info(f"Created meeting record with ID: {meeting_id}")

    # 4. Start the bot container
    container_id = None
    connection_id = None # Initialize connection_id
    try:
        logger.info(f"Attempting to start bot container for meeting {meeting_id} (native: {native_meeting_id})...")
        # MODIFY the call to start_bot_container:
        # Unpack both container_id and connection_id
        container_id, connection_id = await start_bot_container(
            user_id=current_user.id,         # Pass user_id
            meeting_id=meeting_id,           # Internal DB ID
            meeting_url=constructed_url,     # Constructed URL (still pass it to bot if needed)
            platform=req.platform.value,     # Platform string
            bot_name=req.bot_name,
            user_token=user_token,           # Pass the user's API token
            native_meeting_id=native_meeting_id, # Pass the native meeting ID
            language=req.language,           # Pass language
            task=req.task                    # Pass task
        )
        logger.info(f"Call to start_bot_container completed. Container ID: {container_id}, Connection ID: {connection_id}") # Log both IDs

        if not container_id or not connection_id:
            # Log specific error based on which ID is missing
            error_msg = "Failed to start bot container."
            if not container_id:
                error_msg += " Container ID not returned."
            if not connection_id:
                error_msg += " Connection ID not generated/returned."
            logger.error(f"{error_msg} for meeting {meeting_id}")
            
            # Update status immediately if start failed
            new_meeting.status = 'error'
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"status": "error", "message": error_msg, "meeting_id": meeting_id}
            )

        # *** Schedule session start recording AFTER successful container start ***
        asyncio.create_task(_record_session_start(meeting_id, connection_id))
        logger.info(f"Scheduled background task to record session start for meeting {meeting_id}, session {connection_id}")

        # 5. Update Meeting record with container details and status
        logger.info(f"Attempting to update meeting {meeting_id} status to active with container ID {container_id}...") # Log before update
        new_meeting.bot_container_id = container_id
        new_meeting.status = 'active'
        new_meeting.start_time = datetime.utcnow()
        await db.commit()
        await db.refresh(new_meeting)
        logger.info(f"Successfully updated meeting {meeting_id} status.") # Log after update

        logger.info(f"Successfully started bot container {container_id} for meeting {meeting_id}")
        return MeetingResponse.from_orm(new_meeting)

    except HTTPException as http_exc:
        # If the exception was already an HTTPException (like our 403 limit error), re-raise it directly.
        logger.warning(f"HTTPException occurred during bot startup for meeting {meeting_id}: {http_exc.status_code} - {http_exc.detail}")
        # Attempt to update status to error for specific cases if needed, or just re-raise
        try:
            # Fetch again in case session state is lost or object is detached
            meeting_to_update = await db.get(Meeting, meeting_id)
            if meeting_to_update and meeting_to_update.status != 'error': # Avoid redundant updates
                 logger.warning(f"Updating meeting {meeting_id} status to 'error' due to HTTPException {http_exc.status_code}.")
                 meeting_to_update.status = 'error'
                 # Assign container ID even if update failed later, helps debugging
                 if container_id: # If container was somehow created before error
                     meeting_to_update.bot_container_id = container_id
                 await db.commit()
            elif not meeting_to_update:
                logger.error(f"Could not find meeting {meeting_id} to update status to error after HTTPException.")
        except Exception as db_err:
             logger.error(f"Failed to update meeting {meeting_id} status to error after HTTPException: {db_err}")
        raise http_exc # Re-raise the original HTTPException (e.g., the 403)

    except Exception as e:
        # Catch any other unexpected errors as 500
        # Enhanced logging in the exception handler
        logger.error(f"Unexpected exception occurred during bot startup process for meeting {meeting_id} (after DB creation): {e}", exc_info=True)
        # Attempt to update status to error even if container start failed or subsequent update failed
        try:
            # Fetch again in case session state is lost or object is detached
            meeting_to_update = await db.get(Meeting, meeting_id)
            if meeting_to_update and meeting_to_update.status != 'error': # Avoid redundant updates
                 logger.warning(f"Updating meeting {meeting_id} status to 'error' due to unexpected exception.")
                 meeting_to_update.status = 'error'
                 # Assign container ID even if update failed later, helps debugging
                 if container_id:
                     meeting_to_update.bot_container_id = container_id
                 await db.commit()
            elif not meeting_to_update:
                logger.error(f"Could not find meeting {meeting_id} to update status to error after unexpected exception.")
        except Exception as db_err:
             logger.error(f"Failed to update meeting {meeting_id} status to error after unexpected exception: {db_err}")

        # Raise a generic 500 error for unexpected issues
        # Re-raise HTTPException to send appropriate response to client
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

    # 1. Find the *latest* active meeting for this user/platform/native_id
    #    (Similar logic as in PUT /config)
    stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform_value,
        Meeting.platform_specific_id == native_meeting_id,
        Meeting.status == 'active' # Only target active meetings
    ).order_by(desc(Meeting.created_at))

    result = await db.execute(stmt)
    meeting = result.scalars().first()

    if not meeting:
        logger.warning(f"Stop request failed: No active meeting found for {platform_value}/{native_meeting_id} for user {current_user.id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active meeting not found.")

    if not meeting.bot_container_id:
         logger.warning(f"Stop request failed: Active meeting {meeting.id} found, but has no associated container ID.")
         # Update status to error? Or just report failure?
         meeting.status = 'error' # Mark as error if container ID is missing
         await db.commit()
         raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Meeting found but has no associated container.")

    logger.info(f"Found active meeting {meeting.id} with container {meeting.bot_container_id} for stop request.")

    # 2. Find the *earliest* session UID for this meeting
    session_stmt = select(MeetingSession.session_uid).where(
        MeetingSession.meeting_id == meeting.id
    ).order_by(MeetingSession.session_start_time.asc()) # Order by start time ascending

    session_result = await db.execute(session_stmt)
    earliest_session_uid = session_result.scalars().first()

    if not earliest_session_uid:
        logger.error(f"Stop request failed: Could not find any session UID for meeting {meeting.id}. Cannot send leave command.")
        # This is an inconsistent state. Mark meeting as error?
        meeting.status = 'error'
        await db.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal state error: Meeting session UID not found.")

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
        # Call the function from docker_utils - ADD AWAIT HERE
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
    db: AsyncSession = Depends(get_db)
):
    logger.info(f"Received bot exit callback: connection_id={payload.connection_id}, exit_code={payload.exit_code}, reason={payload.reason}")

    try:
        # 1. Find the MeetingSession using the connection_id (session_uid)
        session_stmt = select(MeetingSession).where(MeetingSession.session_uid == payload.connection_id)
        session_result = await db.execute(session_stmt)
        meeting_session = session_result.scalars().first()

        if not meeting_session:
            logger.warning(f"Bot exit callback: No MeetingSession found for connection_id/session_uid: {payload.connection_id}")
            # Return 404 as we can't identify the meeting this bot belonged to.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting session not found for the provided connection_id.")

        meeting_id = meeting_session.meeting_id
        logger.info(f"Bot exit callback: Found meeting_id {meeting_id} for connection_id {payload.connection_id}")

        # 2. Find the Meeting record
        meeting = await db.get(Meeting, meeting_id)
        if not meeting:
            logger.error(f"Bot exit callback: MeetingSession {meeting_session.id} (uid: {payload.connection_id}) exists, but corresponding Meeting {meeting_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting record not found, though session exists.")

        # 3. Update Meeting status based on exit_code
        # Avoid updating if already in a terminal state like 'completed', 'error' (unless we want to overwrite error specifically)
        if meeting.status not in ['completed', 'failed']:
            if payload.exit_code == 0:
                meeting.status = 'completed'
                logger.info(f"Bot exit callback: Meeting {meeting_id} status updated to 'completed'.")
            else: # exit_code == 1 (or other non-zero)
                # If bot couldn't click leave button, it's still a form of completion, but with a UI issue.
                # We could use a more specific status, e.g., 'completed_with_ui_error' if defined,
                # or just 'completed' but log the detail. For now, let's use 'failed' to indicate an issue.
                meeting.status = 'failed' # Or 'completed_with_error'
                logger.info(f"Bot exit callback: Meeting {meeting_id} status updated to 'failed' due to exit_code {payload.exit_code}.")
            
            meeting.end_time = datetime.utcnow()
            await db.commit()
            await db.refresh(meeting)
            logger.info(f"Bot exit callback: Meeting {meeting_id} successfully updated.")
        else:
            logger.info(f"Bot exit callback: Meeting {meeting_id} already in a terminal state ('{meeting.status}'). No status update performed.")

        return {"message": "Callback received and processed."}

    except HTTPException as http_exc: # Re-raise HTTPExceptions directly
        raise http_exc
    except Exception as e:
        logger.error(f"Error processing bot exit callback for connection_id {payload.connection_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error processing bot exit callback.")
# --- --------------------------------------------------------- ---

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080, # Default port for bot-manager
        reload=True # Enable reload for development if needed
    ) 