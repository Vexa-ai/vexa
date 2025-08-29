import requests_unixsocket
import requests  # Make sure standard requests is imported
import logging
import json
import uuid
import os
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import asyncio
from contextlib import asynccontextmanager
import aiodocker
# from app.auth import get_current_user_ws # This function does not exist
from app.config import REDIS_URL # Import from the single source of truth
from requests.exceptions import RequestException, Timeout, ConnectionError

# Explicitly import the exceptions from requests
from requests.exceptions import RequestException, ConnectionError, HTTPError

# Import the Platform class from shared models
from shared_models.schemas import Platform

# ---> ADD Missing imports for _record_session_start
from shared_models.database import async_session_local
from shared_models.models import MeetingSession, Meeting
# <--- END ADD

# ---> ADD Missing imports for check logic & session start
from fastapi import HTTPException # For raising limit error
from app.database.service import TranscriptionService # To get user limit
from sqlalchemy.future import select
from shared_models.models import User, MeetingSession
# <--- END ADD

# Assuming these are still needed from config or env
DOCKER_HOST = os.environ.get("DOCKER_HOST", "unix://var/run/docker.sock")
DOCKER_NETWORK = os.environ.get("DOCKER_NETWORK", "vexa_default")
BOT_IMAGE_NAME = os.environ.get("BOT_IMAGE_NAME", "vexa-bot:dev")

# For example, use 'cuda' for NVIDIA GPUs or 'cpu' for CPU
DEVICE_TYPE = os.environ.get("DEVICE_TYPE", "cuda").lower()

logger = logging.getLogger("bot_manager.orchestrator_utils")

# Global session for requests_unixsocket
unix_socket_session = None

# Define a local exception
class DockerConnectionError(Exception):
    pass

def get_socket_session(max_retries=3, delay=2):
    """Initializes and returns a requests_unixsocket session with retries."""
    global unix_socket_session
    if unix_socket_session is None:
        logger.info(f"Attempting to initialize requests_unixsocket session for {DOCKER_HOST}...")
        retries = 0
        # Extract socket path correctly AND ensure it's absolute
        socket_path_relative = DOCKER_HOST.split('//', 1)[1]
        socket_path_abs = f"/{socket_path_relative}" # Prepend slash for absolute path

        # URL encode path separately using the absolute path
        # The http+unix scheme requires the encoded absolute path
        socket_path_encoded = socket_path_abs.replace("/", "%2F")
        socket_url = f'http+unix://{socket_path_encoded}'

        while retries < max_retries:
            try:
                # Check socket file exists before attempting connection using the absolute path
                logger.debug(f"Checking for socket file at absolute path: {socket_path_abs}") # Added debug log
                if not os.path.exists(socket_path_abs):
                     # Ensure the error message shows the absolute path being checked
                     raise FileNotFoundError(f"Docker socket file not found at: {socket_path_abs}")

                logger.debug(f"Attempt {retries+1}/{max_retries}: Creating session.")
                temp_session = requests_unixsocket.Session()

                # Test connection by getting Docker version via the correctly formed URL
                logger.debug(f"Attempt {retries+1}/{max_retries}: Getting Docker version via {socket_url}/version")
                response = temp_session.get(f'{socket_url}/version')
                response.raise_for_status() # Raise HTTPError for bad responses
                version_data = response.json()
                api_version = version_data.get('ApiVersion')
                logger.info(f"requests_unixsocket session initialized. Docker API version: {api_version}")
                unix_socket_session = temp_session # Assign only on success
                return unix_socket_session

            except FileNotFoundError as e:
                 # Log the actual exception message which now includes the absolute path
                 logger.warning(f"Attempt {retries+1}/{max_retries}: {e}. Retrying in {delay}s...")
            except ConnectionError as e:
                 logger.warning(f"Attempt {retries+1}/{max_retries}: Socket connection error ({e}). Is Docker running? Retrying in {delay}s...")
            except HTTPError as e:
                logger.error(f"Attempt {retries+1}/{max_retries}: HTTP error communicating with Docker socket: {e}", exc_info=True)
                 # Don't retry on HTTP errors like 4xx/5xx immediately, might be persistent issue
                break
            except Exception as e:
                logger.error(f"Attempt {retries+1}/{max_retries}: Failed to initialize requests_unixsocket session: {e}", exc_info=True)

            retries += 1
            if retries < max_retries:
                time.sleep(delay)
            else:
                logger.error(f"Failed to connect to Docker socket at {DOCKER_HOST} after {max_retries} attempts.")
                unix_socket_session = None
                raise DockerConnectionError(f"Could not connect to Docker socket after {max_retries} attempts.")

    return unix_socket_session

