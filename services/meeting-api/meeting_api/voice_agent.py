"""/speak, /chat, /screen, /avatar, /events endpoints.

Voice agent control — sends Redis pub/sub commands to the bot container.
All endpoint paths and Redis channels are frozen.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .schemas import Platform, MeetingStatus

from .auth import get_user_and_token
from .meetings import _find_active_meeting, _find_meeting_any_status, get_redis

logger = logging.getLogger("meeting_api.voice_agent")

router = APIRouter()


# ---------------------------------------------------------------------------
# Speak
# ---------------------------------------------------------------------------

@router.post(
    "/bots/{platform}/{native_meeting_id}/speak",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Make the bot speak in the meeting",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_speak(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)

    if req.get("text"):
        command = {
            "action": "speak",
            "meeting_id": meeting.id,
            "text": req["text"],
            "provider": req.get("provider", "openai"),
            "voice": req.get("voice", "alloy"),
        }
    elif req.get("audio_url") or req.get("audio_base64"):
        command = {
            "action": "speak_audio",
            "meeting_id": meeting.id,
            "audio_url": req.get("audio_url"),
            "audio_base64": req.get("audio_base64"),
            "format": req.get("format", "wav"),
            "sample_rate": req.get("sample_rate", 24000),
        }
    else:
        raise HTTPException(status_code=400, detail="Must provide one of: text, audio_url, or audio_base64")

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps(command))
    return {"message": "Speak command sent", "meeting_id": meeting.id}


@router.delete(
    "/bots/{platform}/{native_meeting_id}/speak",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Interrupt bot speech",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_speak_stop(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)
    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({"action": "speak_stop", "meeting_id": meeting.id}))
    return {"message": "Speak stop command sent", "meeting_id": meeting.id}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@router.post(
    "/bots/{platform}/{native_meeting_id}/chat",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a chat message in the meeting",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_chat_send(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)
    text = req.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({"action": "chat_send", "meeting_id": meeting.id, "text": text}))
    return {"message": "Chat message sent", "meeting_id": meeting.id}


@router.get(
    "/bots/{platform}/{native_meeting_id}/chat",
    summary="Get chat messages from the meeting",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_chat_read(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()

    meeting = await _find_meeting_any_status(db, current_user.id, platform.value, native_meeting_id)

    messages = []
    if redis_client:
        raw = await redis_client.lrange(f"meeting:{meeting.id}:chat_messages", 0, -1)
        for r in raw:
            try:
                messages.append(json.loads(r))
            except json.JSONDecodeError:
                pass

    if not messages and meeting.data and isinstance(meeting.data, dict):
        messages = meeting.data.get("chat_messages", [])

    return {"messages": messages, "meeting_id": meeting.id}


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

@router.post(
    "/bots/{platform}/{native_meeting_id}/screen",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Show content on screen (screen share)",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_screen_show(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)

    content_type = req.get("type")
    if content_type not in ("image", "video", "url", "html"):
        raise HTTPException(status_code=400, detail="type must be one of: image, video, url, html")
    if content_type == "html" and not req.get("html"):
        raise HTTPException(status_code=400, detail="html content is required for type=html")
    elif content_type != "html" and not req.get("url"):
        raise HTTPException(status_code=400, detail="url is required for type=" + content_type)

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "screen_show",
        "meeting_id": meeting.id,
        "type": content_type,
        "url": req.get("url"),
        "html": req.get("html"),
        "start_share": req.get("start_share", True),
    }))
    return {"message": "Screen content command sent", "meeting_id": meeting.id}


@router.delete(
    "/bots/{platform}/{native_meeting_id}/screen",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stop screen sharing",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_screen_stop(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)
    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({"action": "screen_stop", "meeting_id": meeting.id}))
    return {"message": "Screen stop command sent", "meeting_id": meeting.id}


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------

@router.put(
    "/bots/{platform}/{native_meeting_id}/avatar",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Set bot avatar image",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_avatar_set(
    platform: Platform,
    native_meeting_id: str,
    req: dict,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)
    if not req.get("url") and not req.get("image_base64"):
        raise HTTPException(status_code=400, detail="Either 'url' or 'image_base64' must be provided")

    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({
        "action": "avatar_set",
        "meeting_id": meeting.id,
        "url": req.get("url"),
        "image_base64": req.get("image_base64"),
    }))
    return {"message": "Avatar set command sent", "meeting_id": meeting.id}


@router.delete(
    "/bots/{platform}/{native_meeting_id}/avatar",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reset bot avatar to default",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_avatar_reset(
    platform: Platform,
    native_meeting_id: str,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)
    channel = f"bot_commands:meeting:{meeting.id}"
    await redis_client.publish(channel, json.dumps({"action": "avatar_reset", "meeting_id": meeting.id}))
    return {"message": "Avatar reset command sent", "meeting_id": meeting.id}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@router.get(
    "/bots/{platform}/{native_meeting_id}/events",
    summary="Get recent voice agent events for the meeting",
    dependencies=[Depends(get_user_and_token)],
)
async def bot_events(
    platform: Platform,
    native_meeting_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    redis_client = get_redis()

    meeting = await _find_active_meeting(db, current_user.id, platform.value, native_meeting_id)

    events = []
    if redis_client:
        raw = await redis_client.lrange(f"va:meeting:{meeting.id}:event_log", -limit, -1)
        for r in raw:
            try:
                events.append(json.loads(r))
            except json.JSONDecodeError:
                pass

    return {"events": events, "meeting_id": meeting.id, "count": len(events)}
