"""
Runtime tracker configuration.

The tracker config defines WHAT the LLM listens for.
It is loaded from the default at startup and can be updated live via PUT /config.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import copy

# ── Default config ─────────────────────────────────────────────────────────────

DEFAULT_TRACKER_NAME = "Meeting Intelligence"
DEFAULT_TRACKER_DESCRIPTION = "Detects decisions, action items, key insights, and commitments."

DEFAULT_CATEGORIES = [
    {
        "key": "decision",
        "label": "Decision",
        "description": 'Something the group has clearly agreed to or resolved ("we will", "we\'ve decided", "let\'s go with", "we agreed")',
        "enabled": True,
    },
    {
        "key": "action_item",
        "label": "Action Item",
        "description": 'A concrete task assigned to someone with clear ownership ("John will", "we need to", "I\'ll take care of", "Alice is going to")',
        "enabled": True,
    },
    {
        "key": "key_insight",
        "label": "Key Insight",
        "description": "An important observation, status update, risk flag, or strategic insight shared during the meeting that others should know about",
        "enabled": True,
    },
    {
        "key": "commitment",
        "label": "Commitment",
        "description": 'A timeline, deadline, or resource commitment ("by end of quarter", "we\'ll ship by Friday", "budget approved for X")',
        "enabled": True,
    },
]

DEFAULT_EXTRA_INSTRUCTIONS = (
    "Be conservative. Tentative language (\"maybe\", \"what if\", \"could we\") is NOT a decision. "
    "If multiple things are present, pick the most significant one. "
    "Keep summaries short and specific (one sentence). "
    "Include the names of people mentioned whenever possible."
)

# ── Live config store ─────────────────────────────────────────────────────────

_config = {
    "name": DEFAULT_TRACKER_NAME,
    "description": DEFAULT_TRACKER_DESCRIPTION,
    "categories": copy.deepcopy(DEFAULT_CATEGORIES),
    "extra_instructions": DEFAULT_EXTRA_INSTRUCTIONS,
}


def get_config() -> dict:
    return copy.deepcopy(_config)


def set_config(new_config: dict) -> dict:
    global _config
    _config = {
        "name": new_config.get("name", DEFAULT_TRACKER_NAME),
        "description": new_config.get("description", DEFAULT_TRACKER_DESCRIPTION),
        "categories": new_config.get("categories", copy.deepcopy(DEFAULT_CATEGORIES)),
        "extra_instructions": new_config.get("extra_instructions", DEFAULT_EXTRA_INSTRUCTIONS),
    }
    return get_config()


def reset_config() -> dict:
    global _config
    _config = {
        "name": DEFAULT_TRACKER_NAME,
        "description": DEFAULT_TRACKER_DESCRIPTION,
        "categories": copy.deepcopy(DEFAULT_CATEGORIES),
        "extra_instructions": DEFAULT_EXTRA_INSTRUCTIONS,
    }
    return get_config()


# ── Prompt / tool schema builders ─────────────────────────────────────────────

def build_system_prompt() -> str:
    cfg = get_config()
    enabled = [c for c in cfg["categories"] if c.get("enabled", True)]

    lines = [
        "You are a precise meeting analyst.",
        "You are given a rolling window of recent transcript segments from a live meeting.",
        "",
        "Your job: detect exactly ONE of the following, if present:",
    ]
    for cat in enabled:
        lines.append(f'- **{cat["key"]}**: {cat["description"]}')
    lines.append("- **no_match**: nothing significant to capture right now")
    lines.append("")
    lines.append("Rules:")
    for rule in cfg["extra_instructions"].split(". "):
        rule = rule.strip()
        if rule:
            lines.append(f"- {rule}.")
    lines.append("- Always call capture_meeting_item — even for no_match.")
    lines.append("- Extract entities (people, companies, products, dates, amounts, documents, topics) relevant to the detected item.")
    lines.append("- For no_match, entities should be an empty array.")
    return "\n".join(lines)


def build_tool_schema() -> dict:
    cfg = get_config()
    enabled_keys = [c["key"] for c in cfg["categories"] if c.get("enabled", True)]
    type_enum = enabled_keys + ["no_match"]

    cat_descriptions = {c["key"]: c["description"] for c in cfg["categories"]}
    type_desc_parts = [f'"{k}": {cat_descriptions.get(k, k)}' for k in enabled_keys]
    type_desc = "; ".join(type_desc_parts) + '; "no_match": nothing found'

    return {
        "type": "function",
        "function": {
            "name": "capture_meeting_item",
            "description": (
                f"Call this when you detect a tracked item in the transcript. "
                f"Categories: {', '.join(enabled_keys)}. "
                f"Call with type='no_match' if nothing significant is present."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": type_enum,
                        "description": type_desc,
                    },
                    "summary": {
                        "type": "string",
                        "description": "One-sentence summary of the item. Empty string for no_match.",
                    },
                    "speaker": {
                        "type": ["string", "null"],
                        "description": "Speaker name if clearly attributable, otherwise null.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0 and 1.",
                    },
                    "entities": {
                        "type": "array",
                        "description": (
                            "Entities mentioned in this item. Extract people, companies, "
                            "products, dates/deadlines, dollar amounts, documents, and topics. "
                            "Only include entities directly relevant to this specific item."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["person", "company", "product", "date", "amount", "document", "topic"],
                                    "description": "Entity type.",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Display text for the entity (e.g. 'Sarah Chen', 'AWS', 'March 15').",
                                },
                                "id": {
                                    "type": "string",
                                    "description": "Unique slug ID, lowercase with hyphens (e.g. 'sarah-chen', 'aws', 'mar-15').",
                                },
                            },
                            "required": ["type", "label", "id"],
                        },
                    },
                },
                "required": ["type", "summary", "speaker", "confidence"],
            },
        },
    }
