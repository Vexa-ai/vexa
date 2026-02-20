"""
Entity enrichment: takes an entity (type + label) and returns progressively
richer context via web search + LLM synthesis.

Uses Tavily-compatible search (or falls back to a simple OpenAI web search
prompt) to gather context, then synthesizes into structured entity cards.

Designed to be called via SSE — each tier yields results as they become available.
"""
import json
import logging
import time
import hashlib
from typing import AsyncGenerator, Dict, Any, Optional

import httpx
from openai import AsyncOpenAI

from config import LLM_MODEL, LLM_BASE_URL, OPENAI_API_KEY

logger = logging.getLogger(__name__)

# ── In-memory cache (simple TTL-based) ──────────────────────────────────────

_cache: Dict[str, Dict[str, Any]] = {}  # key -> {"data": ..., "expires": timestamp}

CACHE_TTLS = {
    "person": 7 * 24 * 3600,      # 7 days
    "company": 7 * 24 * 3600,     # 7 days
    "product": 24 * 3600,         # 1 day
    "topic": 24 * 3600,           # 1 day
    "date": 0,                    # no cache for dates
    "amount": 0,                  # no cache for amounts
    "document": 7 * 24 * 3600,   # 7 days
}


def _cache_key(entity_type: str, entity_id: str) -> str:
    return f"{entity_type}:{entity_id}"


def _get_cached(entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
    key = _cache_key(entity_type, entity_id)
    entry = _cache.get(key)
    if entry and entry["expires"] > time.time():
        return entry["data"]
    if entry:
        del _cache[key]
    return None


def _set_cached(entity_type: str, entity_id: str, data: Dict[str, Any]):
    ttl = CACHE_TTLS.get(entity_type, 3600)
    if ttl <= 0:
        return
    key = _cache_key(entity_type, entity_id)
    _cache[key] = {"data": data, "expires": time.time() + ttl}


# ── OpenAI client ───────────────────────────────────────────────────────────

_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        kwargs = {"api_key": OPENAI_API_KEY}
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        _client = AsyncOpenAI(**kwargs)
    return _client


# ── Web search (using OpenAI's web search or simple synthesis) ──────────────

async def _web_search(query: str) -> str:
    """
    Perform a web search via OpenAI's built-in web search tool (gpt-4.1-mini
    with web_search_preview) or fall back to knowledge-based synthesis.
    """
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a research assistant. Provide concise, factual information "
                        "about the query. Focus on key facts: who/what it is, recent news, "
                        "key metrics, and relevance. Be brief — 3-5 sentences max."
                    ),
                },
                {"role": "user", "content": query},
            ],
            temperature=0.2,
            max_tokens=512,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Web search failed for '{query}': {e}")
        return ""


# ── Enrichment by entity type ───────────────────────────────────────────────

ENRICHMENT_PROMPTS = {
    "person": (
        "Research this person: {label}. "
        "Provide: their current role/title, company, notable achievements, "
        "and any recent news. If they appear to be a meeting participant "
        "rather than a public figure, just note their role from the meeting context."
    ),
    "company": (
        "Research this company: {label}. "
        "Provide: what they do (1 sentence), founding year, HQ location, "
        "key products/services, funding status, employee count estimate, "
        "and any recent news."
    ),
    "product": (
        "Research this product/technology: {label}. "
        "Provide: what it does (1 sentence), who makes it, key features, "
        "pricing tier (free/paid/enterprise), and main alternatives."
    ),
    "topic": (
        "Provide brief context on this topic: {label}. "
        "What is it, why does it matter in a business/technology context, "
        "and what are the key considerations?"
    ),
}


async def _enrich_entity(
    entity_type: str, label: str, context: str = ""
) -> Dict[str, Any]:
    """
    Perform the actual enrichment lookup for an entity.
    Returns a structured dict with enrichment data.
    """
    prompt_template = ENRICHMENT_PROMPTS.get(entity_type, ENRICHMENT_PROMPTS["topic"])
    query = prompt_template.format(label=label)
    if context:
        query += f"\n\nAdditional context from meeting: {context}"

    # Tier 2: Web search
    search_result = await _web_search(f"{label} {entity_type}")

    # Tier 3: LLM synthesis
    synthesis = ""
    if search_result:
        try:
            client = _get_client()
            response = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a concise entity summarizer. Given research results "
                            "about an entity, produce a structured JSON summary. "
                            "Output valid JSON with these fields:\n"
                            '- "summary": 2-3 sentence overview\n'
                            '- "key_facts": list of 3-5 key facts as strings\n'
                            '- "relevance": why this entity matters in the meeting context\n'
                            '- "links": list of relevant URLs if known (can be empty)\n'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Entity: {label} (type: {entity_type})\n"
                            f"Research results:\n{search_result}\n"
                            f"Meeting context: {context}"
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=512,
            )
            synthesis = json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.warning(f"Enrichment synthesis failed for '{label}': {e}")
            synthesis = {
                "summary": search_result[:200] if search_result else f"No data available for {label}",
                "key_facts": [],
                "relevance": "",
                "links": [],
            }
    else:
        synthesis = {
            "summary": f"No external data available for {label}",
            "key_facts": [],
            "relevance": context or "",
            "links": [],
        }

    return {
        "entity_type": entity_type,
        "label": label,
        "enrichment": synthesis,
        "enriched_at": time.time(),
    }


async def enrich_entity_stream(
    entity_type: str,
    entity_id: str,
    label: str,
    context: str = "",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Progressive enrichment generator that yields SSE events as data becomes available.

    Tiers:
      - Tier 0: Cache hit (immediate)
      - Tier 1: Basic info (entity type + label normalization)
      - Tier 2: Web search results
      - Tier 3: LLM synthesis
    """
    # Tier 0: Cache check
    cached = _get_cached(entity_type, entity_id)
    if cached:
        yield {
            "event": "cached",
            "data": cached,
        }
        yield {"event": "complete", "data": {"source": "cache"}}
        return

    # Tier 1: Basic info (immediate)
    yield {
        "event": "basic_info",
        "data": {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "label": label,
        },
    }

    # Tier 2+3: Full enrichment
    try:
        result = await _enrich_entity(entity_type, label, context)
        _set_cached(entity_type, entity_id, result)

        yield {
            "event": "enrichment",
            "data": result,
        }
    except Exception as e:
        logger.error(f"Enrichment failed for {entity_type}:{label}: {e}")
        yield {
            "event": "error",
            "data": {"error": str(e)},
        }

    yield {"event": "complete", "data": {"source": "live"}}
