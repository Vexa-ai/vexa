import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Depends, Header
import logging
import asyncio
from datetime import datetime, timezone
import redis
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from shared_models.database import get_db, init_db
from shared_models.models import Meeting
from filters import TranscriptionFilter
from config import (
    REDIS_STREAM_NAME,
    REDIS_CONSUMER_GROUP,
    REDIS_STREAM_READ_COUNT,
    REDIS_STREAM_BLOCK_MS,
    CONSUMER_NAME,
    PENDING_MSG_TIMEOUT_MS,
    BACKGROUND_TASK_INTERVAL,
    IMMUTABILITY_THRESHOLD,
    LOG_LEVEL,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_SPEAKER_EVENTS_STREAM_NAME,
    REDIS_SPEAKER_EVENTS_CONSUMER_GROUP
)
from api.endpoints import router as api_router
from streaming.consumer import claim_stale_messages, consume_redis_stream, consume_speaker_events_stream
from background.db_writer import process_redis_to_postgres

app = FastAPI(
    title="Transcription Collector",
    description="Collects and stores transcriptions from WhisperLive instances via Redis Streams."
)
app.include_router(api_router)

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("transcription_collector")

# Redis connection
redis_client: Optional[aioredis.Redis] = None

# Initialize transcription filter
transcription_filter = TranscriptionFilter()

# Background task references
redis_to_pg_task = None
stream_consumer_task = None
speaker_stream_consumer_task = None

@app.on_event("startup")
async def startup():
    global redis_client, redis_to_pg_task, stream_consumer_task, speaker_stream_consumer_task, transcription_filter
    
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    temp_redis_client = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=0,
        decode_responses=True
    )
    await temp_redis_client.ping()
    redis_client = temp_redis_client
    app.state.redis_client = redis_client
    logger.info("Redis connection successful.")
    
    try:
        logger.info(f"Ensuring Redis Stream group '{REDIS_CONSUMER_GROUP}' exists for stream '{REDIS_STREAM_NAME}'...")
        await redis_client.xgroup_create(
            name=REDIS_STREAM_NAME, 
            groupname=REDIS_CONSUMER_GROUP, 
            id='0',
            mkstream=True
        )
        logger.info(f"Consumer group '{REDIS_CONSUMER_GROUP}' ensured for stream '{REDIS_STREAM_NAME}'.")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP Consumer Group name already exists" in str(e):
            logger.info(f"Consumer group '{REDIS_CONSUMER_GROUP}' already exists for stream '{REDIS_STREAM_NAME}'.")
        else:
            logger.error(f"Failed to create Redis consumer group: {e}", exc_info=True)
            return
    
    # Ensure speaker events stream and consumer group exist
    try:
        logger.info(f"Ensuring Redis Stream group '{REDIS_SPEAKER_EVENTS_CONSUMER_GROUP}' exists for stream '{REDIS_SPEAKER_EVENTS_STREAM_NAME}'...")
        await redis_client.xgroup_create(
            name=REDIS_SPEAKER_EVENTS_STREAM_NAME, 
            groupname=REDIS_SPEAKER_EVENTS_CONSUMER_GROUP, 
            id='0',
            mkstream=True
        )
        logger.info(f"Consumer group '{REDIS_SPEAKER_EVENTS_CONSUMER_GROUP}' ensured for stream '{REDIS_SPEAKER_EVENTS_STREAM_NAME}'.")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP Consumer Group name already exists" in str(e):
            logger.info(f"Consumer group '{REDIS_SPEAKER_EVENTS_CONSUMER_GROUP}' already exists for stream '{REDIS_SPEAKER_EVENTS_STREAM_NAME}'.")
        else:
            logger.error(f"Failed to create Redis consumer group for speaker events: {e}", exc_info=True)
            return
    
    logger.info("Database initialized.")
    
    await claim_stale_messages(redis_client)
    
    redis_to_pg_task = asyncio.create_task(process_redis_to_postgres(redis_client, transcription_filter))
    logger.info(f"Redis-to-PostgreSQL task started (Interval: {BACKGROUND_TASK_INTERVAL}s, Threshold: {IMMUTABILITY_THRESHOLD}s)")
    
    stream_consumer_task = asyncio.create_task(consume_redis_stream(redis_client))
    logger.info(f"Redis Stream consumer task started (Stream: {REDIS_STREAM_NAME}, Group: {REDIS_CONSUMER_GROUP}, Consumer: {CONSUMER_NAME})")

    # Start speaker events consumer task
    speaker_stream_consumer_task = asyncio.create_task(consume_speaker_events_stream(redis_client))
    logger.info(f"Speaker Events Redis Stream consumer task started (Stream: {REDIS_SPEAKER_EVENTS_STREAM_NAME}, Group: {REDIS_SPEAKER_EVENTS_CONSUMER_GROUP}, Consumer: {CONSUMER_NAME + '-speaker'})")

@app.on_event("shutdown")
async def shutdown():
    logger.info("Application shutting down...")
    # Cancel background tasks
    tasks_to_cancel = [redis_to_pg_task, stream_consumer_task, speaker_stream_consumer_task]
    for i, task in enumerate(tasks_to_cancel):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Background task {i+1} cancelled.")
            except Exception as e:
                logger.error(f"Error during background task {i+1} cancellation: {e}", exc_info=True)
    
    # Close Redis connection
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed.")
    
    logger.info("Shutdown complete.")

if __name__ == "__main__":
    # Removed uvicorn runner, rely on Docker CMD
    pass 