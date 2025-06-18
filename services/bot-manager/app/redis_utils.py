import redis.asyncio as redis
import logging
import hashlib
import json
# Only import REDIS_URL from config
from config import REDIS_URL
from typing import Optional, Tuple
import re

logger = logging.getLogger(__name__)
redis_client = None

# Use local definitions for TTLs
LOCK_TTL_SECONDS = 60 * 5
MAPPING_TTL_SECONDS = 60 * 60 * 2

async def init_redis():
    """Initializes the Redis client connection."""
    global redis_client
    if redis_client is None:
        try:
            logger.info(f"Connecting to Redis at {REDIS_URL}")
            # Ensure decode_responses=False to handle raw bytes if needed, though strings are fine here
            redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
            await redis_client.ping()
            logger.info("Successfully connected to Redis and pinged.")
        except Exception as e:
            logger.critical(f"Could not connect to Redis: {e}", exc_info=True)
            redis_client = None # Ensure it's None if connection failed
            raise # Reraise the exception to signal failure upstream

async def close_redis():
    """Closes the Redis client connection."""
    global redis_client
    if redis_client:
        logger.info("Closing Redis connection.")
        await redis_client.close()
        redis_client = None

def get_redis_client():
    """Returns the initialized Redis client."""
    if redis_client is None:
        # This should ideally not happen if init_redis is called at startup
        logger.error("Redis client requested before initialization.")
        # Depending on desired behavior, could raise an error or try to init here.
        # For now, return None and let callers handle it.
    return redis_client

# --- Meeting ID and Key Generation ---

def extract_platform_specific_id(platform: str, meeting_url: str) -> Optional[str]:
    """Extracts the platform-specific part of the meeting URL."""
    try:
        # Expect 'google' as the platform identifier
        if platform == "google_meet":
            # https://meet.google.com/abc-def-ghi OR meet.google.com/abc-def-ghi
            match = re.search(r'(?:meet\.google\.com/)?([a-z]{3}-[a-z]{4}-[a-z]{3})', meeting_url)
            if match:
                return match.group(1)
        # Add other platforms here
        # elif platform == "zoom":
        #     # Extract zoom meeting ID logic
        #     pass
        logger.warning(f"Platform '{platform}' URL parsing not implemented or pattern mismatch for URL: {meeting_url}")
        return None
    except Exception as e:
        logger.error(f"Error extracting platform_specific_id for {platform}/{meeting_url}: {e}", exc_info=True)
        return None

def generate_meeting_id(platform: str, platform_specific_id: str, token: str) -> str:
    """Generates a standardized meeting ID."""
    # Basic validation to prevent empty parts
    if not all([platform, platform_specific_id, token]):
        raise ValueError("Platform, platform_specific_id, and token cannot be empty for meeting_id generation.")
    # Format: platform:platform_specific_id:token
    # Ensure no problematic characters are in the components if necessary,
    # but ':' separation should be fine for Redis keys.
    return f"{platform}:{platform_specific_id}:{token}"

def generate_lock_key(meeting_id: str) -> str:
    """Generates the Redis key for the distributed lock."""
    return f"lock:{meeting_id}"

def generate_container_mapping_key(meeting_id: str) -> str:
    """Generates the Redis key for storing the container ID mapping."""
    return f"map:{meeting_id}"

# --- Redis Operations ---

async def acquire_lock(meeting_id: str) -> bool:
    """Acquires a distributed lock for the given meeting ID."""
    global redis_client
    if not redis_client:
        logger.error("Cannot acquire lock, Redis client not initialized")
        return False
    lock_key = generate_lock_key(meeting_id)
    try:
        # SET key value NX PX milliseconds
        # NX -- Only set the key if it does not already exist.
        # PX -- Set the specified expire time, in milliseconds. (Using seconds here via 'ex')
        was_set = await redis_client.set(lock_key, "locked", nx=True, ex=LOCK_TTL_SECONDS)
        if was_set:
            logger.info(f"Acquired lock: {lock_key} for {LOCK_TTL_SECONDS}s")
            return True
        else:
            # Check TTL of existing lock to provide more context
            ttl = await redis_client.ttl(lock_key)
            logger.warning(f"Failed to acquire lock (already held): {lock_key} (TTL: {ttl}s)")
            return False
    except Exception as e:
        logger.error(f"Error acquiring Redis lock for {meeting_id}: {e}", exc_info=True)
        return False

async def release_lock(meeting_id: str):
    """Releases the lock and removes the container mapping for a meeting_id."""
    global redis_client
    if not redis_client:
        logger.error("Cannot release lock/map, Redis client not initialized")
        return

    lock_key = generate_lock_key(meeting_id)
    map_key = generate_container_mapping_key(meeting_id)
    try:
        # Atomically delete both keys using DEL command (variadic)
        deleted_count = await redis_client.delete(lock_key, map_key)

        logger.info(f"Attempted release for meeting_id {meeting_id}. Keys deleted: {deleted_count}")
        if deleted_count == 0:
            logger.warning(f"Neither lock key '{lock_key}' nor map key '{map_key}' found for deletion.")
        elif deleted_count == 1:
            # Check which one might still exist (though delete is idempotent)
            if await redis_client.exists(lock_key):
                logger.warning(f"Released map but lock key '{lock_key}' was not found.")
            elif await redis_client.exists(map_key):
                logger.warning(f"Released lock but map key '{map_key}' was not found.")
            else: # Should not happen if count is 1
                logger.warning(f"Released one key for {meeting_id}, but subsequent existence check found none.")
        else: # deleted_count == 2
            logger.info(f"Successfully released lock '{lock_key}' and mapping '{map_key}'.")

    except Exception as e:
        logger.error(f"Failed to release lock or mapping for {meeting_id}: {e}", exc_info=True)

async def store_container_mapping(meeting_id: str, container_id: str):
    """Stores the mapping from meeting_id to container_id in Redis with TTL."""
    global redis_client
    if not redis_client:
        logger.error("Cannot store mapping, Redis client not initialized")
        return
    map_key = generate_container_mapping_key(meeting_id)
    try:
        await redis_client.set(map_key, container_id, ex=MAPPING_TTL_SECONDS)
        logger.info(f"Stored container mapping: {map_key} -> {container_id} for {MAPPING_TTL_SECONDS}s")
    except Exception as e:
        logger.error(f"Failed to store container mapping for {meeting_id}: {e}", exc_info=True)

async def get_container_id_for_meeting(meeting_id: str) -> Optional[str]:
    """Retrieves the container ID associated with a meeting_id from Redis."""
    global redis_client
    if not redis_client:
        logger.error("Cannot get mapping, Redis client not initialized")
        return None
    map_key = generate_container_mapping_key(meeting_id)
    try:
        container_id = await redis_client.get(map_key)
        if container_id:
            logger.info(f"Retrieved container mapping: {map_key} -> {container_id}")
            return container_id # Already decoded if decode_responses=True
        else:
            # This is not necessarily a warning, could just be expired or stopped
            logger.info(f"No container mapping found for key: {map_key}")
            return None
    except Exception as e:
        logger.error(f"Failed to get container mapping for {meeting_id}: {e}", exc_info=True)
        return None 