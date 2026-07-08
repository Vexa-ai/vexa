"""In-process fakes satisfying the collector's ports — for the ingestion eval AND the gateway
conformance harness (both drive the SAME shipped ``create_app`` / ``ingest`` with these).

  * ``InMemoryTranscriptStore`` — a dict-backed ``TranscriptStore``. ``seed_meeting`` plants a
    meeting (mirrors a ``meetings`` row + its ``data`` JSONB); ``append_segment`` accumulates
    segments by ``segment_id`` (last-write-wins, the parent's Redis-hash identity). ``get_transcript``
    emits an api.v1 ``TranscriptionResponse``-shaped dict; ``list_meetings`` emits
    ``MeetingResponse``-shaped dicts.
  * ``FakeRedisBus`` — a fakeredis-backed ``RedisBus`` wrapper: ``xadd`` to enqueue a stream
    message, ``read_segments`` drains via XREADGROUP, ``publish`` records (and forwards to
    fakeredis pubsub) the ``:mutable`` updates so a test can assert the gateway-facing payload.

These carry NO production logic — they only stand in for Postgres + Redis so the eval/conformance
run OFFLINE (no docker), exactly like the gateway lane's port-fakes.
"""
from __future__ import annotations

import json
from typing import Optional


def _segment_to_api(seg: dict) -> dict:
    """A stored segment → api.v1 ``TranscriptionSegment`` (start/end/text/language required)."""
    out = {
        "start": float(seg.get("start", 0.0)),
        "end": float(seg.get("end", 0.0)),
        "text": seg.get("text", ""),
        "language": seg.get("language"),
    }
    for k in ("speaker", "completed", "segment_id", "absolute_start_time", "absolute_end_time"):
        if seg.get(k) is not None:
            out[k] = seg[k]
    return out


