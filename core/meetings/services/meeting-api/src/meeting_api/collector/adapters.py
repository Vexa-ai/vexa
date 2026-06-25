"""Production adapters — the real implementations of the ``ports.py`` Protocols.

These are the wiring used when the collector runs for real: a SQLAlchemy-async session bound to
the ``meetings`` / ``transcriptions`` tables for the ``TranscriptStore``, and a ``redis.asyncio``
client for the segment-ingestion ``RedisBus`` (XREADGROUP the ``transcription_segments`` stream,
PUBLISH ``tc:meeting:{id}:mutable``).

They are deliberately thin — the carved behavior lives in ``app.py`` / ``ingest.py``; these only
translate the port calls to the concrete clients, exactly as the deployed
``services/meeting-api/meeting_api/collector/`` does (``endpoints.py`` SELECTs; ``consumer.py``
XREADGROUP/XACK; ``processors.py`` HSET/PUBLISH). They carry NO test logic.

Importing the heavy symbols is LAZY (inside ``build_production_app`` / the methods) so the
package can be imported (and unit-tested with the in-memory fakes) without SQLAlchemy-async or
redis installed in the test venv — which is why ``pyproject.toml`` needs NO ``greenlet`` pin
(SQLAlchemy-async is never imported during the gates).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .ports import RedisBus, TranscriptStore


def _doc_ref(doc: dict) -> dict:
    """Normalize a connect-doc body to a stored ``data.docs[]`` ref: ``workspace`` + ``path`` are
    required; ``title`` / ``kind`` ride along when present. Doc bodies live in the agent workspace —
    only this ref is persisted."""
    ref = {"workspace": doc.get("workspace"), "path": doc["path"]}
    for k in ("title", "kind"):
        if doc.get(k) is not None:
            ref[k] = doc[k]
    return ref


def _upsert_doc(docs: list[dict], doc: dict) -> list[dict]:
    """Append the doc ref deduped by ``path`` — re-connecting the same path updates in place
    (idempotent, order-preserving)."""
    ref = _doc_ref(doc)
    out = [d for d in docs if d.get("path") != ref["path"]]
    out.append(ref)
    return out


def _remove_doc(docs: list[dict], path: str) -> list[dict]:
    """Drop the doc ref with ``path`` (idempotent when absent)."""
    return [d for d in docs if d.get("path") != path]


def _segment_to_api(seg: dict) -> dict:
    """Map a stored/Redis segment to an api.v1 ``TranscriptionSegment`` (start/end/text/language
    required; the optional fields ride along)."""
    out = {
        "start": seg.get("start", seg.get("start_time", 0.0)),
        "end": seg.get("end", seg.get("end_time", 0.0)),
        "text": seg.get("text", ""),
        "language": seg.get("language"),
    }
    for k in ("speaker", "completed", "segment_id", "absolute_start_time", "absolute_end_time", "created_at"):
        if seg.get(k) is not None:
            out[k] = seg[k]
    return out


class SqlAlchemyTranscriptStore:
    """``TranscriptStore`` over a SQLAlchemy-async ``session_factory`` (the ``meetings`` /
    ``transcriptions`` tables; recordings/notes live in ``meeting.data`` JSONB — NO separate
    table). Carve of ``collector/endpoints.py`` SELECT/merge logic."""

    def __init__(self, session_factory, redis_client=None):
        self._session_factory = session_factory
        # The live Redis hash of in-flight segments (``meeting:{id}:segments``) is merged on read
        # in prod; the merge helper is kept here when a client is provided.
        self._redis = redis_client

    async def get_transcript(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        from sqlalchemy import select  # lazy: SQLAlchemy not needed for the in-memory fakes

        from .models import Meeting, Transcription  # local re-export of the admin-api models

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                )
                .order_by(Meeting.created_at.desc())
            )
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return None
            seg_rows = (
                await db.execute(
                    select(Transcription).where(Transcription.meeting_id == meeting.id)
                )
            ).scalars().all()
            data = meeting.data if isinstance(meeting.data, dict) else {}
            # Postgres-persisted segments (the background db-writer flush path).
            seg_by_id: dict = {}
            order: list = []
            for r in seg_rows:
                s = _segment_to_api({
                    "start": r.start_time, "end": r.end_time, "text": r.text,
                    "language": r.language, "speaker": r.speaker,
                    "segment_id": r.segment_id, "completed": True,
                })
                sid = s.get("segment_id") or f"pg-{len(order)}"
                if sid not in seg_by_id:
                    order.append(sid)
                seg_by_id[sid] = s
            # Merge the LIVE Redis hash of in-flight segments (``meeting:{id}:segments``) — the source
            # of truth before/until the db-writer flush. The carve had dropped this merge, so a transcript
            # whose segments are still only in Redis (every short/just-finished meeting) read as EMPTY.
            if self._redis is not None:
                try:
                    raw = await self._redis.hgetall(f"meeting:{meeting.id}:segments")
                    for v in (raw.values() if isinstance(raw, dict) else []):
                        try:
                            seg = json.loads(v.decode() if isinstance(v, (bytes, bytearray)) else v)
                        except Exception:
                            continue
                        s = _segment_to_api(seg)
                        sid = s.get("segment_id") or f"rh-{len(order)}"
                        if sid not in seg_by_id:
                            order.append(sid)
                        seg_by_id[sid] = s
                except Exception:
                    pass
            segments = sorted((seg_by_id[k] for k in order), key=lambda s: (s.get("start") or 0.0))
            # The dashboard's renderer SKIPS any segment without absolute_start_time
            # (use-vexa-websocket.ts: `if (!seg.absolute_start_time) continue`). Derive it from the
            # meeting start + the relative offset when a producer didn't supply it, so the historical
            # transcript renders (the carve served only relative start/end → the UI dropped every segment).
            from datetime import timedelta
            base = meeting.start_time or meeting.created_at
            if base is not None:
                for s in segments:
                    if not s.get("absolute_start_time") and s.get("start") is not None:
                        try:
                            s["absolute_start_time"] = (base + timedelta(seconds=float(s["start"]))).isoformat()
                            s["absolute_end_time"] = (base + timedelta(seconds=float(s.get("end") or s["start"]))).isoformat()
                        except Exception:
                            pass
            return {
                "id": meeting.id,
                "platform": meeting.platform,
                "native_meeting_id": meeting.platform_specific_id,
                "constructed_meeting_url": (data.get("constructed_meeting_url")),
                "status": meeting.status,
                "start_time": meeting.start_time.isoformat() if meeting.start_time else None,
                "end_time": meeting.end_time.isoformat() if meeting.end_time else None,
                "recordings": data.get("recordings", []),
                "notes": data.get("notes"),
                "data": data,
                "segments": segments,
            }

    async def list_meetings(self, user_id, *, status=None, platform=None, limit=None, offset=None):
        from sqlalchemy import select

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = select(Meeting).where(Meeting.user_id == user_id)
            if status:
                stmt = stmt.where(Meeting.status == status)
            if platform:
                stmt = stmt.where(Meeting.platform == platform)
            stmt = stmt.order_by(Meeting.created_at.desc())
            if limit:
                stmt = stmt.limit(limit)
            if offset:
                stmt = stmt.offset(offset)
            rows = (await db.execute(stmt)).scalars().all()
            return [
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "platform": m.platform,
                    "native_meeting_id": m.platform_specific_id,
                    "constructed_meeting_url": (m.data or {}).get("constructed_meeting_url")
                    if isinstance(m.data, dict) else None,
                    "status": m.status,
                    "bot_container_id": m.bot_container_id,
                    "start_time": m.start_time.isoformat() if m.start_time else None,
                    "end_time": m.end_time.isoformat() if m.end_time else None,
                    "data": m.data if isinstance(m.data, dict) else {},
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                }
                for m in rows
            ]

    async def authorize_subscribe(self, user_id, platform, native_meeting_id) -> Optional[int]:
        from sqlalchemy import select

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                )
                .order_by(Meeting.created_at.desc())
                .limit(1)
            )
            meeting = (await db.execute(stmt)).scalars().first()
            return meeting.id if meeting else None

    async def append_segment(self, meeting_id, segment) -> None:
        # Live segments land in the Redis hash (``meeting:{id}:segments``), flushed to Postgres by
        # the background db-writer — exactly the parent's persistence-only path.
        if self._redis is None:
            return
        await self._redis.hset(
            f"meeting:{meeting_id}:segments", segment["segment_id"], json.dumps(segment)
        )

    async def _mutate_docs(self, user_id, platform, native_meeting_id, mutator):
        """Owner-scoped atomic read→modify→write of ``meeting.data['docs']`` under ONE
        ``SELECT … FOR UPDATE`` row lock. Returns the updated docs list, or ``None`` when the
        user owns no such meeting."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting)
                .where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                )
                .order_by(Meeting.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return None
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            docs = mutator(list(data.get("docs", [])))
            data["docs"] = docs
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            return docs

    async def connect_doc(self, user_id, platform, native_meeting_id, doc):
        return await self._mutate_docs(
            user_id, platform, native_meeting_id, lambda docs: _upsert_doc(docs, doc)
        )

    async def disconnect_doc(self, user_id, platform, native_meeting_id, path):
        return await self._mutate_docs(
            user_id, platform, native_meeting_id, lambda docs: _remove_doc(docs, path)
        )


class RedisStreamBus:
    """``RedisBus`` over a ``redis.asyncio`` client — XREADGROUP the segments stream, XACK,
    PUBLISH ``tc:meeting:{id}:mutable``. Carve of ``collector/consumer.py`` + ``processors.py``."""

    def __init__(self, client):
        self._client = client

    async def read_segments(self, *, group, consumer, stream, count=10):
        try:
            await self._client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception:
            pass  # BUSYGROUP — group already exists
        resp = await self._client.xreadgroup(
            groupname=group, consumername=consumer, streams={stream: ">"}, count=count
        )
        out: list[tuple[str, dict]] = []
        for _stream_name, messages in resp or []:
            for message_id, fields in messages:
                mid = message_id.decode() if isinstance(message_id, bytes) else message_id
                decoded = {
                    (k.decode() if isinstance(k, bytes) else k):
                    (v.decode() if isinstance(v, bytes) else v)
                    for k, v in fields.items()
                }
                out.append((mid, decoded))
        return out

    async def ack(self, *, group, stream, message_ids):
        if message_ids:
            await self._client.xack(stream, group, *message_ids)

    async def publish(self, channel, data):
        return await self._client.publish(channel, data)


def build_production_app(
    *,
    database_url: Optional[str] = None,
    redis_url: Optional[str] = None,
):
    """Construct the collector app with real SQLAlchemy-async + redis adapters from env.

    Lazy-imports SQLAlchemy + redis so the package can be imported (and unit-tested with fakes)
    without those runtime deps installed in the gate venv.
    """
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from .app import create_app

    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/vexa"
    )
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)

    store = SqlAlchemyTranscriptStore(session_factory, redis_client=redis_client)
    bus = RedisStreamBus(redis_client)
    return create_app(store, bus)
