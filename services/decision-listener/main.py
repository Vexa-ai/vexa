"""
decision-listener service

Endpoints:
  GET  /health                         — liveness check
  GET  /decisions/{meeting_id}         — SSE stream of detected items (live)
  GET  /decisions/{meeting_id}/all     — snapshot of all stored decisions for meeting
  GET  /config                         — get tracker configuration
  PUT  /config                         — update tracker configuration
  POST /config/reset                   — reset tracker configuration
  POST /narrative/{meeting_id}         — generate meeting anthology narrative
  GET  /enrich/{entity_type}/{entity_id} — SSE stream of entity enrichment data
"""
import asyncio
import json
import logging
import os

from fastapi import FastAPI, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import HTTP_PORT, LOG_LEVEL, DECISIONS_KEY_PREFIX
from listener import start_listener, register_sse_queue, unregister_sse_queue, get_redis
from tracker_config import get_config, set_config, reset_config
from narrative import generate_narrative, generate_summary
from enrichment import enrich_entity_stream

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the pub/sub listener as a background task
    task = asyncio.create_task(start_listener())
    logger.info("decision-listener started")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Vexa Decision Listener", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Tracker config endpoints ────────────────────────────────────────────────

@app.get("/config")
async def config_get():
    """Get current tracker configuration."""
    return get_config()


@app.put("/config")
async def config_put(request: Request):
    """Update tracker configuration at runtime."""
    try:
        body = await request.json()
        updated = set_config(body)
        logger.info("Tracker config updated")
        return updated
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/config/reset")
async def config_reset():
    """Reset tracker configuration to defaults."""
    result = reset_config()
    logger.info("Tracker config reset to defaults")
    return result


# ── Live decisions SSE ──────────────────────────────────────────────────────

@app.get("/decisions/{meeting_id}")
async def decisions_sse(meeting_id: str, request: Request):
    """
    SSE stream. Each event is a JSON-encoded detected item:
      {"type": "decision", "summary": "...", "speaker": "...", "confidence": 0.9, "meeting_id": "42"}

    Connect and leave open; items are pushed as they are detected.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    register_sse_queue(meeting_id, q)

    async def event_generator():
        try:
            # Send a comment to establish connection
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    item["meeting_id"] = meeting_id
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        finally:
            unregister_sse_queue(meeting_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/decisions/{meeting_id}/all")
async def decisions_all(meeting_id: str):
    """Snapshot of all stored decisions for a meeting (from Redis list)."""
    redis = await get_redis()
    if redis is None:
        return JSONResponse({"error": "Redis not ready"}, status_code=503)
    key = DECISIONS_KEY_PREFIX.format(meeting_id=meeting_id)
    try:
        raw_items = await redis.lrange(key, 0, -1)
        items = [json.loads(r) for r in raw_items]
        return {"meeting_id": meeting_id, "count": len(items), "items": items}
    except Exception as e:
        logger.error(f"Failed to fetch decisions for {meeting_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Lightweight summary ─────────────────────────────────────────────────────

@app.get("/summary/{meeting_id}")
async def summary_get(meeting_id: str):
    """
    Generate a lightweight 1-2 sentence summary from detected items for a meeting.
    Much faster than full narrative — called periodically by the anthology view.

    Returns: {"meeting_id": "...", "summary": {"lede": "...", "theme": "..."}, "item_count": N}
    """
    try:
        detected_items = []
        redis = await get_redis()
        if redis:
            key = DECISIONS_KEY_PREFIX.format(meeting_id=meeting_id)
            try:
                raw_items = await redis.lrange(key, 0, -1)
                detected_items = [json.loads(r) for r in raw_items]
            except Exception as e:
                logger.warning(f"Failed to load items for summary: {e}")

        if not detected_items:
            return {
                "meeting_id": meeting_id,
                "summary": {"lede": "", "theme": ""},
                "item_count": 0,
            }

        summary = await generate_summary(detected_items)
        return {
            "meeting_id": meeting_id,
            "summary": summary or {"lede": "", "theme": ""},
            "item_count": len(detected_items),
        }
    except Exception as e:
        logger.error(f"Summary generation error: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Narrative generation ────────────────────────────────────────────────────

@app.post("/narrative/{meeting_id}")
async def narrative_generate(meeting_id: str, request: Request):
    """
    Generate a meeting anthology narrative from transcript segments + detected items.

    Request body:
    {
        "segments": [...],          // transcript segments
        "participants": ["..."],    // optional participant names
    }

    The detected items are automatically loaded from Redis.
    Returns the structured narrative JSON.
    """
    try:
        body = await request.json()
        segments = body.get("segments", [])
        participants = body.get("participants", None)

        if not segments:
            return JSONResponse(
                {"error": "No segments provided"},
                status_code=400,
            )

        # Load existing detected items from Redis
        detected_items = []
        redis = await get_redis()
        if redis:
            key = DECISIONS_KEY_PREFIX.format(meeting_id=meeting_id)
            try:
                raw_items = await redis.lrange(key, 0, -1)
                detected_items = [json.loads(r) for r in raw_items]
            except Exception as e:
                logger.warning(f"Failed to load items for narrative: {e}")

        narrative = await generate_narrative(segments, detected_items, participants)

        if narrative is None:
            return JSONResponse(
                {"error": "Failed to generate narrative"},
                status_code=500,
            )

        return {
            "meeting_id": meeting_id,
            "narrative": narrative,
        }

    except Exception as e:
        logger.error(f"Narrative generation error: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Entity enrichment SSE ───────────────────────────────────────────────────

@app.get("/enrich/{entity_type}/{entity_id}")
async def enrich_entity(
    entity_type: str,
    entity_id: str,
    request: Request,
    label: str = Query(..., description="Display label of the entity"),
    context: str = Query("", description="Meeting context for this entity"),
):
    """
    SSE stream of progressive entity enrichment data.

    Events:
      - basic_info: immediate type + label
      - cached: full cached result (if available)
      - enrichment: full enrichment result
      - error: if something went wrong
      - complete: done
    """
    async def event_generator():
        try:
            async for event in enrich_entity_stream(
                entity_type=entity_type,
                entity_id=entity_id,
                label=label,
                context=context,
            ):
                if await request.is_disconnected():
                    break
                event_type = event.get("event", "data")
                event_data = event.get("data", {})
                yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
        except Exception as e:
            logger.error(f"Enrichment SSE error: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT)
