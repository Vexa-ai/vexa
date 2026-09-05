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

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from .ports import RedisBus, TranscriptStore

log = logging.getLogger("meeting_api.collector.adapters")


def _decode_claimed(resp) -> "list[tuple[str, dict]]":
    """Normalize an XAUTOCLAIM response to ``[(message_id, fields), ...]`` (#636).

    redis-py returns ``[cursor, claimed]`` (older) or ``[cursor, claimed, deleted]`` (Redis 7+),
    where ``claimed`` is ``[(id, {field: value}), ...]``. Ids/keys/values are decoded from bytes so
    the drained fields match ``read_segments``' shape (``ingest`` reads ``fields['payload']``)."""
    if not resp or len(resp) < 2:
        return []
    claimed = resp[1] or []
    out: list[tuple[str, dict]] = []
    for message_id, fields in claimed:
        mid = message_id.decode() if isinstance(message_id, bytes) else message_id
        decoded = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in (fields or {}).items()
        }
        out.append((mid, decoded))
    return out


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_share_grant(mode: str, allowed_emails, expires_in_sec: int) -> "tuple[dict, str]":
    """The share-grant record and its one-time secret. ONE definition, because there are now two
    ways to address the meeting it is minted on — the (platform, native) pair and the ROW id — and
    two copies of the hash-at-rest shape would be two places to get the hashing wrong."""
    from datetime import timedelta

    secret = secrets.token_urlsafe(24)
    grant = {
        "id": secrets.token_hex(8),
        "secret_hash": _sha(secret),
        "mode": mode,
        "allowed_emails": list(allowed_emails or []),
        "expires_at": (_now() + timedelta(seconds=int(expires_in_sec))).isoformat(),
        "revoked": False,
    }
    return grant, secret


def _iso_utc(dt) -> Optional[str]:
    """UTC ISO-8601 (``…Z``) for a naive-or-aware datetime. The meeting time columns are naive but
    hold UTC (the DB session is UTC); a bare ``isoformat()`` is zone-less, so a browser's ``new Date()``
    parses it as LOCAL and renders it offset by the viewer's UTC offset. Stamping UTC fixes that."""
    if dt is None:
        return None
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


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


# A relative in-meeting offset never approaches this; anything at/above is an absolute epoch.
_EPOCH_THRESHOLD_S = 1_000_000_000  # ~2001-09-09


def _fill_absolute_times(segments: list, base) -> None:
    """Fill each segment's ``absolute_start_time``/``absolute_end_time`` (in place) when a producer
    didn't supply them, so a renderer that keys on absolute time shows the segment.

    ``start``/``end`` carry TWO semantics by producer: a RELATIVE offset into the meeting (the carve —
    small seconds-since-start, bounded by the 4h ceiling) OR an ABSOLUTE epoch-seconds wall-clock (the
    live pipeline — ~1.78e9). Doing ``base + start`` unconditionally treated an absolute epoch as a
    relative offset and added it to ``base`` → year ~2083 (2026 + 56.5 years). Discriminate by
    magnitude: at/above ``_EPOCH_THRESHOLD_S`` the value is already absolute — use it directly; below,
    anchor the relative offset to ``base`` (the meeting start)."""
    from datetime import timedelta

    for s in segments:
        if s.get("absolute_start_time") or s.get("start") is None:
            continue
        try:
            st = float(s["start"])
            en = float(s["end"]) if s.get("end") is not None else st
        except (TypeError, ValueError):
            continue
        if st >= _EPOCH_THRESHOLD_S:
            s["absolute_start_time"] = _iso_utc(datetime.fromtimestamp(st, timezone.utc))
            s["absolute_end_time"] = _iso_utc(datetime.fromtimestamp(en, timezone.utc))
        elif base is not None:
            s["absolute_start_time"] = _iso_utc(base + timedelta(seconds=st))
            s["absolute_end_time"] = _iso_utc(base + timedelta(seconds=en))