def close_docker_client(): # Keep name for compatibility in main.py
    """Closes the requests_unixsocket session."""
    global unix_socket_session
    if unix_socket_session:
        logger.info("Closing requests_unixsocket session.")
        try:
            unix_socket_session.close()
        except Exception as e:
            logger.warning(f"Error closing requests_unixsocket session: {e}")
        unix_socket_session = None

# Helper async function to record session start
async def _record_session_start(meeting_id: int, session_uid: str):
    try:
        async with async_session_local() as db_session:
            new_session = MeetingSession(
                meeting_id=meeting_id,
                session_uid=session_uid, 
                session_start_time=datetime.now(timezone.utc) # Record timestamp
            )
            db_session.add(new_session)
            await db_session.commit()
            logger.info(f"Recorded start for session {session_uid} for meeting {meeting_id}")
    except Exception as db_err:
        logger.error(f"Failed to record session start for session {session_uid}, meeting {meeting_id}: {db_err}", exc_info=True)
        # Log error but allow the main function to continue

# Make the function async
async def start_bot_container(
    user_id: int,
    meeting_id: int,
    meeting_url: Optional[str],
    platform: str, # External name (e.g., google_meet)
    bot_name: Optional[str],
    user_token: str,
    native_meeting_id: str,
    language: Optional[str],
    task: Optional[str]
) -> Optional[tuple[str, str]]:
    """
    Starts a vexa-bot container via requests_unixsocket AFTER checking user limit.

    Args:
        user_id: The ID of the user requesting the bot.
        meeting_id: Internal database ID of the meeting.
        meeting_url: The URL for the bot to join.
        platform: The meeting platform (external name).
        bot_name: An optional name for the bot inside the meeting.
        user_token: The API token of the user requesting the bot.
        native_meeting_id: The platform-specific meeting ID (e.g., 'xyz-abc-pdq').
        language: Optional language code for transcription.
        task: Optional transcription task ('transcribe' or 'translate').
        
    Returns:
        A tuple (container_id, connection_id) if successful, None otherwise.
    """
    # === START: Bot Limit Check ===
    try:
        # Fetch user details (including max_concurrent_bots)
        user = await TranscriptionService.get_or_create_user(user_id)
        if not user:
             logger.error(f"User with ID {user_id} not found...")
             raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

        # Count currently running bots for this user using labels via Docker API Socket
        session = get_socket_session() # Get the existing session
        if not session:
             logger.error("[Limit Check] Cannot count running bots, requests_unixsocket session not available.")
             raise HTTPException(status_code=500, detail="Failed to connect to Docker to verify bot count.")
             
        try:
            # Construct filters for Docker API
            filters = json.dumps({
                "label": [f"vexa.user_id={user_id}"],
                "status": ["running"]
            })
            
            # Make request to list containers endpoint
            socket_path_relative = DOCKER_HOST.split('//', 1)[1]
            socket_path_abs = f"/{socket_path_relative}"
            socket_path_encoded = socket_path_abs.replace("/", "%2F")
            socket_url_base = f'http+unix://{socket_path_encoded}'
            list_url = f'{socket_url_base}/containers/json'
            
            logger.debug(f"[Limit Check] Querying {list_url} with filters: {filters}")
            response = session.get(list_url, params={"filters": filters, "all": "false"})
            response.raise_for_status() # Check for HTTP errors
            
            running_bots_info = response.json()
            current_bot_count = len(running_bots_info)
            logger.debug(f"[Limit Check] Found {current_bot_count} running bot containers for user {user_id} via socket API")

        except RequestException as sock_err:
            logger.error(f"[Limit Check] Failed to count running bots via socket API for user {user_id}: {sock_err}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to verify current bot count via Docker socket.")
        except Exception as count_err: # Catch other potential errors like JSONDecodeError
            logger.error(f"[Limit Check] Unexpected error counting running bots via socket API for user {user_id}: {count_err}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to process bot count verification.")

        # Check against the user's limit (logic remains the same)
        user_limit = user.max_concurrent_bots
        logger.info(f"Checking bot limit for user {user_id}: Found {current_bot_count} running bots, limit is {user_limit}")

        if not hasattr(user, 'max_concurrent_bots') or user_limit is None:
             logger.error(f"User {user_id} is missing the max_concurrent_bots attribute...")
             raise HTTPException(status_code=500, detail="User configuration error: Bot limit not set.")

        if current_bot_count >= user_limit:
            logger.warning(f"User {user_id} reached bot limit ({user_limit})...")
            raise HTTPException(
                status_code=403,
                detail=f"User has reached the maximum concurrent bot limit ({user_limit})."
            )
        logger.info(f"User {user_id} is under bot limit ({current_bot_count}/{user_limit}). Proceeding...")

    except HTTPException as http_exc:
         raise http_exc
    except Exception as e:
         logger.error(f"Error during bot limit check for user {user_id}: {e}", exc_info=True)
         raise HTTPException(status_code=500, detail="Failed to verify bot limit.")
    # === END: Bot Limit Check ===

    # --- Original start_bot_container logic (using requests_unixsocket) --- 
    session = get_socket_session()
    if not session:
        logger.error("Cannot start bot container, requests_unixsocket session not available.")
        return None, None

    container_name = f"vexa-bot-{meeting_id}-{uuid.uuid4().hex[:8]}"
    if not bot_name:
        bot_name = f"VexaBot-{uuid.uuid4().hex[:6]}"
    connection_id = str(uuid.uuid4())
    logger.info(f"Generated unique connectionId for bot session: {connection_id}")

    # Construct BOT_CONFIG JSON - Include new fields
    bot_config_data = {
        "meeting_id": meeting_id,
        "platform": platform,
        "meetingUrl": meeting_url,
        "botName": bot_name,
        "token": user_token,
        "nativeMeetingId": native_meeting_id,
        "connectionId": connection_id,
        "language": language,
        "task": task,
        "redisUrl": REDIS_URL,
        "container_name": container_name,  # ADDED: Container name for identification
        "automaticLeave": {
            "waitingRoomTimeout": 300000,
            "noOneJoinedTimeout": 120000,
            "everyoneLeftTimeout": 60000
        },
        "botManagerCallbackUrl": f"http://bot-manager:8080/bots/internal/callback/exited"
    }
    # Remove keys with None values before serializing
    cleaned_config_data = {k: v for k, v in bot_config_data.items() if v is not None}
    bot_config_json = json.dumps(cleaned_config_data)

    logger.debug(f"Bot config: {bot_config_json}") # Log the full config

    # Get the WhisperLive URL from bot-manager's own environment.
    # This is set in docker-compose.yml to ws://whisperlive.internal/ws to go through Traefik.
    whisper_live_url_for_bot = os.getenv('WHISPER_LIVE_URL')

    if not whisper_live_url_for_bot:
        # This should ideally not happen if docker-compose.yml is correctly configured.
        logger.error("CRITICAL: WHISPER_LIVE_URL is not set in bot-manager's environment. Falling back to default, but this should be fixed in docker-compose.yml for bot-manager service.")
        whisper_live_url_for_bot = 'ws://whisperlive.internal/ws' # Fallback, but log an error.

    logger.info(f"Passing WHISPER_LIVE_URL to bot: {whisper_live_url_for_bot}")

    # These are the environment variables passed to the Node.js process  of the vexa-bot started by your entrypoint.sh.
    environment = [
        f"BOT_CONFIG={bot_config_json}",
        f"WHISPER_LIVE_URL={whisper_live_url_for_bot}", # Use the URL from bot-manager's env
        f"LOG_LEVEL={os.getenv('LOG_LEVEL', 'INFO').upper()}",
    ]

    # Ensure absolute path for URL encoding here as well
    socket_path_relative = DOCKER_HOST.split('//', 1)[1]
    socket_path_abs = f"/{socket_path_relative}"
    socket_path_encoded = socket_path_abs.replace("/", "%2F")
    socket_url_base = f'http+unix://{socket_path_encoded}'

    # Docker API payload for creating a container
    create_payload = {
        "Image": BOT_IMAGE_NAME,
        "Env": environment,
        "Labels": {"vexa.user_id": str(user_id)}, # *** ADDED Label ***
        "HostConfig": {
            "NetworkMode": DOCKER_NETWORK,
            "AutoRemove": True
        },
    }

    create_url = f'{socket_url_base}/containers/create?name={container_name}'
    start_url_template = f'{socket_url_base}/containers/{{}}/start'

    container_id = None # Initialize container_id
    try:
        logger.info(f"Attempting to create bot container '{container_name}' ({BOT_IMAGE_NAME}) via socket ({socket_url_base})...")
        response = session.post(create_url, json=create_payload)
        response.raise_for_status()
        container_info = response.json()
        container_id = container_info.get('Id')

        if not container_id:
            logger.error(f"Failed to create container: No ID in response: {container_info}")
            return None, None

        logger.info(f"Container {container_id} created. Starting...")

        start_url = start_url_template.format(container_id)
        response = session.post(start_url)

        if response.status_code != 204:
            logger.error(f"Failed to start container {container_id}. Status: {response.status_code}, Response: {response.text}")
            # Consider removing the created container if start fails?
            return None, None

        logger.info(f"Successfully started container {container_id} for meeting: {meeting_id}")
        
        # *** REMOVED Session Recording Call - To be handled by caller ***
        # try:
        #     asyncio.run(_record_session_start(meeting_id, connection_id))
        # except RuntimeError as e:
        #     logger.error(f"Error running async session recording: {e}. Session start NOT recorded.")

        return container_id, connection_id # Return both values

    except RequestException as e:
        logger.error(f"HTTP error communicating with Docker socket: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error starting container via socket: {e}", exc_info=True)

    # Clean up created container if start failed or exception occurred before returning container_id
    # This requires careful handling to avoid race conditions if another process is managing it.
    # For now, relying on AutoRemove=True might be sufficient if start fails cleanly.
    # If an exception happens between create and start success logging, container might linger.

    return None, None # Return None for both if error occurs

