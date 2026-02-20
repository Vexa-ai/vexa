"""
LLM function-calling layer.

Takes a list of transcript segments (already windowed + offset-trimmed),
calls the LLM once, and returns zero or one MeetingItem.
Returns None for no_match or on any LLM error.

Also exposes is_duplicate_llm() — a cheap second-pass check that asks the
LLM whether a newly detected item is semantically equivalent to any of the
already-stored candidates for the same meeting.
"""
import logging
import json
from typing import List, Optional

from openai import AsyncOpenAI

from config import LLM_MODEL, LLM_BASE_URL, OPENAI_API_KEY
from tracker_config import build_system_prompt, build_tool_schema

logger = logging.getLogger(__name__)

# Lazy-initialised client (created on first call so config is fully loaded)
_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        kwargs = {"api_key": OPENAI_API_KEY}
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        _client = AsyncOpenAI(**kwargs)
    return _client


def _format_segments(segments: List[dict]) -> str:
    lines = []
    for seg in segments:
        speaker = seg.get("speaker") or "Unknown"
        text = (seg.get("text") or "").strip()
        ts = seg.get("absolute_start_time") or ""
        if text:
            lines.append(f"[{ts}] {speaker}: {text}")
    return "\n".join(lines) if lines else "(no transcript yet)"


async def analyze_window(segments: List[dict]) -> Optional[dict]:
    """
    Run LLM function calling on the segment window.
    Returns a dict like:
      {"type": "decision", "summary": "...", "speaker": "...", "confidence": 0.9}
    or None on no_match / error.
    """
    transcript_text = _format_segments(segments)
    user_message = f"Transcript window:\n\n{transcript_text}"

    # Build prompt and schema from live config on every call
    system_prompt = build_system_prompt()
    tool_schema = build_tool_schema()

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            tools=[tool_schema],
            tool_choice={"type": "function", "function": {"name": "capture_meeting_item"}},
            temperature=0.1,
            max_tokens=256,
        )

        msg = response.choices[0].message
        if not msg.tool_calls:
            logger.warning("LLM returned no tool call")
            return None

        args_raw = msg.tool_calls[0].function.arguments
        args = json.loads(args_raw)

        item_type = args.get("type", "no_match")
        if item_type == "no_match":
            logger.debug("LLM: no_match")
            return None

        return {
            "type": item_type,
            "summary": args.get("summary", ""),
            "speaker": args.get("speaker"),
            "confidence": float(args.get("confidence", 0.0)),
            "entities": args.get("entities", []),
        }

    except Exception as e:
        logger.error(f"LLM call failed: {e}", exc_info=True)
        return None


async def is_duplicate_llm(new_summary: str, existing_summaries: List[str]) -> bool:
    """
    Ask the LLM whether `new_summary` is semantically the same as any item in
    `existing_summaries`.  Uses a minimal prompt with a yes/no response — fast
    and cheap (no tools, tiny token count).

    Returns True  → treat as duplicate, discard.
    Returns False → new unique item, keep it.
    Falls back to False on any error so we never accidentally suppress items.
    """
    if not existing_summaries:
        return False

    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(existing_summaries))
    system = (
        "You are a deduplication assistant. "
        "Answer with exactly one word: YES or NO."
    )
    user = (
        f"New item:\n{new_summary}\n\n"
        f"Already captured items:\n{numbered}\n\n"
        "Does the new item describe the same thing as any already-captured item "
        "(same person, same task/decision, just worded differently)? YES or NO."
    )

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=5,
        )
        answer = response.choices[0].message.content.strip().upper()
        logger.debug(f"Dedup LLM answer: {answer!r} for: {new_summary[:60]}")
        return answer.startswith("YES")
    except Exception as e:
        logger.warning(f"Dedup LLM call failed (treating as non-duplicate): {e}")
        return False