def _segment_to_api(seg: dict) -> dict:
    """Map a stored/Redis segment to an api.v1 ``TranscriptionSegment`` (start/end/text/language
    required; the optional fields ride along)."""
    out = {
        "start": seg.get("start", seg.get("start_time", 0.0)),
        "end": seg.get("end", seg.get("end_time", 0.0)),
        "text": seg.get("text", ""),
        "language": seg.get("language"),
    }
    for k in ("speaker", "speaker_key", "completed", "segment_id", "source", "absolute_start_time", "absolute_end_time", "created_at"):
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
        # numeric meeting_id → (native_meeting_id, platform). The id→native map is immutable for a
        # meeting row, so cache it forever once resolved (bounded by the live meeting set).
        self._native_cache: dict[int, tuple[str, str]] = {}

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

    # #508: the transcript doc is built in TWO phases so the (possibly slow) Redis merge never
    # happens while a Postgres backend sits idle-in-transaction. Phase 1 (_transcript_pg_part) runs
    # INSIDE the session: it does all DB work and snapshots the row fields to plain values. The
    # caller then EXITS the session block — ending the transaction and returning the connection to
    # the pool — and only THEN calls phase 2 (_merge_live_segments), which awaits Redis with no DB
    # session in scope. The response is byte-identical to the old single-pass build; only the
    # transaction scope changes. (See C2's tx-scope gate, which enforces this shape for good.)

    async def _transcript_pg_part(self, db, meeting) -> "tuple[dict, dict, list]":
        """DB-ONLY half: SELECT the persisted ``transcriptions`` for this row and SNAPSHOT every
        meeting-row field the response needs into plain values — all while the session is live.
        Returns ``(snap, seg_by_id, order)``. Nothing here awaits a non-DB backend, so the caller's
        transaction stays scoped to Postgres statements only (#508).

        The row fields are copied to a plain dict on purpose: after the session closes, touching an
        expired ORM attribute raises ``MissingGreenlet`` — the same reason ``bot_spawn/adapters.py``
        snapshots before returning (``:192-194``)."""
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
        # Snapshot every field the response body reads, INSIDE the live session (see docstring).
        snap = {
            "id": meeting.id,
            "platform": meeting.platform,
            "platform_specific_id": meeting.platform_specific_id,
            "status": meeting.status,
            "start_time": meeting.start_time,
            "end_time": meeting.end_time,
            "created_at": meeting.created_at,
            "data": data,
        }
        return snap, seg_by_id, order

    async def _merge_live_segments(
        self, pg: "tuple[dict, dict, list]", *, viewer_is_owner: bool,
    ) -> dict:
        """POST-SESSION half: merge the LIVE Redis in-flight hash, sort, derive absolute times, and
        assemble the api.v1 ``TranscriptionResponse`` dict. NO database session is open here — this
        is where the (possibly slow) Redis await happens, so it can never pin a pooled connection or
        hold a snapshot/transaction open (the #508 fix).

        This is a RESPONSE edge, so ``data`` goes through ``project_response_data``: calendar sources
        reduced to their identity + policy keys (the raw ICS event snapshot is sweep state, not anyone's
        transcript), credential material dropped for everyone, and the owner's private configuration
        dropped for everyone but the owner — this read authorizes a transcript-share recipient and a
        bound-workspace member too, not only the owner.

        ``viewer_is_owner`` is REQUIRED (no default) because both callers already know it: the
        native-keyed read constrains ``Meeting.user_id == user_id`` in SQL, and the by-id read
        evaluates an explicit owner branch inside its authorization check. Passing the decision down
        beats re-deriving it here, where the caller's ``user_id`` is not even in scope."""
        from .projection import project_response_data

        snap, seg_by_id, order = pg
        data = snap["data"]
        # Merge the LIVE Redis hash of in-flight segments (``meeting:{id}:segments``) — the source
        # of truth before/until the db-writer flush. The carve had dropped this merge, so a transcript
        # whose segments are still only in Redis (every short/just-finished meeting) read as EMPTY.
        if self._redis is not None:
            try:
                raw = await self._redis.hgetall(f"meeting:{snap['id']}:segments")
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
        # (use-vexa-websocket.ts: `if (!seg.absolute_start_time) continue`). Derive it when a producer
        # didn't supply it, so the historical transcript renders. See `_fill_absolute_times`.
        _fill_absolute_times(segments, snap["start_time"] or snap["created_at"])
        return {
            "id": snap["id"],
            "platform": snap["platform"],
            "native_meeting_id": snap["platform_specific_id"],
            "constructed_meeting_url": (data.get("constructed_meeting_url")),
            "status": snap["status"],
            "start_time": _iso_utc(snap["start_time"]),
            "end_time": _iso_utc(snap["end_time"]),
            "recordings": data.get("recordings", []),
            "notes": data.get("notes"),
            "data": project_response_data(data, viewer_is_owner=viewer_is_owner),
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
            data = meeting.data if isinstance(meeting.data, dict) else {}
            deletion_state = data.get("artifact_deletion") or {}
            if deletion_state and deletion_state.get("state", "completed") == "completed":
                return None
            pg = await self._transcript_pg_part(db, meeting)
        # Session closed (transaction ended, connection returned to pool) BEFORE the Redis merge (#508).
        # The SELECT above constrains ``Meeting.user_id == user_id``, so a row reached through this
        # path is by construction the caller's own — the native-keyed read has no share branch.
        return await self._merge_live_segments(pg, viewer_is_owner=True)

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
            deletion_state = data.get("artifact_deletion") or {}
            if deletion_state and deletion_state.get("state", "completed") == "completed":
                return None
            is_owner = meeting.user_id == user_id                                   # (a) owner
            authorized = (
                is_owner
                or user_id in (data.get("transcript_viewers") or [])                # (c) transcript-share
                or (bool(member_workspaces) and data.get("workspace_id") in member_workspaces)  # (b) bound ws member
            )
            if not authorized:
                return None
            pg = await self._transcript_pg_part(db, meeting)
        # Session closed (transaction ended, connection returned to pool) BEFORE the Redis merge (#508).
        # The response projection reuses branch (a) above — the SAME decision that authorized this
        # read decides which tier of the blob it may carry, so the two can never disagree.
        return await self._merge_live_segments(pg, viewer_is_owner=is_owner)

    async def list_meetings(self, user_id, *, status=None, platform=None, limit=None, offset=None,
                            member_workspaces=None, list_view=False, meeting_id=None, slim=False,
                            metadata_filter=None):
        from sqlalchemy import cast, func, literal, select, text, union_all
        from sqlalchemy.dialects.postgresql import JSONB

        from .models import Meeting
        from .projection import DEFAULT_LIST_LIMIT, LIST_PIN_STATUSES, project_list_data

        async with self._session_factory() as db:
            # ACCESS = owner OR transcript-share viewer OR member of the bound workspace. Shared meetings
            # (owned by someone else) surface in the caller's list so a share recipient can find + open them.
            #
            # #800: the branches are UNIONed, never OR-ed. A single `WHERE a OR b` plans as a backward
            # walk of the created_at index with the OR as a Filter — for a caller with few/old meetings
            # that scans most of the table (100s+ per call under production load). Each branch below is
            # independently index-scannable (owner → ix_meeting_user_event_order /
            # ix_meeting_user_created_at, viewer → ix_meeting_transcript_viewers_gin, workspace →
            # ix_meeting_workspace_event_order / ix_meeting_workspace_created_at — event-order for
            # the list_view sort, created_at for internal enumeration) with its own top-N
            # ORDER BY/LIMIT; the outer query dedups the merged ids and re-orders.
            access = [
                Meeting.user_id == user_id,
                cast(Meeting.data["transcript_viewers"], JSONB).op("@>")(func.to_jsonb(user_id)),
            ]
            if member_workspaces:
                access.append(Meeting.data["workspace_id"].astext.in_(list(member_workspaces)))

            if list_view:
                fetch_bound = (offset or 0) + (limit if limit is not None else DEFAULT_LIST_LIMIT) + 1
            else:
                fetch_bound = ((offset or 0) + limit) if limit else None

            # #1222: the USER-FACING list orders by (non-terminal pin, MEETING EVENT time), not
            # row-creation time — a calendar-managed row is created at import time, so created_at
            # buried the meeting that was live right now under every row created since the import.
            # The expressions are verbatim the ones in ix_meeting_user_event_order /
            # ix_meeting_workspace_event_order (sessions/models.py): the branch top-N must stay an
            # index walk (#800), and the planner only substitutes an expression index when the
            # ORDER BY expression matches it structurally. `meeting_event_time()` is the IMMUTABLE
            # SQL wrapper for COALESCE(data.scheduled_at, start_time, created_at) — created by the
            # admin-api schema sync (MIGRATION-0005). Semantics mirrored by the fake via
            # projection.list_order_key. Internal enumeration (get-by-id filter, /bots/status,
            # calendar sync) keeps created_at DESC — _resolve_owned_native documents "newest owned
            # row" against exactly that order.
            _pin_sql = "status IN ({})".format(
                ", ".join(f"'{s}'" for s in sorted(LIST_PIN_STATUSES)))
            event_order = (
                text(f"({_pin_sql}) DESC"),
                text("meeting_event_time(data, start_time, created_at) DESC"),
                Meeting.id.desc(),
            )

            def _branch(cond):
                s = select(Meeting.id).where(cond)
                if status:
                    # A sequence selects a SET of statuses in SQL (`/bots/status` wants the five
                    # non-terminal ones). Filtering here instead of in Python is the difference
                    # between reading a caller's handful of live meetings and reading their entire
                    # history — see the `slim=` note below (#803).
                    s = s.where(
                        Meeting.status.in_(tuple(status))
                        if isinstance(status, (list, tuple, set, frozenset))
                        else Meeting.status == status
                    )
                if meeting_id is not None:
                    # Detail-by-id: constrain in SQL rather than enumerating the account and
                    # filtering in Python. The access union still decides WHETHER the caller may
                    # see it, so ownership/share semantics are unchanged.
                    s = s.where(Meeting.id == meeting_id)
                if platform:
                    s = s.where(Meeting.platform == platform)
                if metadata_filter:
                    # JSONB containment on the whole `data` blob, nesting the caller's filter under
                    # `metadata` — `data @> '{"metadata": {...}}'`. Written against `data` (not
                    # `data->'metadata'`) deliberately: THAT is the shape `ix_meeting_data_gin`
                    # indexes, so this stays an index scan instead of degrading to a seq scan the
                    # moment an account has history. Filtering in SQL rather than in Python is also
                    # the difference between "the meetings tagged acme-42" and "the tagged ones on
                    # the page you happened to fetch" — the latter is a wrong answer, not a slow one.
                    s = s.where(
                        cast(Meeting.data, JSONB).op("@>")(
                            cast(literal(json.dumps({"metadata": metadata_filter})), JSONB)
                        )
                    )
                if fetch_bound is not None:
                    # ORDER BY inside a compound member is only meaningful (and only kept by
                    # the compiler) together with LIMIT; an unbounded branch returns its full
                    # set, so the outer ORDER BY alone decides. The branch MUST pre-limit by the
                    # SAME key the outer sort uses — a created_at top-N under the event-time sort
                    # would silently drop the very rows the sort exists to surface (#1222).
                    branch_order = event_order if list_view else (Meeting.created_at.desc(),)
                    s = s.order_by(*branch_order).limit(fetch_bound)
                return s

            ids = union_all(*[_branch(c) for c in access]).subquery()
            stmt = (
                select(Meeting)
                .where(Meeting.id.in_(select(ids.c.id)))
                .order_by(*(event_order if list_view else (Meeting.created_at.desc(),)))
            )
            if list_view:
                # #584: the paginated, slim list-view path (GET /bots, GET /meetings). Bound the response
                # with a default page size (an explicit `limit` still wins) and over-fetch one row past
                # the page to compute `has_more` without a second COUNT query.
                effective_limit = limit if limit is not None else DEFAULT_LIST_LIMIT
                if offset:
                    stmt = stmt.offset(offset)
                stmt = stmt.limit(effective_limit + 1)
                rows = (await db.execute(stmt)).scalars().all()
                has_more = len(rows) > effective_limit
                rows = rows[:effective_limit]
            else:
                # Internal enumeration (get-by-id filter, /bots/status, calendar sync): unchanged —
                # explicit `limit` only, NO default cap, full `data` retained below.
                if limit:
                    stmt = stmt.limit(limit)
                if offset:
                    stmt = stmt.offset(offset)
                rows = (await db.execute(stmt)).scalars().all()
                has_more = False

            def _row(m):
                # The ONE ownership decision this row makes. `shared` (the api.v1 field) and the
                # response projection's viewer tier both read it, so a row can never claim to be the
                # caller's while being projected as a stranger's, or the reverse.
                is_owner = m.user_id == user_id
                row = {
                    "id": m.id,
                    "user_id": m.user_id,
                    "platform": m.platform,
                    "native_meeting_id": m.platform_specific_id,
                    "constructed_meeting_url": (m.data or {}).get("constructed_meeting_url")
                    if isinstance(m.data, dict) else None,
                    "status": m.status,
                    "bot_container_id": m.bot_container_id,
                    "start_time": _iso_utc(m.start_time),
                    "end_time": _iso_utc(m.end_time),
                    # api.v1 MeetingResponse declares these at top level; the values live in `data`
                    # (hoisted the same way as `_meeting_projection_from_row` in app.py).
                    "completion_reason": (m.data or {}).get("completion_reason") if isinstance(m.data, dict) else None,
                    "failure_stage": (m.data or {}).get("failure_stage") if isinstance(m.data, dict) else None,
                    "shared": not is_owner,   # surfaced via a share/membership, not owned by the caller
                    "created_at": _iso_utc(m.created_at),
                    "updated_at": _iso_utc(m.updated_at),
                    # #584: the LIST drops the heavy detail keys (speaker_events/bot_logs/recordings/… —
                    # the 4.6 MB / event-loop-wedge cause) but keeps the light metadata it renders
                    # (title/docs/flags).
                    # #803: `slim` extends the SAME projection to a non-list caller that renders none
                    # of the heavy keys either. `/bots/status` powers a running-bots badge, yet it
                    # materialized every byte of `data` — 180 MB for one production account, 144 MB of
                    # it `bot_logs` that no endpoint renders. Four concurrent polls demanded ~740 MB
                    # transiently and OOM-killed the pod. Only callers that genuinely need full `data`
                    # (the detail view, calendar sync, reconciliation) leave both flags off — and
                    # those callers project at their own response edge (app.py) or are internal.
                    #
                    # #1243 follow-up: the projection is VIEWER-AWARE. The owner keeps their own
                    # webhook config here, which is where a 0.10 client reads it back
                    # (`test_10_user_webhook_config_flow`); a share/workspace reader does not.
                    "data": project_list_data(m.data, viewer_is_owner=is_owner) if (list_view or slim)
                    else (m.data if isinstance(m.data, dict) else {}),
                }
                return row

            result = [_row(m) for m in rows]
            return (result, has_more) if list_view else result

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

    async def get_meeting_participants(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        """OWNER-scoped (``ports.TranscriptStore.get_meeting_participants``). ONE session, two reads:
        the newest owned row's ``data['attendees']`` (the calendar invitation's ATTENDEE lines), and
        DISTINCT ``transcriptions.speaker`` for that row ordered by FIRST utterance.

        The speaker read is a grouped aggregate, NOT a segment fetch: a long meeting has thousands of
        rows and this endpoint wants at most a few dozen names, so ``GROUP BY speaker`` keeps the
        response bounded by the cast rather than by the transcript's length (the #584 lesson — never
        let a per-meeting response grow with the meeting)."""
        from sqlalchemy import func, select

        from .models import Meeting, Transcription

        async with self._session_factory() as db:
            meeting = (await db.execute(
                select(Meeting).where(
                    Meeting.user_id == user_id,
                    Meeting.platform == platform,
                    Meeting.platform_specific_id == native_meeting_id,
                ).order_by(Meeting.created_at.desc()).limit(1)
            )).scalars().first()
            if meeting is None:
                return None  # → 404. NOT an empty roster: the caller owns no such meeting.
            data = meeting.data if isinstance(meeting.data, dict) else {}
            rows = (await db.execute(
                select(Transcription.speaker, func.min(Transcription.start_time).label("first_at"))
                .where(
                    Transcription.meeting_id == meeting.id,
                    Transcription.speaker.isnot(None),
                )
                .group_by(Transcription.speaker)
                .order_by(func.min(Transcription.start_time))
            )).all()
            attendees = data.get("attendees")
            return {
                "meeting_id": meeting.id,
                "invited": attendees if isinstance(attendees, list) else [],
                "speakers": [r[0] for r in rows if isinstance(r[0], str) and r[0].strip()],
            }

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

    async def _mint_share_on(self, stmt, *, mode, allowed_emails, expires_in_sec) -> "Optional[dict]":
        """Mint a grant onto whichever ONE row ``stmt`` selects. The two public mints differ only in
        how they address the meeting; everything after the row is identical."""
        from sqlalchemy.orm.attributes import flag_modified

        async with self._session_factory() as db:
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return None
            grant, secret = _build_share_grant(mode, allowed_emails, expires_in_sec)
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            data["share_grants"] = list(data.get("share_grants", [])) + [grant]
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            return {"id": grant["id"], "token": f"{meeting.id}.{secret}",
                    "mode": mode, "expires_at": grant["expires_at"]}

    async def mint_transcript_share(self, user_id, platform, native_meeting_id, *,
                                    mode="open", allowed_emails=None, expires_in_sec=86400) -> "Optional[dict]":
        """OWNER-scoped: mint an INDEPENDENT transcript share grant (no workspace needed). Stored in
        ``data.share_grants[]`` as {id, secret_hash, mode, allowed_emails, expires_at, revoked} — only the
        HASH, never the token. Returns {id, token, ...} ONCE (token = ``<meeting_id>.<secret>`` so redeem
        resolves the meeting). None if the caller owns no such meeting.

        Addressed by the (platform, native) PAIR, which is not a reliable identity: a row planned from
        an invite whose url no platform matched carries ``platform='unknown'`` and an EMPTY
        ``platform_specific_id``, so no pair addresses it at all (meeting 97, 2026-09-02 — every
        attendee mail for it shipped with no token). Prefer ``mint_transcript_share_by_id``; this one
        stays because 0.10 clients and the ``/transcripts/{platform}/{native}/share`` alias call it."""
        from sqlalchemy import select

        from .models import Meeting

        return await self._mint_share_on(
            select(Meeting).where(
                Meeting.user_id == user_id, Meeting.platform == platform,
                Meeting.platform_specific_id == native_meeting_id,
            ).order_by(Meeting.created_at.desc()).limit(1).with_for_update(),
            mode=mode, allowed_emails=allowed_emails, expires_in_sec=expires_in_sec)

    async def mint_transcript_share_by_id(self, user_id, meeting_id, *,
                                          mode="open", allowed_emails=None, expires_in_sec=86400) -> "Optional[dict]":
        """OWNER-scoped mint addressed by the ROW's primary key — the identity that always exists.

        Same grant, same hash-at-rest, same one-time token shape as the pair-keyed mint above; the
        only difference is the WHERE. Scoped to ``user_id`` so a row the caller does not own is
        indistinguishable from one that does not exist (404 either way) — minting a capability is an
        owner act, and a share route that leaked existence would be worse than one that leaked
        nothing. No ``order_by``: a primary key selects exactly one row or none."""
        from sqlalchemy import select

        from .models import Meeting

        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            return None
        return await self._mint_share_on(
            select(Meeting).where(
                Meeting.id == mid, Meeting.user_id == user_id,
            ).limit(1).with_for_update(),
            mode=mode, allowed_emails=allowed_emails, expires_in_sec=expires_in_sec)

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

    async def append_segment(self, meeting_id, segment) -> None:
        # Live segments land in the Redis hash (``meeting:{id}:segments``), flushed to Postgres by
        # the background db-writer (``collector/db_writer.py``) — exactly the parent's
        # persistence-only path (0.10 ``processors.py``): the same pipeline SADDs the meeting into
        # ``active_meetings`` (the db-writer's sweep set) and re-arms the hash TTL, so an abandoned
        # hash cannot linger forever once its segments were flushed.
        if self._redis is None:
            return
        from .db_writer import ACTIVE_MEETINGS_KEY, segments_hash_key

        hash_key = segments_hash_key(meeting_id)
        ttl = int(os.environ.get("REDIS_SEGMENT_TTL", "3600"))
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.sadd(ACTIVE_MEETINGS_KEY, str(meeting_id))
            pipe.hset(hash_key, segment["segment_id"], json.dumps(segment))
            pipe.expire(hash_key, ttl)
            await pipe.execute()

    async def delete_segments(self, meeting_id, segment_ids) -> None:
        # Retraction: withdraw superseded/over-extended pending drafts by segment id. Two legs mirror the
        # append path — HDEL the live hash so an UN-flushed draft never reaches Postgres, and DELETE any
        # rows the db-writer already flushed. Keyed on (meeting_id, segment_id). Idempotent.
        ids = [str(s) for s in (segment_ids or []) if s]
        if not ids:
            return
        # Leg 1: drop from the redis flush hash (before the db-writer persists an un-flushed draft).
        if self._redis is not None:
            from .db_writer import segments_hash_key
            try:
                await self._redis.hdel(segments_hash_key(meeting_id), *ids)
            except Exception:  # noqa: BLE001 — best-effort; the DB delete is the durable backstop
                pass
        # Leg 2: delete any already-flushed rows.
        from sqlalchemy import bindparam
        from sqlalchemy import text as sql_text

        stmt = sql_text(
            "DELETE FROM transcriptions WHERE meeting_id = :mid AND segment_id IN :sids"
        ).bindparams(bindparam("sids", expanding=True))
        async with self._session_factory() as db:
            await db.execute(stmt, {"mid": int(meeting_id), "sids": ids})
            await db.commit()

    async def upsert_segments(self, meeting_id, segments) -> None:
        """The db-writer's durable sink — UPSERT a batch of flushed segments into ``transcriptions``
        on the segment identity ``(meeting_id, segment_id)`` (the partial unique index
        ``ix_transcription_meeting_segment`` in the admin-api authoritative schema), exactly the
        parent db-writer's ON CONFLICT statement: idempotent, a re-flushed rewrite lands as an
        UPDATE, never a duplicate row."""
        from datetime import datetime as _dt

        from sqlalchemy import text as sql_text  # lazy: not needed for the in-memory fakes

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
            for row in rows:
                await db.execute(
                    sql_text("""
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
        position the view reflects. ONE ``SELECT … FOR UPDATE`` row lock."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = select(Meeting).where(Meeting.id == int(meeting_id)).with_for_update()
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            meeting.data = _upsert_processed_view(
                data, view_id=view_id, kind=kind, notes=notes,
                source_cursor=source_cursor, params=params,
            )
            flag_modified(meeting, "data")
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
        position the view reflects. ONE ``SELECT … FOR UPDATE`` row lock."""
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            stmt = select(Meeting).where(Meeting.id == int(meeting_id)).with_for_update()
            meeting = (await db.execute(stmt)).scalars().first()
            if not meeting:
                return
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            meeting.data = _upsert_processed_view(
                data, view_id=view_id, kind=kind, notes=notes,
                source_cursor=source_cursor, params=params,
            )
            flag_modified(meeting, "data")
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
            "start_time": _iso_utc(m.start_time),
            "end_time": _iso_utc(m.end_time),
            "data": m.data if isinstance(m.data, dict) else {},
            "shared": False,
            "created_at": _iso_utc(m.created_at),
            "updated_at": _iso_utc(m.updated_at),
        }

    async def create_planned_meeting(self, user_id, *, platform, native_meeting_id,
                                     title=None, scheduled_at=None, meeting_url=None,
                                     workspace_id=None, auto_join=True, calendar_uid=None,
                                     calendar_source=None,
                                     workspace_source=None, attendees=None,
                                     auto_join_last_attempt=None,
                                     auto_join_error=None) -> dict:
        """Insert a PLANNED row (intent status, no bot). Takes the SAME per-user advisory lock as
        ``bot_spawn.create_meeting_guarded`` so planned-create serializes with concurrent spawns
        and calendar sync; the unique partial index remains the DB-level backstop (→ duplicate).

        ``auto_join_last_attempt``/``auto_join_error`` seed the row with a backoff already earned
        elsewhere — calendar sync passes them when this row replaces a terminal one the auto-join
        sweep already dispatched for, so the replacement is not due the instant it exists. Written
        in the INSERT, not patched after, so no sweep tick can see the row without them."""
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
        if calendar_source:
            data["calendar_sources"] = [dict(calendar_source)]
            data["calendar_connection_id"] = calendar_source["id"]
            data["calendar_name"] = calendar_source.get("name") or "Calendar"
            data["calendar_managed"] = True
        if attendees:
            data["attendees"] = attendees
        if auto_join_last_attempt:
            data["auto_join_last_attempt"] = auto_join_last_attempt
        if auto_join_error:
            data["auto_join_error"] = auto_join_error
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

    async def attach_calendar_source(self, user_id, meeting_id, *, calendar_uid,
                                     calendar_sources=None) -> "Optional[dict]":
        """Stamp calendar IDENTITY onto a row in ANY status (the live-row adoption path).

        Deliberately narrower than ``update_planned_meeting``, which refuses an FSM-owned row: an
        imported event whose meeting is already live must attach to THAT row rather than create a
        sibling the auto-join sweep would send a second bot for. Identity keys only — status,
        ``auto_join``, ``auto_join_user_set``, ``calendar_managed`` and ``scheduled_at`` are never
        written here, so adopting a live row can never re-arm or re-dispatch it."""
        from sqlalchemy import bindparam, select, text
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
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            if calendar_uid:
                data["calendar_uid"] = calendar_uid
            if calendar_sources:
                data["calendar_sources"] = [dict(s) for s in calendar_sources]
                primary = calendar_sources[0]
                data["calendar_connection_id"] = primary.get("id")
                data["calendar_name"] = primary.get("name") or "Calendar"
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()
            await db.refresh(meeting)
            return self._planned_row(meeting)

    # The text-search config, used for BOTH the index expression and every query. These MUST be
    # the same string: an index on to_tsvector('english', text) is invisible to a query that says
    # to_tsvector('simple', text), and the failure is silent — correct answers, seq scan, no error.
    FTS_CONFIG = "english"

    async def search_transcripts(self, user_id, query, *, limit=20, offset=0,
                                 platform=None, native_meeting_id=None,
                                 meeting_db_id=None) -> list[dict]:
        """Owner-scoped FTS over transcript segments (see ports.search_transcripts).

        Measured on 210k segments across two tenants (dogfood, 2026-08-29): a rare term took
        914ms unindexed for a 400-meeting tenant and 0.108ms with the GIN index — and 45.7ms →
        0.126ms even for a 24-meeting one, because the dominant cost is computing to_tsvector()
        per row at query time, not finding the rows. The index is part of the feature, not a
        later optimisation.
        """
        from sqlalchemy import text as sql_text

        q = (query or "").strip()
        if not q:
            return []

        cfg = self.FTS_CONFIG
        sql = sql_text(f"""
            SELECT t.id                AS segment_row_id,
                   -- `meeting_db_id`, never `meeting_id`: the INT row id must not travel
                   -- under the name that means the platform's STRING id on every other
                   -- tool. Emitting it as `meeting_id` is the regression this branch's
                   -- identity gate exists to stop.
                   t.meeting_id        AS meeting_db_id,
                   m.platform          AS platform,
                   m.platform_specific_id AS native_meeting_id,
                   t.start_time        AS start,
                   t.end_time          AS "end",
                   t.speaker           AS speaker,
                   t.language          AS language,
                   ts_rank_cd(to_tsvector('{cfg}', t.text), qq) AS rank,
                   ts_headline('{cfg}', t.text, qq,
                       'StartSel=<mark>,StopSel=</mark>,MaxWords=24,MinWords=8,MaxFragments=2') AS snippet,
                   t.text              AS text
            FROM transcriptions t
            JOIN meetings m ON m.id = t.meeting_id,
                 websearch_to_tsquery('{cfg}', :q) AS qq
            WHERE m.user_id = :uid
              AND to_tsvector('{cfg}', t.text) @@ qq
              -- Explicit casts: asyncpg cannot infer a bind parameter's type when it appears
              -- only in `IS NULL` ("could not determine data type of parameter $3"), so an
              -- optional filter must state its own type.
              AND (CAST(:platform AS text) IS NULL OR m.platform = CAST(:platform AS text))
              -- The EXACT row wins over the room code: a Google Meet code names a ROOM and
              -- every session ever held on that link answers to it, so a caller who supplied
              -- both is asking about one meeting. Same explicit-cast rule as the filters above.
              AND (CAST(:mid AS bigint) IS NULL OR t.meeting_id = CAST(:mid AS bigint))
              AND (CAST(:mid AS bigint) IS NOT NULL
                   OR CAST(:native AS text) IS NULL
                   OR m.platform_specific_id = CAST(:native AS text))
            ORDER BY rank DESC, t.meeting_id DESC, t.start_time ASC
            LIMIT :lim OFFSET :off
        """)
        async with self._session_factory() as db:
            rows = (await db.execute(sql, {
                "q": q, "uid": user_id, "platform": platform, "native": native_meeting_id,
                "mid": meeting_db_id,
                "lim": max(1, min(int(limit or 20), 100)), "off": max(0, int(offset or 0)),
            })).mappings().all()
        return [dict(r) for r in rows]

    async def ensure_fts_index(self) -> dict:
        """Build the transcript FTS index CONCURRENTLY, idempotently, out of band.

        Deliberately NOT part of ``_sync_indexes``: that wraps each index in a SAVEPOINT, and
        ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block. It also must not run
        in-band at boot — a plain CREATE INDEX takes ACCESS EXCLUSIVE on ``transcriptions``, the
        highest-row-count table, which would stall startup for as long as the build takes.

        Safe to call on every boot BECAUSE SEARCH WORKS WITHOUT IT: a missing or half-built index
        means a slower query, never a wrong answer and never a failed request. That is what keeps
        this off the deploy's critical path — unlike ``meeting_event_time`` (MIGRATION-0005),
        whose absence makes every list request fail.

        Handles the one real trap: a failed CONCURRENTLY build leaves an INVALID index behind that
        Postgres silently never uses. We detect it via ``pg_index.indisvalid``, drop it, and let
        the next call rebuild.
        """
        from sqlalchemy import text as sql_text

        name = "ix_transcription_text_fts"
        # AUTOCOMMIT: CREATE/DROP INDEX CONCURRENTLY cannot run in a transaction block.
        engine = self._session_factory.kw["bind"] if hasattr(self._session_factory, "kw") else None
        engine = engine or getattr(self, "_engine", None)
        if engine is None:
            return {"status": "skipped", "reason": "no engine handle"}

        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            state = (await conn.execute(sql_text(
                "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :n"), {"n": name})).scalar()
            if state is True:
                return {"status": "present", "index": name}
            if state is False:
                # A previous CONCURRENTLY build failed. The leftover is INVALID and unusable —
                # Postgres will not error on it, it will simply never use it. Drop and rebuild.
                await conn.execute(sql_text(f"DROP INDEX CONCURRENTLY IF EXISTS {name}"))
            await conn.execute(sql_text(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON transcriptions "
                f"USING gin (to_tsvector('{self.FTS_CONFIG}', text))"))
            return {"status": "created", "index": name}

    async def annotate_meeting(self, user_id, meeting_id, *, title=None,
                               metadata=None) -> "Optional[dict]":
        """Caller-owned annotations on a row in ANY status (see ports.annotate_meeting).

        Modelled on ``attach_calendar_source``, not on ``update_planned_meeting``: nothing written
        here is read by the dispatch pipeline, so there is no FSM to fight and no status check."""
        from sqlalchemy import bindparam, select, text
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

            # `title` lives in the data blob, NOT as a column — same place update_planned_meeting
            # puts it. Both writes therefore go through `data`, and both need flag_modified.
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            touched = False

            if title is not None:
                cleaned = (title or "").strip()[:512]
                if cleaned:
                    data["title"] = cleaned
                else:
                    data.pop("title", None)   # empty string clears it
                touched = True

            if metadata is not None:
                touched = True
                # ALWAYS a merge. A caller can only ever affect keys it names — an explicit null
                # deletes exactly one. There is no whole-object replace, so nothing a writer never
                # saw can be destroyed by it.
                current = data.get("metadata")
                merged = dict(current) if isinstance(current, dict) else {}
                for k, v in metadata.items():
                    if v is None:
                        merged.pop(k, None)
                    else:
                        merged[k] = v
                # Bound the MERGED result, never the patch alone: a cap on each write is not a cap
                # at all when writes merge. Refuse rather than truncate — silently storing part of
                # what a caller sent is a worse failure than telling them it did not fit.
                from .projection import check_metadata_bounds
                reason = check_metadata_bounds(merged)
                if reason:
                    return {"error": "metadata_too_large", "detail": reason}
                data["metadata"] = merged

            if touched:
                meeting.data = data
                flag_modified(meeting, "data")

            await db.commit()
            await db.refresh(meeting)
            return self._planned_row(meeting)

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
            # The USER's own auto-join choice, marked so calendar sync stops deriving the flag
            # from the connected calendars' policy for this row.
            if "auto_join_user_set" in updates:
                if updates["auto_join_user_set"]:
                    data["auto_join_user_set"] = True
                else:
                    data.pop("auto_join_user_set", None)
            if "calendar_uid" in updates:
                if updates["calendar_uid"]:
                    data["calendar_uid"] = updates["calendar_uid"]
                else:
                    data.pop("calendar_uid", None)
            if "calendar_sources" in updates:
                if updates["calendar_sources"]:
                    data["calendar_sources"] = updates["calendar_sources"]
                else:
                    data.pop("calendar_sources", None)
            for key in ("calendar_connection_id", "calendar_name"):
                if key in updates:
                    if updates[key]:
                        data[key] = updates[key]
                    else:
                        data.pop(key, None)
            if "calendar_managed" in updates:
                data["calendar_managed"] = bool(updates["calendar_managed"])

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

    async def prepare_completed_artifact_deletion(self, user_id, meeting_id) -> "Optional[dict]":
        from datetime import datetime, timezone

        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting

        async with self._session_factory() as db:
            meeting = (await db.execute(
                select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
                .with_for_update()
            )).scalars().first()
            if meeting is None:
                return None
            if meeting.status not in ("completed", "failed"):
                return {"error": "conflict"}
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            prior = data.get("artifact_deletion") or {}
            already_deleted = bool(
                prior and prior.get("state", "completed") == "completed"
            )
            if not already_deleted:
                data["artifact_deletion"] = {
                    "state": "pending",
                    "requested_at": prior.get("requested_at")
                    or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "scope": "primary_transcript_and_recording_storage",
                    "backup_residuals": "expire_under_deployment_retention_policy",
                }
                meeting.data = data
                flag_modified(meeting, "data")
                await db.commit()
            return {
                "meeting_id": meeting.id,
                "recordings": list(data.get("recordings") or []),
                "already_deleted": already_deleted,
            }

    async def finalize_completed_artifact_deletion(self, user_id, meeting_id) -> "Optional[bool]":
        from datetime import datetime, timezone

        from sqlalchemy import delete, select
        from sqlalchemy.orm.attributes import flag_modified

        from .models import Meeting, Transcription

        async with self._session_factory() as db:
            meeting = (await db.execute(
                select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
                .with_for_update()
            )).scalars().first()
            if meeting is None:
                return None
            if meeting.status not in ("completed", "failed"):
                return False
            await db.execute(delete(Transcription).where(Transcription.meeting_id == meeting_id))
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            for key in ("recordings", "processed", "notes", "share_grants", "transcript_viewers"):
                data.pop(key, None)
            data["artifact_deletion"] = {
                "state": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "scope": "primary_transcript_and_recording_storage",
                "backup_residuals": "expire_under_deployment_retention_policy",
            }
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()

        # The DB tombstone makes this retryable: if Redis cleanup fails, a repeat reaches this same
        # terminal row and tries the cache/stream cleanup again without resurrecting durable data.
        if self._redis is not None:
            await self._redis.delete(
                f"meeting:{meeting_id}:segments", f"proc:meeting:{meeting_id}"
            )
            await self._redis.srem("active_meetings", str(meeting_id))
        return True


class RedisStreamBus:
    """``RedisBus`` over a ``redis.asyncio`` client — XREADGROUP the segments stream, XACK,
    PUBLISH ``tc:meeting:{id}:mutable``. Carve of ``collector/consumer.py`` + ``processors.py``."""

    def __init__(self, client):
        self._client = client
        self._reclaim_unsupported = False  # #636: log-once latch when the Redis lacks XAUTOCLAIM
        self._prune_unsupported = False  # #660: log-once latch when the Redis lacks XINFO CONSUMERS

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

    async def reclaim_orphans(self, *, group, stream, consumer, min_idle_ms, count=10):
        """#636: XAUTOCLAIM idle (crashed-replica) entries from the group's PEL into ``consumer``.
        Bounded (single call, ``count`` cap) — the returned cursor lets the next tick continue.

        XAUTOCLAIM needs **Redis >= 6.2**. On an older server (e.g. Redis 6.0, which the Lite image
        bundled — the #636 regression the v0.12.5 witness caught) the command is unknown; rather than
        crash the segment-consumer loop every tick, degrade to a **no-op** (return no reclaimed
        entries). The normal consume path (XREADGROUP) is unaffected; only cross-replica orphan
        recovery is unavailable until Redis is upgraded. Logged once."""
        from redis.exceptions import ResponseError

        try:
            await self._client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception:
            pass  # BUSYGROUP — group already exists
        try:
            resp = await self._client.xautoclaim(
                name=stream, groupname=group, consumername=consumer,
                min_idle_time=min_idle_ms, start_id="0-0", count=count,
            )
        except ResponseError as e:
            msg = str(e).lower()
            if "unknown command" in msg or "xautoclaim" in msg:
                if not self._reclaim_unsupported:
                    self._reclaim_unsupported = True
                    log.warning(
                        "XAUTOCLAIM unsupported on this Redis (needs >= 6.2) — #636 orphan reclaim "
                        "disabled; normal segment consumption is unaffected. Upgrade Redis to re-enable."
                    )
                return []
            raise
        return _decode_claimed(resp)

    async def list_consumers(self, *, group, stream):
        """#660: XINFO CONSUMERS → ``[{"name", "pending", "idle"}, ...]`` (idle in ms), the seam the
        reclaim sweep uses to find abandoned per-recreate ghost consumers.

        Degrades to ``[]`` on a Redis that rejects the command (NOGROUP before the group exists, or an
        ``unknown command`` on a server without XINFO CONSUMERS) — the SAME no-op-on-unsupported
        contract ``reclaim_orphans`` uses for XAUTOCLAIM, so consumer-pruning being unavailable never
        breaks the normal XREADGROUP consume path. Logged once."""
        from redis.exceptions import ResponseError

        try:
            resp = await self._client.xinfo_consumers(stream, group)
        except ResponseError as e:
            msg = str(e).lower()
            if "unknown command" in msg or "xinfo" in msg:
                if not self._prune_unsupported:
                    self._prune_unsupported = True
                    log.warning(
                        "XINFO CONSUMERS unsupported on this Redis — #660 ghost-consumer prune "
                        "disabled; normal segment consumption is unaffected. Upgrade Redis to re-enable."
                    )
                return []
            if "nogroup" in msg:
                return []  # group not created yet — nothing to prune
            raise
        out: list[dict] = []
        for entry in resp or []:
            info = {
                (k.decode() if isinstance(k, bytes) else k): v
                for k, v in entry.items()
            }
            name = info.get("name")
            out.append({
                "name": name.decode() if isinstance(name, bytes) else name,
                "pending": int(info.get("pending") or 0),
                "idle": int(info.get("idle") or 0),
            })
        return out

    async def delete_consumer(self, *, group, stream, consumer):
        """#660: XGROUP DELCONSUMER — returns the pending count the consumer held (0 for a ghost)."""
        return await self._client.xgroup_delconsumer(stream, group, consumer)

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
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ..db import build_engine
    from .app import create_app

    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/vexa"
    )
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")

    engine = build_engine(database_url)  # #635: env-steered pool
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # #528: hardened Redis client (see meeting_api/__main__.py) — bounded timeouts + keepalive +
    # health checks so a Redis blip self-heals within socket_timeout instead of hanging the consumer.
    redis_client = aioredis.from_url(
        redis_url, decode_responses=True,
        socket_timeout=10, socket_connect_timeout=5, socket_keepalive=True,
        health_check_interval=30, retry_on_timeout=True,
    )

    store = SqlAlchemyTranscriptStore(session_factory, redis_client=redis_client)
    bus = RedisStreamBus(redis_client)
    return create_app(store, bus)