def stop_bot_container(container_id: str) -> bool:
    """Stops a container using its ID via requests_unixsocket."""
    session = get_socket_session()
    if not session:
        logger.error(f"Cannot stop container {container_id}, requests_unixsocket session not available.")
        return False

    # Ensure absolute path for URL encoding here as well
    socket_path_relative = DOCKER_HOST.split('//', 1)[1]
    socket_path_abs = f"/{socket_path_relative}"
    socket_path_encoded = socket_path_abs.replace("/", "%2F")
    socket_url_base = f'http+unix://{socket_path_encoded}'
    
    stop_url = f'{socket_url_base}/containers/{container_id}/stop'
    # Since AutoRemove=True, we don't need a separate remove call

    try:
        logger.info(f"Attempting to stop container {container_id} via socket ({stop_url})...") # Log stop URL
        # Send POST request to stop the container. Docker waits for it to stop.
        # Timeout can be added via query param `t` (e.g., ?t=10 for 10 seconds)
        response = session.post(f"{stop_url}?t=10") 
        
        # Check status code: 204 No Content (success), 304 Not Modified (already stopped), 404 Not Found
        if response.status_code == 204:
            logger.info(f"Successfully sent stop command to container {container_id}.")
            return True
        elif response.status_code == 304:
            logger.warning(f"Container {container_id} was already stopped.")
            return True
        elif response.status_code == 404:
            logger.warning(f"Container {container_id} not found, assuming already stopped/removed.")
            return True 
        else:
            # Raise exception for other errors (like 500)
            logger.error(f"Error stopping container {container_id}. Status: {response.status_code}, Body: {response.text}")
            response.raise_for_status()
            return False # Should not be reached if raise_for_status() works

    except RequestException as e:
        # Handle 404 specifically if raise_for_status() doesn't catch it as expected
        if hasattr(e, 'response') and e.response is not None and e.response.status_code == 404:
            logger.warning(f"Container {container_id} not found (exception check), assuming already stopped/removed.")
            return True
        logger.error(f"HTTP error stopping container {container_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error stopping container {container_id}: {e}", exc_info=True)
        return False 