class InMemoryTranscriptStore:
    """A dict-backed ``TranscriptStore``. Owner-scoped by ``user_id`` (the authorization
    boundary). Keyed internally by the synthetic ``meeting_id``.

    Pass ``redis_client`` (fakeredis) to mirror the PRODUCTION topology exactly: ``append_segment``
    then lands live segments in the redis hash ``meeting:{id}:segments`` (+ ``active_meetings``),
    ``get_transcript`` merges the durable dict with that hash, and the db-writer tick
    (``db_writer.db_writer_tick``) moves segments from the hash into the durable dict via
    ``upsert_segments`` — so the flush/trim/read-merge seam is testable offline, no docker."""

    def __init__(self, redis_client=None):
        # meeting_id -> {user_id, platform, native_meeting_id, status, start_time, end_time,
        #                data, segments: {segment_id: seg}}
        self._meetings: dict[int, dict] = {}
        self._next_id = 1
        # Optional live-segment redis (fakeredis in tests) — mirrors the prod adapter's split
        # between the in-flight hash (redis) and the durable rows (the dict standing in for PG).
        self._redis = redis_client

    def seed_meeting(
        self,
        *,
        user_id: int,
        platform: str,
        native_meeting_id: str,
        status: str = "active",
        meeting_id: Optional[int] = None,
        start_time: Optional[str] = "2026-06-20T09:00:00Z",
        end_time: Optional[str] = None,
        bot_container_id: Optional[str] = None,
        data: Optional[dict] = None,
        created_at: str = "2026-06-20T08:59:00Z",
        updated_at: str = "2026-06-20T09:00:05Z",
        constructed_meeting_url: Optional[str] = None,
        segments: Optional[list[dict]] = None,
    ) -> int:
        mid = meeting_id if meeting_id is not None else self._next_id
        self._next_id = max(self._next_id, mid + 1)
        self._meetings[mid] = {
            "user_id": user_id,
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "status": status,
            "start_time": start_time,
            "end_time": end_time,
            "bot_container_id": bot_container_id,
            "constructed_meeting_url": constructed_meeting_url,
            "data": dict(data or {}),
            "created_at": created_at,
            "updated_at": updated_at,
            "segments": {s["segment_id"]: s for s in (segments or [])},
        }
        return mid

    async def native_for(self, meeting_id):
        """Numeric meeting_id → (native_meeting_id, platform), cross-user (the internal segment
        consumer owns the mapping). Mirrors the SqlAlchemy store so ingest can stamp the live payload."""
        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            return None
        m = self._meetings.get(mid)
        if not m or not m.get("native_meeting_id"):
            return None
        return (m["native_meeting_id"], m.get("platform") or "google_meet")

    def _find(self, user_id, platform, native_meeting_id) -> Optional[int]:
        # NEWEST-first, exactly like the SqlAlchemy store (``order_by(Meeting.created_at.desc())``): a user
        # with several rows on the SAME native link resolves to the LATEST run. This faithfully mirrors the
        # symptom-2 ambiguity — the native path can only ever address the newest row, which is precisely
        # why the by-ROW-id read path exists (P0). Tiebreak on the id so the pick is deterministic.
        matches = [
            (mid, m) for mid, m in self._meetings.items()
            if m["user_id"] == user_id
            and m["platform"] == platform
            and m["native_meeting_id"] == native_meeting_id
        ]
        if not matches:
            return None
        matches.sort(key=lambda kv: (kv[1].get("created_at") or "", kv[0]), reverse=True)
        return matches[0][0]

    async def _transcript_doc(self, mid) -> dict:
        """Build the api.v1 ``TranscriptionResponse`` for row ``mid`` — shared by ``get_transcript``
        (native → newest) and ``get_transcript_by_id`` (exact row). Keyed by the row id ``mid``, so a
        by-id read returns exactly that row's segments/notes."""
        m = self._meetings[mid]
        by_id = dict(m["segments"])
        # Redis-wired (prod-topology) mode: merge the LIVE in-flight hash over the durable rows,
        # exactly like the SqlAlchemy store's read merge.
        if self._redis is not None:
            raw = await self._redis.hgetall(f"meeting:{mid}:segments")
            for v in (raw.values() if isinstance(raw, dict) else []):
                try:
                    seg = json.loads(v.decode() if isinstance(v, (bytes, bytearray)) else v)
                except Exception:
                    continue
                sid = seg.get("segment_id")
                if sid:
                    by_id[sid] = seg
        segments = sorted(by_id.values(), key=lambda s: float(s.get("start", 0.0)))
        return {
            "id": mid,
            "platform": m["platform"],
            "native_meeting_id": m["native_meeting_id"],
            "constructed_meeting_url": m.get("constructed_meeting_url"),
            "status": m["status"],
            "start_time": m["start_time"],
            "end_time": m["end_time"],
            "recordings": m["data"].get("recordings", []),
            "notes": m["data"].get("notes"),
            "data": m["data"],
            "segments": [_segment_to_api(s) for s in segments],
        }

    async def get_transcript(self, user_id, platform, native_meeting_id) -> Optional[dict]:
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        return await self._transcript_doc(mid)

    async def get_transcript_by_id(self, user_id, meeting_id, member_workspaces=None) -> Optional[dict]:
        """Exact-row transcript authorized by owner OR transcript-viewer OR bound-workspace member (mirrors
        authorize_subscribe) — any other caller → ``None`` (a different tenant's row never leaks)."""
        try:
            mid = int(meeting_id)
        except (TypeError, ValueError):
            return None
        m = self._meetings.get(mid)
        if m is None:
            return None
        data = m.get("data") if isinstance(m.get("data"), dict) else {}
        authorized = (
            m.get("user_id") == user_id
            or user_id in (data.get("transcript_viewers") or [])
            or (bool(member_workspaces) and data.get("workspace_id") in member_workspaces)
        )
        return await self._transcript_doc(mid) if authorized else None

    async def list_meetings(self, user_id, *, status=None, platform=None, limit=None, offset=None, member_workspaces=None):
        mws = member_workspaces or set()

        def accessible(m):
            data = m.get("data") if isinstance(m.get("data"), dict) else {}
            return (m["user_id"] == user_id
                    or user_id in (data.get("transcript_viewers") or [])
                    or data.get("workspace_id") in mws)
        rows = [
            (mid, m) for mid, m in self._meetings.items()
            if accessible(m)
            and (status is None or m["status"] == status)
            and (platform is None or m["platform"] == platform)
        ]
        # newest first (by created_at desc, then id desc as a stable tiebreak)
        rows.sort(key=lambda kv: (kv[1]["created_at"], kv[0]), reverse=True)
        if offset:
            rows = rows[offset:]
        if limit:
            rows = rows[:limit]
        return [
            {
                "id": mid,
                "user_id": m["user_id"],
                "platform": m["platform"],
                "native_meeting_id": m["native_meeting_id"],
                "constructed_meeting_url": m.get("constructed_meeting_url"),
                "status": m["status"],
                "bot_container_id": m.get("bot_container_id"),
                "start_time": m["start_time"],
                "end_time": m["end_time"],
                "data": m["data"],
                "shared": m["user_id"] != user_id,
                "created_at": m["created_at"],
                "updated_at": m["updated_at"],
            }
            for mid, m in rows
        ]

    async def authorize_subscribe(self, user_id, platform, native_meeting_id, member_workspaces=None) -> Optional[int]:
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is not None:
            return mid  # (a) owner
        for m_id, m in self._meetings.items():
            if not (m.get("platform") == platform and m.get("native_meeting_id") == native_meeting_id
                    and isinstance(m.get("data"), dict)):
                continue
            data = m["data"]
            if member_workspaces and data.get("workspace_id") in member_workspaces:
                return m_id  # (b) member of the bound workspace
            if user_id in (data.get("transcript_viewers") or []):
                return m_id  # (c) redeemed an independent transcript-share link
        return None

    async def bind_workspace(self, user_id, platform, native_meeting_id, workspace_id):
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        self._meetings[mid]["data"]["workspace_id"] = workspace_id
        return workspace_id

    async def mint_transcript_share(self, user_id, platform, native_meeting_id, *,
                                    mode="open", allowed_emails=None, expires_in_sec=86400):
        from datetime import timedelta

        from .adapters import _now, _sha
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        import secrets
        secret = secrets.token_urlsafe(24)
        gid = secrets.token_hex(8)
        expires_at = (_now() + timedelta(seconds=int(expires_in_sec))).isoformat()
        grant = {"id": gid, "secret_hash": _sha(secret), "mode": mode,
                 "allowed_emails": list(allowed_emails or []), "expires_at": expires_at, "revoked": False}
        self._meetings[mid]["data"].setdefault("share_grants", []).append(grant)
        return {"id": gid, "token": f"{mid}.{secret}", "mode": mode, "expires_at": expires_at}

    async def redeem_transcript_share(self, user_id, user_email, token):
        from .adapters import _sha, validate_transcript_grant
        if not token or "." not in token:
            return None
        mid_s, secret = token.split(".", 1)
        try:
            mid = int(mid_s)
        except ValueError:
            return None
        m = self._meetings.get(mid)
        if not m:
            return None
        grant = next((g for g in m["data"].get("share_grants", []) if g.get("secret_hash") == _sha(secret)), None)
        if not grant:
            return {"error": "invalid"}
        err = validate_transcript_grant(grant, user_email)
        if err:
            return {"error": err}
        viewers = m["data"].setdefault("transcript_viewers", [])
        if user_id not in viewers:
            viewers.append(user_id)
        return {"meeting_id": mid, "ok": True}

    async def connect_doc(self, user_id, platform, native_meeting_id, doc):
        from .adapters import _upsert_doc

        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        data = self._meetings[mid]["data"]
        docs = _upsert_doc(list(data.get("docs", [])), doc)
        data["docs"] = docs
        return docs

    async def disconnect_doc(self, user_id, platform, native_meeting_id, path):
        from .adapters import _remove_doc

        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        data = self._meetings[mid]["data"]
        docs = _remove_doc(list(data.get("docs", [])), path)
        data["docs"] = docs
        return docs

    async def set_intent(self, user_id, platform, native_meeting_id, status, scheduled_at=None):
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        m = self._meetings[mid]
        data = m["data"]
        prev_status = m.get("status")
        prev_at = data.get("scheduled_at")
        new_at = scheduled_at if status == "scheduled" else None
        m["status"] = status
        if status == "scheduled":
            data["scheduled_at"] = new_at
        else:
            data.pop("scheduled_at", None)
        changed = (prev_status != status) or (prev_at != new_at)
        return {
            "id": mid,
            "user_id": user_id,
            "platform": platform,
            "native_id": native_meeting_id,
            "status": status,
            "scheduled_at": new_at,
            "changed": changed,
        }

    def _planned_row(self, mid) -> dict:
        m = self._meetings[mid]
        return {
            "id": mid,
            "user_id": m["user_id"],
            "platform": m["platform"],
            "native_meeting_id": m["native_meeting_id"],
            "constructed_meeting_url": m.get("constructed_meeting_url")
            or m["data"].get("constructed_meeting_url"),
            "status": m["status"],
            "bot_container_id": m.get("bot_container_id"),
            "start_time": m.get("start_time"),
            "end_time": m.get("end_time"),
            "data": m["data"],
            "shared": False,
            "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"),
        }

    def _dup_non_terminal(self, user_id, platform, native_meeting_id, exclude_id=None):
        """True when a NON-TERMINAL row already exists for (user, platform, native) — the fake's
        stand-in for the partial unique index + the adapter's dup check."""
        if native_meeting_id is None:
            return False
        return any(
            m["user_id"] == user_id and m["platform"] == platform
            and m["native_meeting_id"] == native_meeting_id
            and m["status"] not in ("completed", "failed")
            and mid != exclude_id
            for mid, m in self._meetings.items()
        )

    async def create_planned_meeting(self, user_id, *, platform, native_meeting_id,
                                     title=None, scheduled_at=None, meeting_url=None,
                                     workspace_id=None, auto_join=True, calendar_uid=None,
                                     workspace_source=None, attendees=None):
        if self._dup_non_terminal(user_id, platform, native_meeting_id):
            return {"error": "duplicate"}
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
        mid = self.seed_meeting(
            user_id=user_id, platform=platform, native_meeting_id=native_meeting_id,
            status="scheduled" if scheduled_at else "idle",
            start_time=None, data=data, constructed_meeting_url=meeting_url,
        )
        return self._planned_row(mid)

    async def update_planned_meeting(self, user_id, meeting_id, updates):
        m = self._meetings.get(meeting_id)
        if m is None or m["user_id"] != user_id:
            return None
        if m["status"] not in ("idle", "scheduled"):
            return {"error": "conflict"}
        data = m["data"]
        if "native_meeting_id" in updates:
            new_platform = updates.get("platform") or m["platform"]
            new_native = updates["native_meeting_id"]
            if new_native is not None and self._dup_non_terminal(
                user_id, new_platform, new_native, exclude_id=meeting_id
            ):
                return {"error": "duplicate"}
            m["platform"] = new_platform
            m["native_meeting_id"] = new_native
        if "constructed_meeting_url" in updates:
            if updates["constructed_meeting_url"]:
                data["constructed_meeting_url"] = updates["constructed_meeting_url"]
                m["constructed_meeting_url"] = updates["constructed_meeting_url"]
            else:
                data.pop("constructed_meeting_url", None)
                m["constructed_meeting_url"] = None
        if "title" in updates:
            if updates["title"]:
                data["title"] = updates["title"]
            else:
                data.pop("title", None)
        if "scheduled_at" in updates:
            if updates["scheduled_at"]:
                data["scheduled_at"] = updates["scheduled_at"]
                m["status"] = "scheduled"
            else:
                data.pop("scheduled_at", None)
                m["status"] = "idle"
        if "workspace_id" in updates:
            if updates["workspace_id"]:
                data["workspace_id"] = updates["workspace_id"]
                data["workspace_source"] = "user"
                data.pop("workspace_unbound", None)
            else:
                data.pop("workspace_id", None)
                data.pop("workspace_source", None)
                if data.get("calendar_uid"):
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
        return self._planned_row(meeting_id)

    async def delete_planned_meeting(self, user_id, meeting_id):
        m = self._meetings.get(meeting_id)
        if m is None or m["user_id"] != user_id:
            return None
        if m["status"] not in ("idle", "scheduled"):
            return False
        del self._meetings[meeting_id]
        return True

    def _row_or_placeholder(self, meeting_id) -> dict:
        m = self._meetings.get(meeting_id)
        if m is None:
            # An ingested segment for an unknown meeting — seed a placeholder so the segment is
            # not lost (the parent persists by meeting_id regardless; the meeting row exists by
            # the time segments flow). Keep it owner-less until seeded.
            m = self._meetings.setdefault(meeting_id, {
                "user_id": None, "platform": None, "native_meeting_id": None,
                "status": "active", "start_time": None, "end_time": None,
                "bot_container_id": None, "constructed_meeting_url": None,
                "data": {}, "created_at": "", "updated_at": "", "segments": {},
            })
        return m

    async def append_segment(self, meeting_id, segment) -> None:
        if self._redis is not None:
            # Prod-topology mode: live segments land in the redis HASH (+ the db-writer's
            # active_meetings sweep set), exactly like SqlAlchemyTranscriptStore.append_segment;
            # only the db-writer tick moves them into the durable dict.
            from .db_writer import ACTIVE_MEETINGS_KEY, segments_hash_key

            await self._redis.sadd(ACTIVE_MEETINGS_KEY, str(meeting_id))
            await self._redis.hset(
                segments_hash_key(meeting_id), segment["segment_id"], json.dumps(segment)
            )
            return
        self._row_or_placeholder(meeting_id)["segments"][segment["segment_id"]] = segment

    async def upsert_segments(self, meeting_id, segments) -> None:
        """The db-writer's durable sink (the dict stands in for the ``transcriptions`` table):
        upsert by ``segment_id`` — idempotent, a re-flush updates in place."""
        m = self._row_or_placeholder(meeting_id)
        for seg in segments:
            sid = seg.get("segment_id")
            if sid:
                m["segments"][sid] = dict(seg)

    async def processed_view_cursor(self, meeting_id, view_id) -> Optional[str]:
        from .adapters import _find_processed_view

        m = self._meetings.get(meeting_id)
        if not m:
            return None
        view = _find_processed_view(m["data"], view_id)
        return view.get("source_cursor") if view else None

    async def merge_processed_view(
        self, meeting_id, *, view_id, kind, notes, source_cursor, params=None,
    ) -> None:
        """Persist drained copilot notes into ``data['processed']['views']`` — the SAME pure
        upsert the SqlAlchemy store commits (the versioned multi-view shape, merged by note id)."""
        from .adapters import _upsert_processed_view

        m = self._row_or_placeholder(meeting_id)
        m["data"] = _upsert_processed_view(
            m["data"], view_id=view_id, kind=kind, notes=notes,
            source_cursor=source_cursor, params=params,
        )


class FakeRedisBus:
    """A ``RedisBus`` over fakeredis. Wraps a fakeredis async client for stream read/ack/publish,
    plus ``xadd`` (test-only) to enqueue stream messages and a ``published`` log of ``:mutable``
    payloads for assertions."""

    def __init__(self, client):
        self._client = client
        self.published: list[tuple[str, str]] = []  # (channel, raw_json)

    async def xadd(self, stream: str, payload: dict) -> str:
        """Enqueue one stream message (the bot's XADD). ``payload`` is the inner JSON; the stream
        field is ``payload`` (the parent's stream field name)."""
        return await self._client.xadd(stream, {"payload": json.dumps(payload)})

    async def read_segments(self, *, group, consumer, stream, count=10):
        try:
            await self._client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception:
            pass
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
        self.published.append((channel, data))
        return await self._client.publish(channel, data)
