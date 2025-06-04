import logging
import asyncio
import redis.asyncio as aioredis
import redis # For redis.exceptions
from typing import Dict, Any # For message_data type hint if being very specific

from config import (
    REDIS_STREAM_NAME,
    REDIS_CONSUMER_GROUP,
    CONSUMER_NAME,
    PENDING_MSG_TIMEOUT_MS,
    REDIS_STREAM_READ_COUNT,
    REDIS_STREAM_BLOCK_MS,
    REDIS_SPEAKER_EVENTS_STREAM_NAME,
    REDIS_SPEAKER_EVENTS_CONSUMER_GROUP
)
from streaming.processors import process_stream_message, process_speaker_event_message

logger = logging.getLogger(__name__)

async def claim_stale_messages(redis_c: aioredis.Redis):
    """Claims and processes stale messages from the Redis Stream for the current consumer."""
    messages_claimed_total = 0
    processed_claim_count = 0
    acked_claim_count = 0
    error_claim_count = 0

    logger.info(f"Starting stale message check (consumer: {CONSUMER_NAME}, idle > {PENDING_MSG_TIMEOUT_MS}ms).")

    try:
        while True:
            pending_details = await redis_c.xpending_range(
                name=REDIS_STREAM_NAME,
                groupname=REDIS_CONSUMER_GROUP,
                min='-',
                max='+',
                count=100 
            )

            if not pending_details:
                logger.debug("No more pending messages found for the group during stale check.")
                break

            stale_candidates = [
                msg for msg in pending_details
                if msg.get('idle', 0) > PENDING_MSG_TIMEOUT_MS
            ]

            if not stale_candidates:
                logger.debug("No messages found exceeding idle time in the current pending batch.")
                if len(pending_details) < 100:
                    break 
                else:
                    logger.debug("Checked 100 pending messages, none were stale enough. More might exist but stopping check for this run to avoid long loops on very busy streams.")
                    break
            
            stale_message_ids = [msg['message_id'] for msg in stale_candidates]
            logger.info(f"Found {len(stale_message_ids)} potentially stale message(s) to claim: {stale_message_ids}")

            if stale_message_ids:
                claimed_messages = await redis_c.xclaim(
                    name=REDIS_STREAM_NAME,
                    groupname=REDIS_CONSUMER_GROUP,
                    consumername=CONSUMER_NAME,
                    min_idle_time=PENDING_MSG_TIMEOUT_MS, 
                    message_ids=stale_message_ids,
                )
                
                messages_claimed_now = len(claimed_messages)
                messages_claimed_total += messages_claimed_now
                if messages_claimed_now > 0:
                    logger.info(f"Successfully claimed {messages_claimed_now} stale message(s): {[msg[0].decode('utf-8') for msg in claimed_messages]}")

                for message_id_bytes, message_data_bytes in claimed_messages:
                    message_id_str = message_id_bytes.decode('utf-8') if isinstance(message_id_bytes, bytes) else message_id_bytes
                    message_data_decoded: Dict[str, Any] = {}
                    if isinstance(message_data_bytes, dict):
                        # Already decoded
                        message_data_decoded = {k: v for k, v in message_data_bytes.items()}
                    else:
                        # Need to decode
                        message_data_decoded = {k.decode('utf-8'): v.decode('utf-8') for k, v in message_data_bytes.items()}
                    
                    logger.info(f"Processing claimed stale message {message_id_str}...")
                    processed_claim_count += 1
                    try:
                        success = await process_stream_message(message_id_str, message_data_decoded, redis_c)
                        if success:
                            logger.info(f"Successfully processed claimed stale message {message_id_str}. Acknowledging.")
                            await redis_c.xack(REDIS_STREAM_NAME, REDIS_CONSUMER_GROUP, message_id_str)
                            acked_claim_count += 1
                        else:
                            logger.warning(f"Processing failed for claimed stale message {message_id_str}. Not acknowledging.")
                            error_claim_count += 1
                    except Exception as e:
                        logger.error(f"Error processing claimed stale message {message_id_str}: {e}", exc_info=True)
                        error_claim_count += 1
            
            if not stale_candidates or len(pending_details) < 100: # Break if no stale candidates or if we didn't get a full batch of pending messages
                break

    except redis.exceptions.RedisError as e:
        logger.error(f"Redis error during stale message claiming: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error during stale message claiming: {e}", exc_info=True)

    logger.info(f"Stale message check finished. Total claimed: {messages_claimed_total}, Processed: {processed_claim_count}, Acked: {acked_claim_count}, Errors: {error_claim_count}")