# --- ADDED: Get Running Bot Status --- 
# Make the function async
async def get_running_bots_status(user_id: int) -> List[Dict[str, Any]]:
    """Gets status of RUNNING bot containers for a user using labels via socket API, including DB lookup for meeting details."""
    session = get_socket_session()
    if not session:
        logger.error("[Bot Status] Cannot get status, requests_unixsocket session not available.")
        return [] 
        
    bots_status = []
    running_containers = [] # Initialize
    try:
        # Construct filters for Docker API
        filters = json.dumps({
            "label": [f"vexa.user_id={user_id}"],
            "status": ["running"]
        })
        
        # Make request to list containers endpoint
        socket_path_relative = DOCKER_HOST.split('//', 1)[1]
        socket_path_abs = f"/{socket_path_relative}"
        socket_path_encoded = socket_path_abs.replace("/", "%2F")
        socket_url_base = f'http+unix://{socket_path_encoded}'
        list_url = f'{socket_url_base}/containers/json'
        
        logger.debug(f"[Bot Status] Querying {list_url} with filters: {filters}")
        response = session.get(list_url, params={"filters": filters, "all": "false"})
        response.raise_for_status()
        
        running_containers = response.json()
        logger.info(f"[Bot Status] Found {len(running_containers)} running containers for user {user_id}")

    except RequestException as sock_err:
        logger.error(f"[Bot Status] Failed to list containers via socket API for user {user_id}: {sock_err}", exc_info=True)
        return [] # Return empty on error listing containers
    except Exception as e:
        logger.error(f"[Bot Status] Unexpected error listing containers for user {user_id}: {e}", exc_info=True)
        return []
        
    # Perform DB lookups asynchronously for each container
    async with async_session_local() as db_session:
        for container_info in running_containers:
            platform = None
            native_meeting_id = None
            meeting_id_int = None
            
            container_id = container_info.get('Id')
            name = container_info.get('Names', ['N/A'])[0].lstrip('/')
            created_at_unix = container_info.get('Created')
            created_at = datetime.fromtimestamp(created_at_unix, timezone.utc).isoformat() if created_at_unix else None
            status = container_info.get('Status')
            labels = container_info.get('Labels', {})
            
            # Parse meeting_id from name: vexa-bot-{meeting_id}-{uuid}
            meeting_id_from_name = "unknown"
            try:
                 parts = name.split('-')
                 if len(parts) > 2 and parts[0] == 'vexa' and parts[1] == 'bot':
                      meeting_id_from_name = parts[2]
                      # Try converting to int for DB lookup
                      meeting_id_int = int(meeting_id_from_name)
            except (ValueError, IndexError, Exception) as parse_err:
                 logger.warning(f"[Bot Status] Could not parse meeting ID from container name '{name}': {parse_err}")
                 meeting_id_int = None # Ensure it's None if parsing fails
            
            # If we have a valid meeting ID, query the DB
            if meeting_id_int is not None:
                try:
                    meeting = await db_session.get(Meeting, meeting_id_int)
                    if meeting:
                        platform = meeting.platform
                        native_meeting_id = meeting.platform_specific_id
                        logger.debug(f"[Bot Status] Found DB details for meeting {meeting_id_int}: platform={platform}, native_id={native_meeting_id}")
                    else:
                        logger.warning(f"[Bot Status] No meeting found in DB for ID {meeting_id_int} parsed from container '{name}'")
                except Exception as db_err:
                    logger.error(f"[Bot Status] DB error fetching meeting {meeting_id_int}: {db_err}", exc_info=True)
            
            bots_status.append({
                "container_id": container_id,
                "container_name": name,
                "platform": platform, # Added
                "native_meeting_id": native_meeting_id, # Added
                "status": status,
                "created_at": created_at,
                "labels": labels,
                "meeting_id_from_name": meeting_id_from_name
            })
            
    return bots_status
