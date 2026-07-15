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

from contextlib import asynccontextmanager
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from ..meeting_writes import MEETING_WRITE_LOCK_NAMESPACE, capture_is_withdrawn
from .ports import RedisBus, TranscriptStore, TranscriptWriteRefused


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(iso: Optional[str]) -> bool:
    """True if the ISO-8601 timestamp is in the past (None = never expires)."""
    if not iso:
        return False
    try:
        exp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < _now()
    except ValueError:
        return False


def validate_transcript_grant(grant: dict, user_email: Optional[str]) -> Optional[str]:
    """Shared (fake + real) validation of a transcript share grant → an error code, or None if OK.
    open = anyone authenticated; restricted = the caller's verified email ∈ allowed_emails."""
    if grant.get("revoked"):
        return "revoked"
    if _expired(grant.get("expires_at")):
        return "expired"
    if grant.get("mode") == "restricted":
        allowed = {e.lower() for e in grant.get("allowed_emails", [])}
        if not user_email or user_email.lower() not in allowed:
            return "not_allowed"
    return None


class _RedisTranscriptBatchWriter:
    """Persist one decoded transcript message with one Redis transaction."""

    def __init__(self, redis_client, meeting_id: int):
        self._redis = redis_client
        self._meeting_id = meeting_id

    async def append_segments(self, segments: list[dict]) -> None:
        if self._redis is None or not segments:
            return
        from .db_writer import ACTIVE_MEETINGS_KEY, segments_hash_key

        hash_key = segments_hash_key(self._meeting_id)
        ttl = int(os.environ.get("REDIS_SEGMENT_TTL", "3600"))
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.sadd(ACTIVE_MEETINGS_KEY, str(self._meeting_id))
            for segment in segments:
                pipe.hset(hash_key, segment["segment_id"], json.dumps(segment))
            pipe.expire(hash_key, ttl)
            await pipe.execute()


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


