"""Process orchestrator implementation.

This module provides an alternative orchestrator that spawns bots as child
Node.js processes instead of Docker containers. Designed for Lite
(all-in-one) deployments where Docker socket access is unavailable.

Activate with ORCHESTRATOR=process environment variable.

The process orchestrator maintains API compatibility with the Docker and Nomad
orchestrators, implementing the same interface:
- start_bot_container() -> spawns a Node.js process
- stop_bot_container() -> terminates the process
- get_running_bots_status() -> lists active processes
- verify_container_running() -> checks if process is alive
"""
from __future__ import annotations

import os
import uuid
import json
import signal
import logging
import asyncio
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List

from app.orchestrators.common import enforce_user_concurrency_limit, count_user_active_bots

logger = logging.getLogger("bot_manager.process_orchestrator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Path to the compiled bot entry point
BOT_SCRIPT_PATH = os.getenv("BOT_SCRIPT_PATH", "/app/vexa-bot/dist/docker.js")

# Working directory for the bot process
BOT_WORKING_DIR = os.getenv("BOT_WORKING_DIR", "/app/vexa-bot")

# WhisperLive URL (direct connection, no Traefik in Lite mode)
WHISPER_LIVE_URL = os.getenv("WHISPER_LIVE_URL", "ws://localhost:9090")

# Redis URL from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Directory for bot process logs
PROCESS_LOGS_DIR = os.getenv("PROCESS_LOGS_DIR", "/var/log/vexa-bots")

# X11 display for headless browser
DISPLAY = os.getenv("DISPLAY", ":99")

# Bot manager callback URL (localhost in Lite mode)
BOT_CALLBACK_BASE_URL = os.getenv("BOT_CALLBACK_BASE_URL", "http://localhost:8080")

# ---------------------------------------------------------------------------
# Process Registry
# ---------------------------------------------------------------------------

# In-memory registry of active bot processes
# Key: process_id (str PID), Value: process metadata dict
_active_processes: Dict[str, Dict[str, Any]] = {}

# Lock for thread-safe access to the registry
_registry_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Compatibility Stubs (Docker-specific concepts not applicable here)
# ---------------------------------------------------------------------------

def get_socket_session(*_args, **_kwargs):
    """Compatibility stub - Docker socket not used in process orchestrator."""
    return None


def close_client():
    """Compatibility stub - no persistent client to close."""
    pass


# Alias for compatibility with existing code
close_docker_client = close_client


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _process_is_alive(proc: subprocess.Popen) -> bool:
    """Check if a subprocess is still running."""
    return proc.poll() is None


def _terminate_process_group(pid: int, timeout: int = 10) -> bool:
    """Terminate a process group gracefully, with force-kill fallback.

    Args:
        pid: Process ID (leader of the process group)
        timeout: Seconds to wait before force-killing

    Returns:
        True if process was terminated, False on error
    """
    try:
        pgid = os.getpgid(pid)

        # Send SIGTERM to process group
        logger.debug(f"Sending SIGTERM to process group {pgid}")
        os.killpg(pgid, signal.SIGTERM)

        # Wait for graceful shutdown
        start_time = datetime.now()
        while (datetime.now() - start_time).seconds < timeout:
            try:
                # Check if process still exists
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                logger.info(f"Process {pid} terminated gracefully")
                return True

        # Force kill if still alive
        logger.warning(f"Process {pid} did not terminate gracefully, sending SIGKILL")
        os.killpg(pgid, signal.SIGKILL)
        return True

    except ProcessLookupError:
        logger.debug(f"Process {pid} already terminated")
        return True
    except PermissionError as e:
        logger.error(f"Permission denied terminating process {pid}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error terminating process {pid}: {e}", exc_info=True)
        return False


async def _cleanup_dead_processes() -> None:
    """Remove dead processes from the registry."""
    async with _registry_lock:
        dead_pids = []
        for pid, info in _active_processes.items():
            proc: subprocess.Popen = info.get("process")
            if proc and not _process_is_alive(proc):
                dead_pids.append(pid)

        for pid in dead_pids:
            logger.debug(f"Cleaning up dead process {pid} from registry")
            del _active_processes[pid]


# ---------------------------------------------------------------------------
# Core Public API
# ---------------------------------------------------------------------------

async def start_bot_container(
    user_id: int,
    meeting_id: int,
    meeting_url: Optional[str],
    platform: str,
    bot_name: Optional[str],
    user_token: str,
    native_meeting_id: str,
    language: Optional[str],
    task: Optional[str]
) -> Optional[Tuple[str, str]]:
    """Start a bot as a Node.js child process.

    This function mimics the Docker orchestrator's start_bot_container but
    spawns a local process instead of a container.

    Args:
        user_id: ID of the user requesting the bot
        meeting_id: Internal database meeting ID
        meeting_url: URL of the meeting to join
        platform: Meeting platform (google_meet, teams, zoom)
        bot_name: Display name for the bot in the meeting
        user_token: API token of the requesting user
        native_meeting_id: Platform-specific meeting identifier
        language: Language code for transcription
        task: Transcription task (transcribe or translate)

    Returns:
        Tuple of (process_id, connection_id) on success, (None, None) on failure
    """
    # Generate unique identifiers
    connection_id = str(uuid.uuid4())
    process_name = f"vexa-bot-{meeting_id}-{uuid.uuid4().hex[:8]}"

    logger.info(
        f"Starting bot process for meeting {meeting_id} "
        f"(platform={platform}, connection_id={connection_id})"
    )

    # Mint MeetingToken (HS256 JWT for bot authentication)
    from app.main import mint_meeting_token
    try:
        meeting_token = mint_meeting_token(
            meeting_id=meeting_id,
            user_id=user_id,
            platform=platform,
            native_meeting_id=native_meeting_id,
            ttl_seconds=7200  # 2 hours
        )
    except Exception as e:
        logger.error(f"Failed to mint MeetingToken for meeting {meeting_id}: {e}", exc_info=True)
        return None, None

    # Build BOT_CONFIG JSON (same structure as Docker orchestrator)
    bot_config = {
        "meeting_id": meeting_id,
        "platform": platform,
        "meetingUrl": meeting_url,
        "botName": bot_name or f"VexaBot-{uuid.uuid4().hex[:6]}",
        "token": meeting_token,
        "nativeMeetingId": native_meeting_id,
        "connectionId": connection_id,
        "language": language,
        "task": task or "transcribe",
        "redisUrl": REDIS_URL,
        "container_name": process_name,
        "automaticLeave": {
            "waitingRoomTimeout": 300000,   # 5 minutes
            "noOneJoinedTimeout": 120000,   # 2 minutes
            "everyoneLeftTimeout": 60000    # 1 minute
        },
        "botManagerCallbackUrl": f"{BOT_CALLBACK_BASE_URL}/bots/internal/callback/exited"
    }

    # Remove None values from config
    bot_config = {k: v for k, v in bot_config.items() if v is not None}

    logger.debug(f"Bot config prepared for {process_name}")

    # Prepare environment for the bot process
    env = os.environ.copy()
    env["BOT_CONFIG"] = json.dumps(bot_config)
    env["WHISPER_LIVE_URL"] = WHISPER_LIVE_URL
    env["DISPLAY"] = DISPLAY
    env["LOG_LEVEL"] = os.getenv("LOG_LEVEL", "INFO")
    # Ensure Node.js can find modules
    env["NODE_PATH"] = os.path.join(BOT_WORKING_DIR, "node_modules")

    # Ensure logs directory exists
    logs_path = Path(PROCESS_LOGS_DIR)
    try:
        logs_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"Could not create logs directory: {e}")

    log_file = logs_path / f"{process_name}.log"

    # Verify bot script exists
    if not Path(BOT_SCRIPT_PATH).exists():
        logger.error(f"Bot script not found at {BOT_SCRIPT_PATH}")
        return None, None

    try:
        # Open log file for the bot process
        log_handle = open(log_file, "w")

        # Spawn the bot process
        proc = subprocess.Popen(
            ["node", BOT_SCRIPT_PATH],
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=BOT_WORKING_DIR,
            preexec_fn=os.setsid  # Create new process group for clean termination
        )

        process_id = str(proc.pid)

        # Register in our tracking dictionary
        async with _registry_lock:
            _active_processes[process_id] = {
                "process": proc,
                "log_handle": log_handle,
                "meeting_id": meeting_id,
                "user_id": user_id,
                "connection_id": connection_id,
                "platform": platform,
                "native_meeting_id": native_meeting_id,
                "process_name": process_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "log_file": str(log_file)
            }

        logger.info(
            f"Successfully started bot process: PID={process_id}, "
            f"meeting={meeting_id}, name={process_name}"
        )

        return process_id, connection_id

    except FileNotFoundError as e:
        logger.error(f"Node.js or bot script not found: {e}")
        return None, None
    except PermissionError as e:
        logger.error(f"Permission denied starting bot process: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error starting bot process: {e}", exc_info=True)
        return None, None


