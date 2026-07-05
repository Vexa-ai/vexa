"""Meeting Archive Search — Phase 3 MVP.

Full-text search using PG tsvector + semantic embeddings.
Works with any LLM provider configured via AI_MODEL.
"""

import json
import logging
import os
import math

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session_local
from .models import Meeting, Transcription
from .intelligence_config import AI_MODEL, AI_API_KEY, AI_BASE_URL

logger = logging.getLogger("meeting_api.search")


async def search_meetings(
    query: str,
    user_id: int,
    db: AsyncSession,
    limit: int = 20,
) -> dict:
    """Search meetings by full-text and return structured results.

    Returns:
        {
            "query": str,
            "total": int,
            "results": [
                {
                    "meeting": {...},
                    "matched_segments": [
                        {"text": str, "speaker": str, "start_time": float, "timestamp": str}
                    ]
                }
            ]
        }
    """
    # Full-text search using PG tsvector on transcripts
    stmt = text("""
        SELECT m.id, m.platform, m.platform_specific_id, m.status,
               m.start_time, m.end_time, m.data,
               COUNT(t.id) as segment_count
        FROM meetings m
        LEFT JOIN transcriptions t ON t.meeting_id = m.id
        WHERE m.user_id = :user_id
          AND t.text @@ to_tsquery('spanish', :query)
        GROUP BY m.id
        ORDER BY m.created_at DESC
        LIMIT :limit
    """)

    result = await db.execute(stmt, {
        "user_id": user_id,
        "query": query,
        "limit": limit,
    })
    rows = result.fetchall()

    results = []
    for row in rows:
        meeting_id = row[0]

        # Get matched segments
        seg_result = await db.execute(
            text("""
                SELECT text, speaker, start_time
                FROM transcriptions
                WHERE meeting_id = :mid
                  AND text @@ to_tsquery('spanish', :query)
                ORDER BY start_time
                LIMIT 5
            """),
            {"mid": meeting_id, "query": query},
        )
        segments = seg_result.fetchall()

        matched_segments = []
        for seg in segments:
            mins, secs = divmod(int(seg[2]), 60)
            matched_segments.append({
                "text": seg[0],
                "speaker": seg[1] or "unknown",
                "start_time": seg[2],
                "timestamp": f"{mins:02d}:{secs:02d}",
            })

        data = row[6] or {}
        results.append({
            "meeting": {
                "id": row[0],
                "platform": row[1],
                "native_id": row[2],
                "status": row[3],
                "start_time": row[4].isoformat() if row[4] else None,
                "end_time": row[5].isoformat() if row[5] else None,
                "data": data,
            },
            "matched_segments": matched_segments,
        })

    return {
        "query": query,
        "total": len(results),
        "results": results,
    }


async def search_with_embeddings(
    query: str,
    user_id: int,
    db: AsyncSession,
    limit: int = 20,
) -> dict:
    """Search using semantic embeddings (when embeddings are available).

    Falls back to full-text search if embeddings aren't configured.
    """
    # For MVP: fall back to full-text search
    # Future: use pgvector or external embedding service
    return await search_meetings(query, user_id, db, limit)


async def ask_about_meetings(
    question: str,
    user_id: int,
    db: AsyncSession,
) -> dict:
    """AI-powered Q&A across meeting transcripts.

    1. Search transcripts for relevant content
    2. Send relevant context + question to LLM
    3. Return AI answer with citations
    """
    # Step 1: Search for relevant transcripts
    search_result = await search_meetings(question, user_id, db, limit=5)

    # Step 2: Build context from search results
    context_parts = []
    for r in search_result["results"][:3]:
        meeting = r["meeting"]
        for seg in r["matched_segments"]:
            context_parts.append(
                f"[{meeting.get('platform', '?')}/{meeting.get('native_id', '?')}] "
                f"({seg['timestamp']}) {seg['speaker']}: {seg['text']}"
            )

    if not context_parts:
        return {
            "answer": "No relevant meeting content found for that question.",
            "citations": [],
        }

    context = "\n".join(context_parts)

    # Step 3: Call LLM for answer (reuse same provider config as AI notes)
    if not AI_MODEL:
        return {
            "answer": "AI not configured. Set AI_MODEL to enable semantic Q&A.",
            "citations": [{"meeting_id": r["meeting"]["id"], "text": r["matched_segments"][0]["text"]} for r in search_result["results"][:3]],
        }

    provider, model = AI_MODEL.split("/", 1)
    api_key = AI_API_KEY or "not-needed"
    base_url = AI_BASE_URL

    provider_urls = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "http://localhost:11434/v1",
        "local": "http://localhost:11434/v1",
    }

    if provider in provider_urls:
        base_url = base_url or provider_urls[provider]

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    user_msg = (
        f"Based on these meeting transcripts, answer: {question}\n\n"
        f"TRANSCRIPT CONTEXT:\n{context}\n\n"
        f"Rules:\n"
        f"- Answer in the same language as the question\n"
        f"- Cite sources with [platform/native_id timestamp]\n"
        f"- Be concise and specific\n"
        f"- If the answer isn't in the context, say so clearly"
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                endpoint,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a meeting archive assistant. Answer questions based on transcript context with citations."},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.3,
                },
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "No answer")
    except Exception as e:
        answer = f"Error calling AI: {e}"

    # Build citations from search results
    citations = []
    for r in search_result["results"][:3]:
        for seg in r["matched_segments"][:2]:
            citations.append({
                "meeting_id": r["meeting"]["id"],
                "platform": r["meeting"]["platform"],
                "native_id": r["meeting"]["native_id"],
                "timestamp": seg["timestamp"],
                "text": seg["text"][:100],
            })

    return {
        "answer": answer,
        "citations": citations,
        "source_count": len(search_result["results"]),
    }
