"""Meeting Intelligence — AI-powered post-meeting note generation.

Triggers on meeting.completed, fetches transcripts, calls LLM,
and persists structured notes into meeting.data['ai_notes'].
"""

import json
import logging
import os
from datetime import datetime

import httpx
from sqlalchemy import select

from .database import async_session_local
from .models import Meeting, Transcription
from .intelligence_config import (
    AI_MODEL,
    AI_API_KEY,
    AI_BASE_URL,
    AI_API_VERSION,
    AI_NOTES_ENABLED,
    AI_NOTES_SYSTEM_PROMPT,
    AI_NOTES_TIMEOUT,
    MAX_TRANSCRIPT_TOKENS,
)

logger = logging.getLogger("meeting_api.meeting_intelligence")


def _resolve_provider_config():
    """Resolve provider/base_url from AI_MODEL (same logic as dashboard route.ts)."""
    if not AI_MODEL:
        return None, None, None
    parts = AI_MODEL.split("/", 1)
    if len(parts) != 2:
        return None, None, None
    provider, model = parts[0].lower(), parts[1]
    api_key = AI_API_KEY or "not-needed"
    base_url = AI_BASE_URL

    provider_urls = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://localhost:11434/v1",
        "local": "http://localhost:11434/v1",
        "custom": base_url or "http://localhost:11434/v1",
    }

    if provider in provider_urls:
        base_url = base_url or provider_urls[provider]

    if provider == "anthropic":
        # Anthropic uses /v1/messages, not /v1/chat/completions
        endpoint = f"{base_url.rstrip('/')}/v1/messages"
        return "anthropic", model, endpoint
    else:
        # OpenAI-compatible
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        return "openai-compatible", model, endpoint


def _build_transcript_text(segments):
    """Build a plain-text transcript from Transcription segments, sorted by time."""
    sorted_segs = sorted(segments, key=lambda s: s.start_time)
    lines = []
    for seg in sorted_segs:
        mins, secs = divmod(int(seg.start_time), 60)
        ts = f"{mins:02d}:{secs:02d}"
        speaker = f" [{seg.speaker}]" if seg.speaker else ""
        lines.append(f"{ts}{speaker}: {seg.text}")
    return "\n".join(lines)


async def fetch_transcripts_for_meeting(meeting_id: int):
    """Fetch all Transcription rows for a meeting from the DB."""
    async with async_session_local() as db:
        result = await db.execute(
            select(Transcription).where(Transcription.meeting_id == meeting_id).order_by(Transcription.start_time)
        )
        return result.scalars().all()


async def generate_ai_notes(meeting_id: int):
    """Generate AI notes for a completed meeting and persist to meeting.data.

    Returns True if notes were generated and saved, False otherwise.
    """
    if not AI_NOTES_ENABLED:
        logger.info("AI notes generation not configured (set AI_MODEL + AI_API_KEY/AI_BASE_URL)")
        return False

    provider, model, endpoint = _resolve_provider_config()
    if not provider:
        logger.error("AI_MODEL is not set or has invalid format (expected provider/model)")
        return False

    # Fetch transcripts
    segments = await fetch_transcripts_for_meeting(meeting_id)
    if not segments:
        logger.info(f"No transcript segments for meeting {meeting_id}, skipping AI notes")
        return False

    transcript_text = _build_transcript_text(segments)
    logger.info(
        f"Generating AI notes for meeting {meeting_id}: "
        f"{len(segments)} segments, {len(transcript_text)} chars, provider={provider}, model={model}"
    )

    # Truncate if needed (rough char-based cap; tokens ~ 4 chars each)
    max_chars = MAX_TRANSCRIPT_TOKENS * 4
    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars] + "\n...(truncated)"

    # Build the request
    user_message = f"{AI_NOTES_SYSTEM_PROMPT}\n\nTRANSCRIPT:\n{transcript_text}"

    if provider == "anthropic":
        req_body = _build_anthropic_request(model, user_message)
        headers = _get_anthropic_headers()
    else:
        req_body = _build_openai_request(model, user_message)
        headers = _get_openai_headers()

    try:
        async with httpx.AsyncClient(timeout=AI_NOTES_TIMEOUT) as client:
            resp = await client.post(endpoint, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Extract response text
        if provider == "anthropic":
            ai_text = data.get("content", [{}])[0].get("text", "")
        else:
            ai_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Parse JSON from response (strip markdown code fences if present)
        notes = _parse_ai_notes(ai_text)

        if not notes:
            logger.error(f"AI returned empty notes for meeting {meeting_id}")
            return False

        # Persist to meeting.data
        async with async_session_local() as db:
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                logger.error(f"Meeting {meeting_id} not found for saving AI notes")
                return False

            data_dict = dict(meeting.data or {})
            data_dict["ai_notes"] = notes
            data_dict["ai_notes_generated_at"] = datetime.utcnow().isoformat()
            data_dict["ai_notes_model"] = f"{provider}/{model}"
            meeting.data = data_dict
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(meeting, "data")
            await db.commit()

        logger.info(
            f"AI notes saved for meeting {meeting_id}: "
            f"summary={len(notes.get('summary', ''))} chars, "
            f"moments={len(notes.get('key_moments', []))}, "
            f"decisions={len(notes.get('decisions', []))}, "
            f"action_items={len(notes.get('action_items', []))}"
        )
        return True

    except httpx.RequestError as e:
        logger.error(f"Network error generating AI notes for meeting {meeting_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error generating AI notes for meeting {meeting_id}: {e}", exc_info=True)
        return False


def _build_openai_request(model, user_text):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an expert meeting analyst. Return ONLY valid JSON."},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }


def _build_anthropic_request(model, user_text):
    return {
        "model": model,
        "system": "You are an expert meeting analyst. Return ONLY valid JSON.",
        "messages": [{"role": "user", "content": user_text}],
        "temperature": 0.3,
        "max_tokens": 8000,
    }


def _get_openai_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {AI_API_KEY}",
    }


def _get_anthropic_headers():
    return {
        "Content-Type": "application/json",
        "x-api-key": AI_API_KEY,
        "anthropic-version": "2023-06-01",
    }


def _parse_ai_notes(ai_text: str) -> dict | None:
    """Parse the AI response, stripping markdown code fences if present."""
    text = ai_text.strip()
    # Strip ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse AI notes JSON: {text[:200]}")
        return None
