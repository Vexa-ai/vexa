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

from .cursor import changed_since as _changed_since
from .cursor import is_stale as _cursor_is_stale
from .cursor import iso as _iso
from .cursor import now_iso as _now_iso
from .cursor import watermark as _cursor_watermark


def _segment_to_api(seg: dict) -> dict:
    """A stored segment → api.v1 ``TranscriptionSegment`` (start/end/text/language required)."""
    out = {
        "start": float(seg.get("start", 0.0)),
        "end": float(seg.get("end", 0.0)),
        "text": seg.get("text", ""),
        "language": seg.get("language"),
    }
    for k in ("speaker", "speaker_key", "completed", "segment_id", "source",
              "absolute_start_time", "absolute_end_time", "updated_at"):
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
        # meeting_id -> {segment_id: retracted_at} — the retraction tombstones the `?since=` cursor
        # reports, mirroring the prod store's `meeting:{id}:retracted` hash.
        self._retracted: dict[int, dict[str, str]] = {}
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

    async def _transcript_doc(self, mid, since=None, *, viewer_is_owner: bool) -> dict:
        """Build the api.v1 ``TranscriptionResponse`` for row ``mid`` — shared by ``get_transcript``
        (native → newest) and ``get_transcript_by_id`` (exact row). Keyed by the row id ``mid``, so a
        by-id read returns exactly that row's segments/notes. Mirrors the real store's viewer-aware
        response projection, ``viewer_is_owner`` included — the fake and the real store must agree on
        what a share recipient receives, since most of the suite drives the fake.

        ``since`` applies the incremental-read cursor exactly as the prod store does (see
        ``cursor.py``): change-stamp filter, watermark taken before the read, stale cursor → full
        re-read with ``resynced: true``, retractions reported so a follower can DROP them."""
        from .projection import project_response_data

        watermark = _cursor_watermark()
        stale = _cursor_is_stale(since)
        effective_since = None if stale else since
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
        kept = [s for s in by_id.values() if _changed_since(s, effective_since)]
        segments = sorted(kept, key=lambda s: float(s.get("start", 0.0)))
        doc = {
            "id": mid,
            "platform": m["platform"],
            "native_meeting_id": m["native_meeting_id"],
            "constructed_meeting_url": m.get("constructed_meeting_url"),
            "status": m["status"],
            "start_time": m["start_time"],
            "end_time": m["end_time"],
            "recordings": m["data"].get("recordings", []),
            "notes": m["data"].get("notes"),
            "data": project_response_data(m["data"], viewer_is_owner=viewer_is_owner),
            "segments": [_segment_to_api(s) for s in segments],
            "next_since": _iso(watermark),
        }
        if since is not None:
            doc["retracted_segment_ids"] = await self.retractions_since(mid, effective_since)
            doc["resynced"] = stale
        return doc

    async def get_transcript(self, user_id, platform, native_meeting_id, since=None) -> Optional[dict]:
        mid = self._find(user_id, platform, native_meeting_id)
        if mid is None:
            return None
        deletion = (self._meetings[mid].get("data") or {}).get("artifact_deletion") or {}
        if deletion and deletion.get("state", "completed") == "completed":
            return None
        # ``_find`` matches on ``m["user_id"] == user_id`` (mirroring the real store's SQL), so a row
        # reached by the native-keyed path is always the caller's own.
        return await self._transcript_doc(mid, since, viewer_is_owner=True)

    async def get_transcript_by_id(self, user_id, meeting_id, member_workspaces=None,
                                   since=None) -> Optional[dict]:
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
        deletion = data.get("artifact_deletion") or {}
        if deletion and deletion.get("state", "completed") == "completed":
            return None
        is_owner = m.get("user_id") == user_id
        authorized = (
            is_owner
            or user_id in (data.get("transcript_viewers") or [])
            or (bool(member_workspaces) and data.get("workspace_id") in member_workspaces)
        )
        # Same decision, two uses: whether the read is allowed, and which tier of ``data`` it carries.
        return await self._transcript_doc(mid, since, viewer_is_owner=is_owner) if authorized else None

    async def list_meetings(self, user_id, *, status=None, platform=None, limit=None, offset=None,
                            member_workspaces=None, list_view=False, meeting_id=None, slim=False):
        from .projection import DEFAULT_LIST_LIMIT, project_list_data
        mws = member_workspaces or set()

        def accessible(m):
            data = m.get("data") if isinstance(m.get("data"), dict) else {}
            return (m["user_id"] == user_id
                    or user_id in (data.get("transcript_viewers") or [])
                    or data.get("workspace_id") in mws)
        rows = [
            (mid, m) for mid, m in self._meetings.items()
            if accessible(m)
            and (status is None or (m["status"] in status
                                    if isinstance(status, (list, tuple, set, frozenset))
                                    else m["status"] == status))
            and (meeting_id is None or mid == meeting_id)
            and (platform is None or m["platform"] == platform)
        ]
        # newest first (by created_at desc, then id desc as a stable tiebreak)
        rows.sort(key=lambda kv: (kv[1]["created_at"], kv[0]), reverse=True)
        if offset:
            rows = rows[offset:]
        if list_view:
            # #584: mirror the real store — default page size + over-fetch-by-1 for honest has_more.
            effective_limit = limit if limit is not None else DEFAULT_LIST_LIMIT
            has_more = len(rows) > effective_limit
            rows = rows[:effective_limit]
        else:
            if limit:
                rows = rows[:limit]
            has_more = False

        def _row(mid, m):
            # One ownership decision per row, feeding both `shared` and the projection's viewer tier
            # (mirrors the real store's `_row`).
            is_owner = m["user_id"] == user_id
            row = {
                "id": mid,
                "user_id": m["user_id"],
                "platform": m["platform"],
                "native_meeting_id": m["native_meeting_id"],
                "constructed_meeting_url": m.get("constructed_meeting_url"),
                "status": m["status"],
                "bot_container_id": m.get("bot_container_id"),
                "start_time": m["start_time"],
                "end_time": m["end_time"],
                # api.v1 MeetingResponse declares these at top level; the values live in `data`.
                "completion_reason": (m.get("data") or {}).get("completion_reason") if isinstance(m.get("data"), dict) else None,
                "failure_stage": (m.get("data") or {}).get("failure_stage") if isinstance(m.get("data"), dict) else None,
                "shared": not is_owner,
                "created_at": m["created_at"],
                "updated_at": m["updated_at"],
                # #584 list_view / #803 slim: both drop the heavy detail keys and keep the light
                # metadata. Only a caller that genuinely renders full `data` leaves both off.
                # The response omissions are viewer-aware — the owner keeps their own webhook config.
                "data": project_list_data(m["data"], viewer_is_owner=is_owner) if (list_view or slim)
                else m["data"],
            }
            return row

        result = [_row(mid, m) for mid, m in rows]
        return (result, has_more) if list_view else result

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
                                     calendar_source=None,
                                     workspace_source=None, attendees=None,
                                     auto_join_last_attempt=None,
                                     auto_join_error=None):
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
        mid = self.seed_meeting(
            user_id=user_id, platform=platform, native_meeting_id=native_meeting_id,
            status="scheduled" if scheduled_at else "idle",
            start_time=None, data=data, constructed_meeting_url=meeting_url,
        )
        return self._planned_row(mid)

    async def attach_calendar_source(self, user_id, meeting_id, *, calendar_uid,
                                     calendar_sources=None):
        """Identity-only stamp on a row in ANY status — mirrors the adapter (live-row adoption)."""
        m = self._meetings.get(meeting_id)
        if m is None or m["user_id"] != user_id:
            return None
        data = m["data"]
        if calendar_uid:
            data["calendar_uid"] = calendar_uid
        if calendar_sources:
            data["calendar_sources"] = [dict(s) for s in calendar_sources]
            primary = calendar_sources[0]
            data["calendar_connection_id"] = primary.get("id")
            data["calendar_name"] = primary.get("name") or "Calendar"
        return self._planned_row(meeting_id)

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
        return self._planned_row(meeting_id)

    async def delete_planned_meeting(self, user_id, meeting_id):
        m = self._meetings.get(meeting_id)
        if m is None or m["user_id"] != user_id:
            return None
        if m["status"] not in ("idle", "scheduled"):
            return False
        del self._meetings[meeting_id]
        return True

    async def prepare_completed_artifact_deletion(self, user_id, meeting_id):
        from datetime import datetime, timezone

        m = self._meetings.get(meeting_id)
        if m is None or m["user_id"] != user_id:
            return None
        if m["status"] not in ("completed", "failed"):
            return {"error": "conflict"}
        data = dict(m.get("data") or {})
        prior = data.get("artifact_deletion") or {}
        already_deleted = bool(prior and prior.get("state", "completed") == "completed")
        if not already_deleted:
            data["artifact_deletion"] = {
                "state": "pending",
                "requested_at": prior.get("requested_at")
                or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "scope": "primary_transcript_and_recording_storage",
                "backup_residuals": "expire_under_deployment_retention_policy",
            }
            m["data"] = data
        return {
            "meeting_id": meeting_id,
            "recordings": list(data.get("recordings") or []),
            "already_deleted": already_deleted,
        }

    async def finalize_completed_artifact_deletion(self, user_id, meeting_id):
        from datetime import datetime, timezone

        m = self._meetings.get(meeting_id)
        if m is None or m["user_id"] != user_id:
            return None
        if m["status"] not in ("completed", "failed"):
            return False
        m["segments"] = {}
        data = dict(m.get("data") or {})
        for key in ("recordings", "processed", "notes", "share_grants", "transcript_viewers"):
            data.pop(key, None)
        data["artifact_deletion"] = {
            "state": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "primary_transcript_and_recording_storage",
            "backup_residuals": "expire_under_deployment_retention_policy",
        }
        m["data"] = data
        if self._redis is not None:
            await self._redis.delete(
                f"meeting:{meeting_id}:segments", f"proc:meeting:{meeting_id}"
            )
            await self._redis.srem("active_meetings", str(meeting_id))
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

    async def delete_segments(self, meeting_id, segment_ids) -> None:
        ids = [str(s) for s in (segment_ids or []) if s]
        if not ids:
            return
        # Tombstone the retraction so an incremental reader learns the segment is GONE (the prod
        # store's `_record_retractions`); a deletion is the one change `?since=` cannot express as data.
        stamp = _now_iso()
        self._retracted.setdefault(int(meeting_id), {}).update({sid: stamp for sid in ids})
        if self._redis is not None:
            from .db_writer import segments_hash_key

            await self._redis.hdel(segments_hash_key(meeting_id), *ids)
            return
        segs = self._row_or_placeholder(meeting_id)["segments"]
        for sid in ids:
            segs.pop(sid, None)

    async def retractions_since(self, meeting_id, since) -> list:
        """The segment ids retracted at-or-after ``since`` — mirrors the prod store."""
        if since is None:
            return []
        return sorted(
            sid for sid, stamp in self._retracted.get(int(meeting_id), {}).items()
            if _changed_since({"updated_at": stamp}, since)
        )

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

    async def reclaim_orphans(self, *, group, stream, consumer, min_idle_ms, count=10):
        """#636: mirror of ``RedisStreamBus.reclaim_orphans`` over ``fakeredis.aioredis`` (which
        supports XAUTOCLAIM ≥ 2.21). Reclaims idle delivered-but-un-acked entries into ``consumer``."""
        from .adapters import _decode_claimed
        try:
            await self._client.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception:
            pass
        resp = await self._client.xautoclaim(
            name=stream, groupname=group, consumername=consumer,
            min_idle_time=min_idle_ms, start_id="0-0", count=count,
        )
        return _decode_claimed(resp)

    async def list_consumers(self, *, group, stream):
        """#660: mirror of ``RedisStreamBus.list_consumers`` over ``fakeredis.aioredis`` (which
        supports XINFO CONSUMERS and advances ``idle`` with wall-clock). Degrades to ``[]`` when the
        group does not exist yet (NOGROUP), like the real adapter."""
        from redis.exceptions import ResponseError

        try:
            resp = await self._client.xinfo_consumers(stream, group)
        except ResponseError as e:
            if "nogroup" in str(e).lower():
                return []
            raise
        out: list[dict] = []
        for entry in resp or []:
            name = entry.get("name")
            out.append({
                "name": name.decode() if isinstance(name, bytes) else name,
                "pending": int(entry.get("pending") or 0),
                "idle": int(entry.get("idle") or 0),
            })
        return out

    async def delete_consumer(self, *, group, stream, consumer):
        return await self._client.xgroup_delconsumer(stream, group, consumer)

    async def ack(self, *, group, stream, message_ids):
        if message_ids:
            await self._client.xack(stream, group, *message_ids)

    async def publish(self, channel, data):
        self.published.append((channel, data))
        return await self._client.publish(channel, data)