# --- END: Get Running Bot Status --- 

async def verify_container_running(container_id: str) -> bool:
    """Verify if a container exists and is running via the Docker socket API."""
    session = get_socket_session() # Assumes get_socket_session() is defined in this file
    if not session:
        logger.error(f"[Verify Container] Cannot verify container {container_id}, requests_unixsocket session not available.")
        return False # Or raise an exception, depending on desired error handling

    # Construct the correct base URL for socket requests
    # This logic should mirror how other Docker API calls are made in this file
    # For example, if DOCKER_HOST is 'unix:///var/run/docker.sock'
    socket_path_relative = DOCKER_HOST.split('//', 1)[1] 
    socket_path_abs = f"/{socket_path_relative}"
    socket_path_encoded = socket_path_abs.replace("/", "%2F")
    socket_url_base = f'http+unix://{socket_path_encoded}'
    
    inspect_url = f'{socket_url_base}/containers/{container_id}/json'
    
    try:
        logger.debug(f"[Verify Container] Inspecting container {container_id} via URL: {inspect_url}")
        # Make the request asynchronously. Requires session to be an AIOHTTP client or similar.
        # If get_socket_session() returns a synchronous requests.Session, this needs to run in a thread.
        # Assuming for now that session can handle async requests or this will be wrapped.
        # For a synchronous session, it would be:
        # response = await asyncio.to_thread(session.get, inspect_url)
        
        # If get_socket_session() returns a regular requests.Session:
        import asyncio
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, session.get, inspect_url)

        if response.status_code == 404:
            logger.info(f"[Verify Container] Container {container_id} not found (404).")
            return False
        
        response.raise_for_status() # Raise an exception for other bad status codes (500, etc.)
        
        container_info = response.json()
        is_running = container_info.get('State', {}).get('Running', False)
        logger.info(f"[Verify Container] Container {container_id} found. Running: {is_running}")
        return is_running
        
    except requests.exceptions.RequestException as e: # Catching requests-specific exceptions
        if hasattr(e, 'response') and e.response is not None and e.response.status_code == 404:
             logger.warning(f"[Verify Container] Container {container_id} not found during request (exception check).")
             return False
        logger.error(f"[Verify Container] HTTP error inspecting container {container_id}: {e}", exc_info=True)
        return False # Treat HTTP errors (other than 404) as "not verifiable" or "not running"
    except Exception as e:
        logger.error(f"[Verify Container] Unexpected error inspecting container {container_id}: {e}", exc_info=True)
        return False # Treat other errors as "not verifiable" or "not running"