def _merge_notes_by_id(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Merge drained copilot notes into a processed view's ``doc['notes']`` list, keyed by note
    ``id`` (== segment_id): a refining re-emit UPDATES its note in place (order preserved);
    a new id appends. Notes without an id append as-is (nothing to key an upsert on)."""
    out = [dict(n) for n in existing]
    index = {str(n.get("id")): i for i, n in enumerate(out) if n.get("id") is not None}
    for note in incoming:
        nid = note.get("id")
        if nid is not None and str(nid) in index:
            out[index[str(nid)]].update(note)
        else:
            if nid is not None:
                index[str(nid)] = len(out)
            out.append(dict(note))
    return out


def _find_processed_view(data: dict, view_id: str) -> Optional[dict]:
    """The view with ``view_id`` inside ``data['processed']['views']`` (None when absent)."""
    processed = data.get("processed") if isinstance(data.get("processed"), dict) else {}
    views = processed.get("views") if isinstance(processed.get("views"), list) else []
    return next((v for v in views if isinstance(v, dict) and v.get("id") == view_id), None)


def _upsert_processed_view(
    data: dict, *, view_id: str, kind: str, notes: list[dict],
    source_cursor: Optional[str], params: Optional[dict],
) -> dict:
    """Pure merge of drained copilot notes into the ADDRESSABLE, VERSIONED processed shape
    (release DoD — multi-consumer, meeting-scoped today, mountable by N consumers later):

        data.processed = {"views": [{id, kind, params, doc, source_cursor, updated_at}]}

    Upserts the view keyed by ``id`` — other views (future per-workspace/other processings) are
    preserved untouched; merges ``notes`` into the view's ``doc['notes']`` by note id; stamps
    ``params`` (the processing metadata APPLIED — provider/model/pipeline, stamped by the
    producing worker — reproducibility) only when the drain carried them, so an idle drain never
    erases provenance; ``source_cursor`` records the stream position the view reflects.
    Returns the new ``data`` dict (the caller persists it)."""
    from datetime import datetime, timezone

    out = dict(data)
    processed = dict(out.get("processed")) if isinstance(out.get("processed"), dict) else {}
    views = [dict(v) for v in processed.get("views", []) if isinstance(v, dict)] \
        if isinstance(processed.get("views"), list) else []
    view = next((v for v in views if v.get("id") == view_id), None)
    if view is None:
        view = {"id": view_id, "kind": kind, "params": {}, "doc": {"notes": []}}
        views.append(view)
    doc = dict(view.get("doc")) if isinstance(view.get("doc"), dict) else {}
    existing_notes = doc.get("notes") if isinstance(doc.get("notes"), list) else []
    doc["notes"] = _merge_notes_by_id(list(existing_notes), notes)
    view["doc"] = doc
    view["kind"] = kind
    if params:
        view["params"] = params
    if source_cursor:
        view["source_cursor"] = source_cursor
    view["updated_at"] = datetime.now(timezone.utc).isoformat()
    processed["views"] = views
    out["processed"] = processed
    return out


def _segment_to_api(seg: dict) -> dict:
    """Map a stored/Redis segment to an api.v1 ``TranscriptionSegment`` (start/end/text/language
    required; the optional fields ride along)."""
    out = {
        "start": seg.get("start", seg.get("start_time", 0.0)),
        "end": seg.get("end", seg.get("end_time", 0.0)),
        "text": seg.get("text", ""),
        "language": seg.get("language"),
    }
    for k in ("speaker", "completed", "segment_id", "source", "absolute_start_time", "absolute_end_time", "created_at"):
        if seg.get(k) is not None:
            out[k] = seg[k]
    return out


class SqlAlchemyTranscriptStore:
    """``TranscriptStore`` over a SQLAlchemy-async ``session_factory`` (the ``meetings`` /
    ``transcriptions`` tables; recordings/notes live in ``meeting.data`` JSONB — NO separate
    table). Carve of ``collector/endpoints.py`` SELECT/merge logic."""

    def __init__(self, session_factory, redis_client=None, *, statement_factory=None):
        self._session_factory = session_factory
        self._statement_factory = statement_factory
        # The live Redis hash of in-flight segments (``meeting:{id}:segments``) is merged on read
        # in prod; the merge helper is kept here when a client is provided.
        self._redis = redis_client
        # numeric meeting_id → (native_meeting_id, platform). The id→native map is immutable for a
        # meeting row, so cache it forever once resolved (bounded by the live meeting set).
        self._native_cache: dict[int, tuple[str, str]] = {}

    def _statement(self, sql: str):
        if self._statement_factory is not None:
            return self._statement_factory(sql)
        from sqlalchemy import text

        return text(sql)

    async def native_for(self, meeting_id) -> "Optional[tuple[str, str]]":
        """Resolve a NUMERIC meeting_id → (native_meeting_id, platform) from the meetings table.

        Cross-user (the collector is the trusted internal segment consumer — it owns the mapping and
        is NOT user-scoped): the agent-api live-transcript relay re-keys numeric→native off this, so a
        meeting's segments reach the terminal's native channel regardless of which user owns it. Cached
        because the pair is immutable per row. Returns None if the id is unknown (caller keeps numeric)."""
        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            return None
        if mid in self._native_cache:
            return self._native_cache[mid]
        from sqlalchemy import select  # lazy: not needed for the in-memory fakes

        from .models import Meeting

        async with self._session_factory() as db:
            m = (await db.execute(select(Meeting).where(Meeting.id == mid))).scalars().first()
            if not m or not m.platform_specific_id:
                return None
            pair = (m.platform_specific_id, m.platform or "google_meet")
            self._native_cache[mid] = pair
            return pair

    async def _transcript_doc(self, db, meeting) -> dict:
        """Build the api.v1 ``TranscriptionResponse`` dict for a resolved ``meeting`` ROW — the shared
        body used by BOTH ``get_transcript`` (native → newest row) and ``get_transcript_by_id`` (exact
        row). Reads the row's persisted ``transcriptions`` + merges the live redis in-flight hash, all
        keyed by ``meeting.id`` (the row id) — so a by-id read returns EXACTLY that row's segments/notes,
        never a sibling row's (the wrong-row hydration fix)."""
        from sqlalchemy import select

        from .models import Transcription

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

    async def get_transcript(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        from sqlalchemy import select  # lazy: SQLAlchemy not needed for the in-memory fakes

        from .models import Meeting  # local re-export of the admin-api models

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
            return await self._transcript_doc(db, meeting)

    async def get_transcript_by_id(self, user_id, meeting_id, member_workspaces=None) -> Optional[dict]:
        """Exact-row transcript for ``meeting.id == meeting_id``, authorized by the SAME three-way rule as
        authorize_subscribe: (a) owner, (b) member of the bound workspace, (c) redeemed a transcript-share
        link (``data.transcript_viewers``). Any other caller → ``None`` (→ 404), so it can never leak an
        unrelated tenant's transcript (P0) while letting a shared recipient load the durable feed."""
        from sqlalchemy import select

        from .models import Meeting

        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            return None
        async with self._session_factory() as db:
            meeting = (await db.execute(select(Meeting).where(Meeting.id == mid))).scalars().first()
            if not meeting:
                return None
            data = meeting.data if isinstance(meeting.data, dict) else {}
            authorized = (
                meeting.user_id == user_id                                          # (a) owner
                or user_id in (data.get("transcript_viewers") or [])                # (c) transcript-share
                or (bool(member_workspaces) and data.get("workspace_id") in member_workspaces)  # (b) bound ws member
            )
            if not authorized:
                return None
            return await self._transcript_doc(db, meeting)

    async def list_meetings(self, user_id, *, status=None, platform=None, limit=None, offset=None, member_workspaces=None):
        from sqlalchemy import cast, func, or_, select
        from sqlalchemy.dialects.postgresql import JSONB

        from .models import Meeting

        async with self._session_factory() as db:
            # ACCESS = owner OR transcript-share viewer OR member of the bound workspace. Shared meetings
            # (owned by someone else) surface in the caller's list so a share recipient can find + open them.
            access = [
                Meeting.user_id == user_id,
                cast(Meeting.data["transcript_viewers"], JSONB).op("@>")(func.to_jsonb(user_id)),
            ]
            if member_workspaces:
                access.append(Meeting.data["workspace_id"].astext.in_(list(member_workspaces)))
            stmt = select(Meeting).where(or_(*access))
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
                    "shared": m.user_id != user_id,   # surfaced via a share/membership, not owned by the caller
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                }
                for m in rows
            ]

    async def authorize_subscribe(self, user_id, platform, native_meeting_id, member_workspaces=None) -> Optional[int]:
        """Authorize a live-transcript subscribe → the meeting ROW id, or None. TWO branches:
        (a) OWNERSHIP (unchanged) — the meeting's owner may always subscribe;
        (b) MEMBERSHIP (Lane A) — any meeting BOUND (``data.workspace_id``) to a shared workspace the
            caller is a member of. ``member_workspaces`` is the caller's workspace-id set (gateway-injected
            x-user-workspaces). The binding IS the authorization: a member of the bound workspace sees the
            feed. Native-id collisions across tenants are handled by scanning candidates and matching the
            binding, never by picking a row blindly."""
        from sqlalchemy import select

        from .models import Meeting

        async with self._session_factory() as db:
            owned = (await db.execute(
                select(Meeting).where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                ).order_by(Meeting.created_at.desc()).limit(1)
            )).scalars().first()
            if owned:
                return owned.id  # (a) owner
            rows = (await db.execute(
                select(Meeting).where(
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                )
            )).scalars().all()
            for mtg in rows:
                data = mtg.data if isinstance(mtg.data, dict) else {}
                if member_workspaces and data.get("workspace_id") in member_workspaces:
                    return mtg.id  # (b) member of the meeting's bound shared workspace (optional convenience)
                if user_id in (data.get("transcript_viewers") or []):
                    return mtg.id  # (c) redeemed an INDEPENDENT transcript-share link for this meeting
            return None

    async def bind_workspace(self, user_id, platform, native_meeting_id, workspace_id) -> "Optional[str]":
        """OWNER-scoped: bind the meeting to a shared workspace (``data.workspace_id``) so its members can
        subscribe to the live transcript feed (authorize_subscribe branch b). Many meetings → one workspace
        (Amendment 6). Returns the bound workspace_id, or None if the caller owns no such meeting."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = (
                select(Meeting).where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                ).order_by(Meeting.created_at.desc()).limit(1).with_for_update()
            )
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return None
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            data["workspace_id"] = workspace_id
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            return workspace_id

    async def mint_transcript_share(self, user_id, platform, native_meeting_id, *,
                                    mode="open", allowed_emails=None, expires_in_sec=86400) -> "Optional[dict]":
        """OWNER-scoped: mint an INDEPENDENT transcript share grant (no workspace needed). Stored in
        ``data.share_grants[]`` as {id, secret_hash, mode, allowed_emails, expires_at, revoked} — only the
        HASH, never the token. Returns {id, token, ...} ONCE (token = ``<meeting_id>.<secret>`` so redeem
        resolves the meeting). None if the caller owns no such meeting."""
        from datetime import timedelta

        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = (select(Meeting).where(
                Meeting.user_id == user_id, Meeting.platform == platform,
                Meeting.platform_specific_id == native_meeting_id,
            ).order_by(Meeting.created_at.desc()).limit(1).with_for_update())
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return None
            secret = secrets.token_urlsafe(24)
            gid = secrets.token_hex(8)
            expires_at = (_now() + timedelta(seconds=int(expires_in_sec))).isoformat()
            grant = {"id": gid, "secret_hash": _sha(secret), "mode": mode,
                     "allowed_emails": list(allowed_emails or []), "expires_at": expires_at, "revoked": False}
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            data["share_grants"] = list(data.get("share_grants", [])) + [grant]
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            return {"id": gid, "token": f"{meeting.id}.{secret}", "mode": mode, "expires_at": expires_at}

    async def redeem_transcript_share(self, user_id, user_email, token) -> "Optional[dict]":
        """Redeem a transcript share token (any authenticated user) → grants THIS user subscribe access to
        that meeting's live feed (adds them to ``data.transcript_viewers[]``). Token = ``<meeting_id>.<secret>``.
        Returns {meeting_id, ok} on success, {error} on an invalid/expired/not-allowed grant, or None if the
        token is malformed / the meeting is gone. Cross-user by design — the capability token IS the authz."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        if not token or "." not in token:
            return None
        mid_s, secret = token.split(".", 1)
        try:
            mid = int(mid_s)
        except ValueError:
            return None
        async with self._session_factory() as db:
            meeting = (await db.execute(
                select(Meeting).where(Meeting.id == mid).with_for_update()
            )).scalars().first()
            if not meeting or not isinstance(meeting.data, dict):
                return None
            data = dict(meeting.data)
            h = _sha(secret)
            grant = next((g for g in data.get("share_grants", []) if g.get("secret_hash") == h), None)
            if not grant:
                return {"error": "invalid"}
            err = validate_transcript_grant(grant, user_email)
            if err:
                return {"error": err}
            viewers = list(data.get("transcript_viewers", []))
            if user_id not in viewers:
                viewers.append(user_id)
            data["transcript_viewers"] = viewers
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            return {"meeting_id": mid, "ok": True}

    @asynccontextmanager
    async def transcript_write_lease(self, meeting_id):
        """Hold the shared consent barrier across batch persistence and its live publications."""
        lock_params = {
            "lock_namespace": MEETING_WRITE_LOCK_NAMESPACE,
            "meeting_id": int(meeting_id),
        }
        async with self._session_factory() as db:
            await db.execute(
                self._statement(
                    "SELECT pg_advisory_lock_shared(:lock_namespace, :meeting_id)"
                ),
                lock_params,
            )
            try:
                result = await db.execute(
                    self._statement("SELECT data FROM meetings WHERE id = :meeting_id"),
                    {"meeting_id": int(meeting_id)},
                )
                row = result.mappings().first()
                data = row["data"] if row and isinstance(row["data"], dict) else {}
                if row is None or capture_is_withdrawn(data):
                    raise TranscriptWriteRefused("meeting is not writable")
                yield _RedisTranscriptBatchWriter(self._redis, int(meeting_id))
            finally:
                await db.execute(
                    self._statement(
                        "SELECT pg_advisory_unlock_shared(:lock_namespace, :meeting_id)"
                    ),
                    lock_params,
                )

    async def append_segment(self, meeting_id, segment) -> None:
        # Compatibility seam for callers that produce a single segment. Ingest uses one lease for
        # the complete decoded message so consent cannot be withdrawn between segment writes and
        # their live publication.
        async with self.transcript_write_lease(meeting_id) as writer:
            await writer.append_segments([segment])

    async def upsert_segments(self, meeting_id, segments) -> None:
        """The db-writer's durable sink — UPSERT a batch of flushed segments into ``transcriptions``
        on the segment identity ``(meeting_id, segment_id)`` (the partial unique index
        ``ix_transcription_meeting_segment`` in the admin-api authoritative schema), exactly the
        parent db-writer's ON CONFLICT statement: idempotent, a re-flushed rewrite lands as an
        UPDATE, never a duplicate row."""
        from datetime import datetime as _dt

        rows = []
        for seg in segments:
            sid = seg.get("segment_id")
            if not sid:
                continue  # 0.12 ingest guarantees segment_id; a legacy stray is skipped, not guessed
            try:
                start = float(seg.get("start", seg.get("start_time", 0.0)) or 0.0)
                end = float(seg.get("end", seg.get("end_time", start)) or start)
            except (TypeError, ValueError):
                continue
            if end < start:
                start, end = end, start
            rows.append({
                "mid": int(meeting_id), "start": start, "end": end,
                "text": seg.get("text") or "", "speaker": seg.get("speaker"),
                "lang": seg.get("language"), "uid": seg.get("session_uid"),
                "segid": str(sid), "created": _dt.utcnow(),
            })
        if not rows:
            return
        async with self._session_factory() as db:
            lock_params = {
                "lock_namespace": MEETING_WRITE_LOCK_NAMESPACE,
                "meeting_id": int(meeting_id),
            }
            await db.execute(
                self._statement(
                    "SELECT pg_advisory_xact_lock_shared(:lock_namespace, :meeting_id)"
                ),
                lock_params,
            )
            result = await db.execute(
                self._statement("SELECT data FROM meetings WHERE id = :meeting_id"),
                {"meeting_id": int(meeting_id)},
            )
            meeting = result.mappings().first()
            data = meeting["data"] if meeting and isinstance(meeting["data"], dict) else {}
            if meeting is None or capture_is_withdrawn(data):
                raise TranscriptWriteRefused("meeting is not writable")
            for row in rows:
                await db.execute(
                    self._statement("""
                        INSERT INTO transcriptions (meeting_id, start_time, end_time, text, speaker, language, session_uid, segment_id, created_at)
                        VALUES (:mid, :start, :end, :text, :speaker, :lang, :uid, :segid, :created)
                        ON CONFLICT (meeting_id, segment_id) WHERE segment_id IS NOT NULL
                        DO UPDATE SET text = EXCLUDED.text, speaker = EXCLUDED.speaker,
                                      start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time,
                                      language = EXCLUDED.language, created_at = EXCLUDED.created_at
                    """),
                    row,
                )
            await db.commit()

    async def processed_view_cursor(self, meeting_id, view_id) -> Optional[str]:
        """The ``source_cursor`` of the ``view_id`` view inside ``meeting.data['processed']['views']``
        — the last ``proc:meeting:{id}`` stream entry already durable; the db-writer resumes after it."""
        from sqlalchemy import select

        from .models import Meeting

        async with self._session_factory() as db:
            m = (await db.execute(select(Meeting).where(Meeting.id == int(meeting_id)))).scalars().first()
            if not m or not isinstance(m.data, dict):
                return None
            view = _find_processed_view(m.data, view_id)
            return view.get("source_cursor") if view else None

    async def merge_processed_view(
        self, meeting_id, *, view_id, kind, notes, source_cursor, params=None,
    ) -> None:
        """Persist drained copilot notes into the meeting row's ``data['processed']['views']``
        JSONB (the documented meeting.data home — the same pattern recordings/notes/docs use; NO
        schema change), in the ADDRESSABLE, VERSIONED multi-consumer shape (release DoD):
        the view keyed ``view_id`` is upserted (other views preserved), its ``doc['notes']`` merged
        by note id, ``params`` = the processing metadata APPLIED, ``source_cursor`` = the stream
        position the view reflects. The shared meeting-write barrier is acquired before the row
        lock, matching transcript and recording writers, so durable withdrawal wins against every
        transcript-derived PostgreSQL mutation."""
        async with self._session_factory() as db:
            lock_params = {
                "lock_namespace": MEETING_WRITE_LOCK_NAMESPACE,
                "meeting_id": int(meeting_id),
            }
            await db.execute(
                self._statement(
                    "SELECT pg_advisory_xact_lock_shared(:lock_namespace, :meeting_id)"
                ),
                lock_params,
            )
            result = await db.execute(
                self._statement("SELECT data FROM meetings WHERE id = :meeting_id FOR UPDATE"),
                {"meeting_id": int(meeting_id)},
            )
            row = result.mappings().first()
            data = row["data"] if row and isinstance(row["data"], dict) else {}
            if row is None or capture_is_withdrawn(data):
                raise TranscriptWriteRefused("meeting is not writable")
            updated = _upsert_processed_view(
                data, view_id=view_id, kind=kind, notes=notes,
                source_cursor=source_cursor, params=params,
            )
            await db.execute(
                self._statement(
                    "UPDATE meetings SET data = CAST(:data AS jsonb) WHERE id = :meeting_id"
                ),
                {
                    "meeting_id": int(meeting_id),
                    "data": json.dumps(updated, sort_keys=True),
                },
            )
            await db.commit()

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

    async def set_intent(self, user_id, platform, native_meeting_id, status, scheduled_at=None):
        """Owner-scoped atomic write of the INTENT status (``idle`` / ``scheduled``) onto the
        ``meetings.status`` column under ONE ``SELECT … FOR UPDATE`` row lock. Stamps / clears
        ``meeting.data['scheduled_at']``. NEVER touches the bot FSM."""
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
            prev_status = meeting.status
            prev_at = data.get("scheduled_at")
            new_at = scheduled_at if status == "scheduled" else None
            meeting.status = status
            if status == "scheduled":
                data["scheduled_at"] = new_at
            else:
                data.pop("scheduled_at", None)
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            changed = (prev_status != status) or (prev_at != new_at)
            return {
                "id": meeting.id,
                "user_id": user_id,
                "platform": platform,
                "native_id": native_meeting_id,
                "status": status,
                "scheduled_at": new_at,
                "changed": changed,
            }

    @staticmethod
    def _planned_row(m) -> dict:
        """One meeting ORM row → the ``list_meetings`` dict shape (owner context: shared=False)."""
        return {
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
            "shared": False,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }

    async def create_planned_meeting(self, user_id, *, platform, native_meeting_id,
                                     title=None, scheduled_at=None, meeting_url=None,
                                     workspace_id=None, auto_join=True, calendar_uid=None,
                                     workspace_source=None, attendees=None) -> dict:
        """Insert a PLANNED row (intent status, no bot). Takes the SAME per-user advisory lock as
        ``bot_spawn.create_meeting_guarded`` so planned-create serializes with concurrent spawns
        and calendar sync; the unique partial index remains the DB-level backstop (→ duplicate)."""
        from sqlalchemy import bindparam, select, text
        from sqlalchemy.exc import IntegrityError

        from .models import Meeting

        data: dict = {"auto_join": bool(auto_join)}
        if title:
            data["title"] = title
        if scheduled_at:
            data["scheduled_at"] = scheduled_at
        if meeting_url:
            data["constructed_meeting_url"] = meeting_url
        if workspace_id:
            data["workspace_id"] = workspace_id
            if workspace_source:
                data["workspace_source"] = workspace_source
        if calendar_uid:
            data["calendar_uid"] = calendar_uid
        if attendees:
            data["attendees"] = attendees
        status = "scheduled" if scheduled_at else "idle"

        async with self._session_factory() as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:uid)").bindparams(bindparam("uid", user_id))
            )
            if native_meeting_id is not None:
                dup = (await db.execute(
                    select(Meeting.id).where(
                        Meeting.user_id == user_id,
                        Meeting.platform == platform,
                        Meeting.platform_specific_id == native_meeting_id,
                        Meeting.status.notin_(("completed", "failed")),
                    )
                )).scalars().first()
                if dup is not None:
                    return {"error": "duplicate"}
            m = Meeting(
                user_id=user_id, platform=platform, platform_specific_id=native_meeting_id,
                status=status, data=data,
            )
            db.add(m)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return {"error": "duplicate"}
            await db.refresh(m)
            return self._planned_row(m)

    async def update_planned_meeting(self, user_id, meeting_id, updates) -> "Optional[dict]":
        """ROW-id-addressed PATCH of a planned row (intent status only). ``updates`` carries only
        the keys the caller sent — presence means apply (None clears where documented)."""
        from sqlalchemy import bindparam, select, text
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:uid)").bindparams(bindparam("uid", user_id))
            )
            meeting = (await db.execute(
                select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
                .with_for_update()
            )).scalars().first()
            if meeting is None:
                return None
            if meeting.status not in ("idle", "scheduled"):
                return {"error": "conflict"}
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}

            if "native_meeting_id" in updates:
                new_platform = updates.get("platform") or meeting.platform
                new_native = updates["native_meeting_id"]
                if new_native is not None:
                    dup = (await db.execute(
                        select(Meeting.id).where(
                            Meeting.user_id == user_id,
                            Meeting.platform == new_platform,
                            Meeting.platform_specific_id == new_native,
                            Meeting.status.notin_(("completed", "failed")),
                            Meeting.id != meeting_id,
                        )
                    )).scalars().first()
                    if dup is not None:
                        return {"error": "duplicate"}
                meeting.platform = new_platform
                meeting.platform_specific_id = new_native
            if "constructed_meeting_url" in updates:
                if updates["constructed_meeting_url"]:
                    data["constructed_meeting_url"] = updates["constructed_meeting_url"]
                else:
                    data.pop("constructed_meeting_url", None)
            if "title" in updates:
                if updates["title"]:
                    data["title"] = updates["title"]
                else:
                    data.pop("title", None)
            if "scheduled_at" in updates:
                if updates["scheduled_at"]:
                    data["scheduled_at"] = updates["scheduled_at"]
                    meeting.status = "scheduled"
                else:
                    data.pop("scheduled_at", None)
                    meeting.status = "idle"
            if "workspace_id" in updates:
                if updates["workspace_id"]:
                    # an explicit bind is the USER's choice — it also lifts any series tombstone
                    data["workspace_id"] = updates["workspace_id"]
                    data["workspace_source"] = "user"
                    data.pop("workspace_unbound", None)
                else:
                    # explicit unbind tombstones the series row so sync never re-inherits it
                    data.pop("workspace_id", None)
                    data.pop("workspace_source", None)
                    if (data.get("calendar_uid")):
                        data["workspace_unbound"] = True
            if "attendees" in updates:
                if updates["attendees"]:
                    data["attendees"] = updates["attendees"]
                else:
                    data.pop("attendees", None)
            if "auto_join" in updates:
                data["auto_join"] = bool(updates["auto_join"])
            if "calendar_uid" in updates:
                if updates["calendar_uid"]:
                    data["calendar_uid"] = updates["calendar_uid"]
                else:
                    data.pop("calendar_uid", None)

            meeting.data = data
            flag_modified(meeting, "data")
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return {"error": "duplicate"}
            await db.refresh(meeting)
            return self._planned_row(meeting)

    async def delete_planned_meeting(self, user_id, meeting_id) -> "Optional[bool]":
        from sqlalchemy import select

        from .models import Meeting

        async with self._session_factory() as db:
            meeting = (await db.execute(
                select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
                .with_for_update()
            )).scalars().first()
            if meeting is None:
                return None
            if meeting.status not in ("idle", "scheduled"):
                return False
            await db.delete(meeting)
            await db.commit()
            return True


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

    async def xadd(self, stream, payload):
        """Append one entry to a redis STREAM under the ``payload`` field — the native transcript feed
        ``tc:meeting:{native}`` the collector owns as single writer (P23)."""
        return await self._client.xadd(stream, {"payload": json.dumps(payload)})


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
