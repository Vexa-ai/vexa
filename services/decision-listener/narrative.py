"""
Narrative generation: converts a list of transcript segments + detected items
into a structured meeting anthology (lede, nut graf, moments, kicker).

Each section contains inline entity annotations that the frontend renders
as interactive chips.
"""
import json
import logging
from typing import List, Optional, Dict, Any

from openai import AsyncOpenAI

from config import LLM_MODEL, LLM_BASE_URL, OPENAI_API_KEY

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        kwargs = {"api_key": OPENAI_API_KEY}
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        _client = AsyncOpenAI(**kwargs)
    return _client


NARRATIVE_SYSTEM_PROMPT = """\
You are a meeting narrative architect. You transform raw meeting transcripts into
a structured, concise anthology — like a short newspaper article, not bullet lists.

You MUST output valid JSON matching the schema below. Every significant noun
(person names, company names, product names, dollar amounts, dates/deadlines,
document references) should be wrapped as an entity annotation.

Entity annotation format in text:
  {{entity:TYPE:LABEL:ID}}

Where TYPE is one of: person, company, product, date, amount, document, topic
LABEL is the display text
ID is a unique slug (lowercase, hyphens)

Example: "{{entity:person:Sarah Chen:sarah-chen}} committed to shipping {{entity:product:v2.0:v2-0}} by {{entity:date:March 15:mar-15}}"

Output JSON schema:
{
  "lede": "1-2 sentence verdict with entity annotations",
  "nut_graf": "1 short paragraph of context with entity annotations",
  "moments": [
    {
      "timestamp": "HH:MM:SS or approximate",
      "speaker": "Name",
      "narrative": "2-3 sentence description with entity annotations",
      "tag": "context" | "tension" | "resolution" | "decision",
      "decision": {
        "type": "decision" | "action_item",
        "summary": "what was decided/assigned",
        "owner": "person name or null",
        "deadline": "date or null"
      } | null
    }
  ],
  "kicker": "1-2 sentence forward-looking close with entity annotations",
  "entities": [
    {
      "id": "sarah-chen",
      "type": "person",
      "label": "Sarah Chen",
      "context": "Engineering lead, driving v2.0 scope decisions"
    }
  ]
}

Rules:
- The lede must be opinionated — not "the meeting covered X" but "the team decided X after Y"
- Moments should be chronological and capture the KEY beats, not every utterance
- Aim for 3-8 moments depending on meeting length
- Decisions/action items should appear as decision blocks INSIDE the moment where they happened
- The kicker points forward — what happens next
- Keep everything concise. Summaries earn their place or get cut.
- Extract ALL entities mentioned and list them in the entities array
- If participants are provided, use their full names from the participant list
"""


SUMMARY_SYSTEM_PROMPT = """\
You are a concise meeting summarizer. Given a list of decisions, action items, and
architecture statements detected during a meeting, produce a brief JSON summary.

Output valid JSON with these fields:
- "lede": 1-2 sentence concise summary of the most important outcomes so far
- "theme": a 2-4 word theme label (e.g. "API Migration Planning", "Q2 Budget Review")

Rules:
- Be opinionated and specific — "Team committed to Redis migration by March" not "The team discussed various topics"
- Focus on DECISIONS and ACTION ITEMS, not just context
- Keep the lede under 40 words
- If there are no items yet, return {"lede": "", "theme": ""}
"""


async def generate_summary(
    detected_items: List[dict],
    participants: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate a lightweight 1-2 sentence summary from detected items.
    Much faster than full narrative generation — meant to be called periodically.
    """
    if not detected_items:
        return {"lede": "", "theme": ""}

    items_lines = []
    for item in detected_items:
        items_lines.append(
            f"- [{item.get('type', 'unknown')}] {item.get('summary', '')} "
            f"(speaker: {item.get('speaker', 'unknown')})"
        )
    items_text = "\n".join(items_lines)

    participants_context = ""
    if participants:
        participants_context = f"\nParticipants: {', '.join(participants)}"

    user_message = (
        f"Summarize these {len(detected_items)} detected meeting items:"
        f"{participants_context}\n\n{items_text}"
    )

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=256,
        )

        content = response.choices[0].message.content
        result = json.loads(content)
        return {
            "lede": result.get("lede", ""),
            "theme": result.get("theme", ""),
        }
    except Exception as e:
        logger.error(f"Summary generation failed: {e}", exc_info=True)
        return None


async def generate_narrative(
    segments: List[dict],
    detected_items: List[dict],
    participants: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate a structured meeting anthology from transcript segments and
    already-detected decision items.

    Returns the narrative dict or None on error.
    """
    # Format transcript
    lines = []
    for seg in segments:
        speaker = seg.get("speaker") or "Unknown"
        text = (seg.get("text") or "").strip()
        ts = seg.get("absolute_start_time") or seg.get("start", "")
        if text:
            lines.append(f"[{ts}] {speaker}: {text}")
    transcript_text = "\n".join(lines) if lines else "(empty transcript)"

    # Format detected items as context
    items_context = ""
    if detected_items:
        items_lines = []
        for item in detected_items:
            items_lines.append(
                f"- [{item.get('type', 'unknown')}] {item.get('summary', '')} "
                f"(speaker: {item.get('speaker', 'unknown')}, confidence: {item.get('confidence', 0)})"
            )
        items_context = "\n\nAlready detected items:\n" + "\n".join(items_lines)

    # Participants context
    participants_context = ""
    if participants:
        participants_context = f"\n\nMeeting participants: {', '.join(participants)}"

    user_message = (
        f"Generate a meeting anthology from this transcript."
        f"{participants_context}"
        f"{items_context}"
        f"\n\nTranscript:\n\n{transcript_text}"
    )

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=4096,
        )

        content = response.choices[0].message.content
        narrative = json.loads(content)

        # Validate minimum structure
        if "lede" not in narrative:
            logger.warning("Narrative missing lede, returning None")
            return None

        # Ensure entities array exists
        if "entities" not in narrative:
            narrative["entities"] = []
        if "moments" not in narrative:
            narrative["moments"] = []
        if "kicker" not in narrative:
            narrative["kicker"] = ""
        if "nut_graf" not in narrative:
            narrative["nut_graf"] = ""

        logger.info(
            f"Generated narrative: {len(narrative['moments'])} moments, "
            f"{len(narrative['entities'])} entities"
        )
        return narrative

    except Exception as e:
        logger.error(f"Narrative generation failed: {e}", exc_info=True)
        return None