def stop_bot_container(container_id: str) -> bool:
    """Stop a bot process by its PID.

    This function is synchronous to match the Docker orchestrator interface.

    Args:
        container_id: The process ID (PID) as a string

    Returns:
        True if process was stopped or already dead, False on error
    """
    logger.info(f"Stopping bot process {container_id}")

    # Check if process is in our registry
    if container_id not in _active_processes:
        logger.warning(f"Process {container_id} not in registry")
        # Try to terminate anyway if it's a valid PID
        try:
            pid = int(container_id)
            os.kill(pid, 0)  # Check if exists
            return _terminate_process_group(pid)
        except (ValueError, ProcessLookupError, PermissionError):
            logger.info(f"Process {container_id} not found or already stopped")
            return True

    proc_info = _active_processes[container_id]
    proc: subprocess.Popen = proc_info["process"]

    try:
        if _process_is_alive(proc):
            success = _terminate_process_group(proc.pid)
        else:
            logger.info(f"Process {container_id} already stopped")
            success = True

        # Close log file handle
        log_handle = proc_info.get("log_handle")
        if log_handle:
            try:
                log_handle.close()
            except Exception:
                pass

        # Remove from registry (synchronous removal)
        if container_id in _active_processes:
            del _active_processes[container_id]

        return success

    except Exception as e:
        logger.error(f"Error stopping process {container_id}: {e}", exc_info=True)
        return False