async def consume_redis_stream(redis_c: aioredis.Redis):
    """Background task to consume transcription segments from Redis Stream."""
    last_processed_id = '>' 
    logger.info(f"Starting main consumer loop for '{CONSUMER_NAME}', reading new messages ('>')...")

    while True:
        try:
            response = await redis_c.xreadgroup(
                groupname=REDIS_CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={REDIS_STREAM_NAME: last_processed_id},
                count=REDIS_STREAM_READ_COUNT,
                block=REDIS_STREAM_BLOCK_MS 
            )

            if not response:
                continue

            for stream_name_bytes, messages in response:
                # stream_name = stream_name_bytes.decode('utf-8') # Not strictly needed if only one stream
                message_ids_to_ack = []
                processed_count = 0
                
                for message_id_bytes, message_data_bytes in messages:
                    message_id_str = message_id_bytes.decode('utf-8') if isinstance(message_id_bytes, bytes) else message_id_bytes
                    message_data_decoded: Dict[str, Any] = {}
                    if isinstance(message_data_bytes, dict):
                        # Already decoded
                        message_data_decoded = {k: v for k, v in message_data_bytes.items()}
                    else:
                        # Need to decode
                        message_data_decoded = {k.decode('utf-8'): v.decode('utf-8') for k, v in message_data_bytes.items()}
                    
                    should_ack = False
                    processed_count += 1
                    try:
                        should_ack = await process_stream_message(message_id_str, message_data_decoded, redis_c)
                    except Exception as e:
                        logger.error(f"Critical error during process_stream_message call for {message_id_str}: {e}", exc_info=True)
                        should_ack = False
                    if should_ack:
                        message_ids_to_ack.append(message_id_str)
                        
                if message_ids_to_ack:
                    try:
                        await redis_c.xack(REDIS_STREAM_NAME, REDIS_CONSUMER_GROUP, *message_ids_to_ack)
                        logger.debug(f"Acknowledged {len(message_ids_to_ack)}/{processed_count} messages: {message_ids_to_ack}")
                    except Exception as e:
                        logger.error(f"Failed to acknowledge messages {message_ids_to_ack}: {e}", exc_info=True)
        
        except asyncio.CancelledError:
            logger.info("Redis Stream consumer task cancelled.")
            break
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis connection error in stream consumer: {e}. Retrying after delay...", exc_info=True)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unhandled error in Redis Stream consumer loop: {e}", exc_info=True)
            await asyncio.sleep(5) 

async def consume_speaker_events_stream(redis_c: aioredis.Redis):
    """Background task to consume speaker events from Redis Stream."""
    # Note: Using CONSUMER_NAME + '-speaker' to differentiate if needed, or could be shared if logic allows.
    # Stale message claiming for this stream is not implemented here, but could be added similarly to claim_stale_messages.
    consumer_name_speaker = f"{CONSUMER_NAME}-speaker"
    last_processed_id = '>' 
    logger.info(f"Starting speaker event consumer loop for '{consumer_name_speaker}', reading new messages ('>')...")

    while True:
        try:
            response = await redis_c.xreadgroup(
                groupname=REDIS_SPEAKER_EVENTS_CONSUMER_GROUP,
                consumername=consumer_name_speaker,
                streams={REDIS_SPEAKER_EVENTS_STREAM_NAME: last_processed_id},
                count=REDIS_STREAM_READ_COUNT, # Can use the same count or a specific one
                block=REDIS_STREAM_BLOCK_MS    # Can use the same block time or a specific one
            )

            if not response:
                continue

            for stream_name_bytes, messages in response:
                message_ids_to_ack = []
                processed_count = 0
                
                for message_id_bytes, message_data_bytes in messages:
                    message_id_str = message_id_bytes.decode('utf-8') if isinstance(message_id_bytes, bytes) else message_id_bytes
                    # Speaker event messages are expected to be flat JSON strings in the 'payload' field from WhisperLive
                    # However, WhisperLive sends them as top-level fields. We need to adapt.
                    # The `message_data_bytes` from xreadgroup for speaker_events stream will directly contain the speaker event fields.
                    message_data_decoded: Dict[str, Any] = {}
                    if isinstance(message_data_bytes, dict):
                        message_data_decoded = {k.decode('utf-8') if isinstance(k, bytes) else k: 
                                                v.decode('utf-8') if isinstance(v, bytes) else v 
                                                for k, v in message_data_bytes.items()}
                    else:
                        logger.error(f"[SpeakerConsumer] Unexpected message_data_bytes format for {message_id_str}: {type(message_data_bytes)}")
                        continue # Skip this message

                    should_ack = False
                    processed_count += 1
                    try:
                        # Pass the already decoded dictionary directly
                        should_ack = await process_speaker_event_message(message_id_str, message_data_decoded, redis_c)
                    except Exception as e:
                        logger.error(f"[SpeakerConsumer] Critical error during process_speaker_event_message call for {message_id_str}: {e}", exc_info=True)
                        should_ack = False # Ensure it's false on error
                    
                    if should_ack:
                        message_ids_to_ack.append(message_id_str)
                        
                if message_ids_to_ack:
                    try:
                        await redis_c.xack(REDIS_SPEAKER_EVENTS_STREAM_NAME, REDIS_SPEAKER_EVENTS_CONSUMER_GROUP, *message_ids_to_ack)
                        logger.debug(f"[SpeakerConsumer] Acknowledged {len(message_ids_to_ack)}/{processed_count} speaker event messages: {message_ids_to_ack}")
                    except Exception as e:
                        logger.error(f"[SpeakerConsumer] Failed to acknowledge speaker event messages {message_ids_to_ack}: {e}", exc_info=True)
        
        except asyncio.CancelledError:
            logger.info("[SpeakerConsumer] Speaker Events Redis Stream consumer task cancelled.")
            break
        except redis.exceptions.ConnectionError as e:
            logger.error(f"[SpeakerConsumer] Redis connection error in speaker event stream consumer: {e}. Retrying after delay...", exc_info=True)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"[SpeakerConsumer] Unhandled error in Speaker Events Redis Stream consumer loop: {e}", exc_info=True)
            await asyncio.sleep(5) 