async def get_running_bots_status(user_id: int) -> List[Dict[str, Any]]:
    """Get status of running bot processes for a user.

    Args:
        user_id: ID of the user to query

    Returns:
        List of bot status dictionaries matching the Docker orchestrator format
    """
    # Clean up any dead processes first
    await _cleanup_dead_processes()

    result = []

    async with _registry_lock:
        for process_id, info in _active_processes.items():
            # Filter by user
            if info["user_id"] != user_id:
                continue

            proc: subprocess.Popen = info["process"]

            # Verify process is still alive
            if not _process_is_alive(proc):
                continue

            # Build status dict matching Docker orchestrator format
            result.append({
                "container_id": process_id,
                "container_name": info["process_name"],
                "platform": info["platform"],
                "native_meeting_id": info["native_meeting_id"],
                "status": "running",
                "normalized_status": "Up",
                "created_at": info["created_at"],
                "labels": {"vexa.user_id": str(user_id)},
                "meeting_id_from_name": str(info["meeting_id"])
            })

    logger.info(f"Found {len(result)} running bots for user {user_id}")
    return result


async def verify_container_running(container_id: str) -> bool:
    """Verify if a bot process is still running.

    This function checks both the in-memory registry and the actual system process
    to handle cases where the registry is empty (e.g., after container restart).
    
    Supports both PID (numeric string) and container/process name lookup.

    Args:
        container_id: The process ID (PID) as a string, or container/process name

    Returns:
        True if process exists and is running, False otherwise
    """
    # Try to parse as PID first
    is_pid = False
    pid = None
    try:
        pid = int(container_id)
        is_pid = True
    except (ValueError, TypeError):
        # Not a PID - might be a container/process name
        pass

    # First, check if process is in our registry by PID
    if is_pid and container_id in _active_processes:
        proc: subprocess.Popen = _active_processes[container_id]["process"]
        is_running = _process_is_alive(proc)

        if not is_running:
            # Clean up dead process
            logger.debug(f"Process {container_id} is dead, removing from registry")
            async with _registry_lock:
                if container_id in _active_processes:
                    # Close log handle
                    log_handle = _active_processes[container_id].get("log_handle")
                    if log_handle:
                        try:
                            log_handle.close()
                        except Exception:
                            pass
                    del _active_processes[container_id]

        return is_running

    # If not found by PID, try to find by process_name (container name)
    if not is_pid:
        async with _registry_lock:
            for registered_pid, process_info in _active_processes.items():
                if process_info.get("process_name") == container_id:
                    # Found by name - check if the process is alive
                    proc: subprocess.Popen = process_info["process"]
                    is_running = _process_is_alive(proc)
                    
                    if not is_running:
                        # Clean up dead process
                        logger.debug(f"Process {registered_pid} (name: {container_id}) is dead, removing from registry")
                        log_handle = process_info.get("log_handle")
                        if log_handle:
                            try:
                                log_handle.close()
                            except Exception:
                                pass
                        del _active_processes[registered_pid]
                    
                    logger.debug(f"Found process {registered_pid} by name '{container_id}', running: {is_running}")
                    return is_running
        
        # Not found in registry by name - log available processes for debugging
        async with _registry_lock:
            registered_names = [info.get("process_name") for info in _active_processes.values()]
            logger.warning(
                f"Process/container '{container_id}' not found in registry. "
                f"Registered processes: {registered_names}"
            )
        return False

    # Process not in registry but we have a PID - check if it actually exists in the system
    # This handles the case where the container restarted and registry was lost
    # but the process is still running (though this is unlikely for child processes)
    try:
        # os.kill(pid, 0) doesn't kill the process, just checks if it exists
        # Raises ProcessLookupError if process doesn't exist
        # Raises PermissionError if we don't have permission (but process exists)
        os.kill(pid, 0)
        # Process exists - but it's not in our registry
        # This could happen if:
        # 1. Container restarted and registry was lost (but process survived - unlikely)
        # 2. Process was started outside our orchestrator
        # For reconciliation purposes, if the process exists, we consider it running
        logger.info(f"Process {container_id} exists in system but not in registry (possible container restart)")
        return True
    except ProcessLookupError:
        # Process doesn't exist
        logger.debug(f"Process {container_id} not found in system")
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        # This means the process is running
        logger.debug(f"Process {container_id} exists but permission denied (process is running)")
        return True
    except Exception as e:
        # Other errors - log and assume not running to be safe
        logger.warning(f"Error checking process {container_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Session Recording (shared with other orchestrators)
# ---------------------------------------------------------------------------

# Import the shared session recording function from orchestrator_utils
# This creates a MeetingSession entry in the database
from app.orchestrator_utils import _record_session_start  # noqa: E402


# ---------------------------------------------------------------------------
# Module Exports
# ---------------------------------------------------------------------------

__all__ = [
    "get_socket_session",
    "close_docker_client",
    "start_bot_container",
    "stop_bot_container",
    "_record_session_start",
    "get_running_bots_status",
    "verify_container_running",
]
