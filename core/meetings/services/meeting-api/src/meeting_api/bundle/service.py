"""Export one meeting to a meeting-bundle.v1 archive, and import one as a new meeting.

The two flows over the ports meeting-api already owns — the transcript store (rows, segments,
annotations), the recording repo (``meeting.data['recordings']``) and object storage. No new table
and no new store method: an import is the composition of the writes the product already makes
(plan a row, put recordings on it, annotate it, complete it from a transcript), in that order, so
every byte lands through the one writer that owns it.

Ownership is the rule on both sides:

* **Export** reads the whole meeting, so only its OWNER may export it. A share recipient or a
  workspace member sees a projected meeting (credentials and the owner's own configuration
  stripped), not the whole one; they are refused with 403, and a meeting the caller cannot see at
  all is 404, the same answer as one that does not exist.
* **Import** creates a meeting owned by the importer and nobody else: fresh ids, no workspace bind,
  no share grant, no reader roster. The source's ids survive only as provenance in the new
  meeting's annotations (``imported_from``).
"""
from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Callable, Optional

from .codec import (
    BundleRefused,
    MediaBlob,
    ParsedBundle,
    clean_text,
    read_bundle,
    write_bundle,
)

# A segment start at or past this is an absolute epoch second, below it an offset from the start
# (the same discrimination the transcript read applies — ``collector.adapters._fill_absolute_times``).
_EPOCH_THRESHOLD_S = 1_000_000_000
# The meeting statuses with a bot in flight: there is no finished meeting to export yet.
_LIVE_STATUSES = frozenset({"requested", "joining", "awaiting_admission", "needs_help", "active", "stopping"})
#: The annotation key the duplicate check reads: flat, so a containment filter finds it on any store.
BUNDLE_ID_KEY = "imported_bundle_id"
PROVENANCE_KEY = "imported_from"
SOURCE = "bundle"


class ExportError(Exception):
    """An export that cannot be produced, with the HTTP status that says why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def deployment_id(secret: Optional[str]) -> str:
    """This deployment's opaque, stable bundle identity: an HMAC of a fixed label under the
    deployment's own secret, so it is stable across restarts, differs between deployments, and
    cannot be turned back into the secret or into an address."""
    key = (secret or "").encode("utf-8")
    return hmac.new(key, b"vexa/meeting-bundle.v1/deployment-id", sha256).hexdigest()[:32]


def _parse(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _relative(seg: dict, base: Optional[datetime], key: str, abs_key: str) -> float:
    """A segment's time as seconds from the meeting's start, whichever form it was stored in."""
    absolute = _parse(seg.get(abs_key))
    if absolute is not None and base is not None:
        return max(0.0, round((absolute - base).total_seconds(), 3))
    try:
        value = float(seg.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if value >= _EPOCH_THRESHOLD_S and base is not None:
        value = value - base.timestamp()
    return max(0.0, round(value, 3))


def _segments(doc_segments: list, base: Optional[datetime]) -> list:
    out = []
    for seg in doc_segments or []:
        start = _relative(seg, base, "start", "absolute_start_time")
        end = max(start, _relative(seg, base, "end", "absolute_end_time"))
        out.append({
            "start": start,
            "end": end,
            "speaker": clean_text(seg.get("speaker"), 256) or None,
            "text": clean_text(seg.get("text"), 8000) or "",
            "language": clean_text(seg.get("language"), 16) or None,
        })
    out.sort(key=lambda s: (s["start"], s["end"]))
    return out


def _participants(data: dict, segments: list) -> list:
    """Display names only: who spoke, and the invitees' names. Never an address or an account id —
    a name that is itself an email address is left out."""
    names: list = []
    for seg in segments:
        if seg["speaker"]:
            names.append(seg["speaker"])
    for a in data.get("attendees") or []:
        if isinstance(a, dict):
            names.append(clean_text(a.get("name"), 256) or "")
    seen, out = set(), []
    for n in names:
        if n and "@" not in n and n not in seen:
            seen.add(n)
            out.append(n)
    return out[:500]


async def export_meeting(
    store, recording_repo, storage, *,
    user_id: int, meeting_id: int, secret: Optional[str], include_media: bool = True,
    member_workspaces: "Optional[set]" = None,
    finalize: Callable, now: Optional[datetime] = None, bundle_id: Optional[str] = None,
) -> "tuple[bytes, str]":
    """``(archive bytes, suggested filename)`` for one meeting the caller owns, or :class:`ExportError`."""
    rows = await store.list_meetings(user_id, meeting_id=meeting_id,
                                     member_workspaces=set(member_workspaces or ()))
    row = next((r for r in rows if r.get("id") == meeting_id), None)
    if row is None:
        raise ExportError(404, "Meeting not found")
    if row.get("shared") is not False:
        raise ExportError(403, "Only the meeting's owner can export it: a shared or workspace view "
                               "is not the whole meeting")
    if row.get("status") in _LIVE_STATUSES:
        raise ExportError(409, f"Meeting {meeting_id} is {row.get('status')} — export it after it ends")
    doc = await store.get_transcript_by_id(user_id, meeting_id)
    if doc is None:
        raise ExportError(404, "Meeting not found")
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    base = _parse(row.get("start_time"))
    segments = _segments(doc.get("segments") or [], base)

    media: list = []
    if include_media:
        recordings = await recording_repo.get_recordings(meeting_id)
        index = 0
        for rec in sorted(recordings, key=lambda r: str(r.get("created_at") or "")):
            for mf in sorted(rec.get("media_files") or [], key=lambda m: str(m.get("type"))):
                mtype, fmt = mf.get("type"), mf.get("format")
                if mtype not in ("audio", "video") or fmt not in ("webm", "wav", "mkv", "mp4"):
                    raise ExportError(500, f"recording {rec.get('id')} holds a {mtype}/{fmt} media file "
                                           "this bundle version cannot carry")
                key = await finalize(recording_repo, storage, meeting_id=meeting_id,
                                     recording_id=rec["id"], media_type=mtype)
                if not key:
                    raise ExportError(502, f"recording {rec.get('id')} {mtype} has no playable master to export")
                try:
                    blob = await storage.get(key)
                except Exception as e:  # noqa: BLE001 — named, then refused: never a bundle missing a part
                    raise ExportError(502, f"recording {rec.get('id')} {mtype} could not be read from storage: "
                                           f"{type(e).__name__}")
                index += 1
                media.append(MediaBlob(path=f"media/recording-{index}-{mtype}.{fmt}", type=mtype, format=fmt,
                                       duration_seconds=mf.get("duration_seconds"), data=blob))

    title = clean_text(data.get("title"), 512) or None
    meeting = {
        "platform": str(row.get("platform") or "unknown"),
        "native_meeting_id": clean_text(row.get("native_meeting_id"), 256) or None,
        "title": title,
        "status": str(row.get("status") or "completed"),
        "start_time": _iso(base),
        "end_time": _iso(_parse(row.get("end_time"))),
        "participants": _participants(data, segments),
        "media": [{"path": m.path, "type": m.type, "format": m.format,
                   "duration_seconds": m.duration_seconds} for m in media],
    }
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    notes = data.get("notes") if isinstance(data.get("notes"), str) else None
    annotations = {"metadata": metadata, "notes": notes}
    when = now or datetime.now(timezone.utc)
    archive = write_bundle(
        meeting=meeting, transcript={"segments": segments}, annotations=annotations, media=media,
        bundle_id=bundle_id or str(uuid.uuid4()), exported_at=_iso(when),
        deployment_id=deployment_id(secret), source_meeting_id=meeting_id,
    )
    slug = "".join(c if c.isalnum() else "-" for c in (title or f"meeting-{meeting_id}").lower()).strip("-")[:60]
    return archive, f"{slug or 'meeting'}.meeting-bundle.zip"


def preview(parsed: ParsedBundle, duplicate_of: Optional[int]) -> dict:
    """What an import would create, shown before anything is written."""
    meeting, segs = parsed.meeting, parsed.transcript["segments"]
    skipped = []
    if parsed.workspace:
        skipped.append({"part": "workspace", "files": len(parsed.workspace),
                        "reason": "meeting import does not restore an attached workspace"})
    if parsed.annotations.get("notes"):
        skipped.append({"part": "notes", "reason": "meeting import does not restore free-text notes"})
    return {
        "bundle_id": parsed.bundle_id,
        "format_version": parsed.manifest["format_version"],
        "exported_at": parsed.manifest["exported_at"],
        "source": dict(parsed.manifest["source"]),
        "meeting": {k: meeting[k] for k in ("platform", "native_meeting_id", "title", "status",
                                            "start_time", "end_time", "participants")},
        "segments": len(segs),
        "speakers": sorted({s["speaker"] for s in segs if s.get("speaker")}),
        "media": [{"path": m.path, "type": m.type, "format": m.format, "bytes": len(m.data),
                   "duration_seconds": m.duration_seconds} for m in parsed.media],
        "annotations": {"metadata_keys": sorted(parsed.annotations["metadata"].keys())},
        "skipped": skipped,
        "duplicate_of": duplicate_of,
    }


async def find_duplicate(store, user_id: int, bundle_id: str) -> Optional[int]:
    rows = await store.list_meetings(user_id, metadata_filter={BUNDLE_ID_KEY: bundle_id})
    own = [r for r in rows if r.get("shared") is False]
    return own[0]["id"] if own else None


def _occurrence(meeting: dict, manifest: dict, segments: list) -> "tuple[datetime, datetime]":
    start = _parse(meeting.get("start_time")) or _parse(meeting.get("end_time")) or _parse(manifest["exported_at"])
    end = _parse(meeting.get("end_time"))
    length = max((s["end"] for s in segments), default=0.0)
    if end is None or end < start:
        end = start + timedelta(seconds=length)
    return start, end


async def import_bundle(
    store, recording_repo, storage, *,
    user_id: int, raw: bytes, dry_run: bool, log_event: Callable,
) -> dict:
    """Validate ``raw`` and, unless ``dry_run``, land it as a new meeting the caller owns.

    Raises :class:`BundleRefused` for every refusal (the route maps codes to statuses)."""
    from ..collector.transcript_import import normalize_segments, session_uid_for
    from ..recordings import new_recording_numeric_id

    parsed = read_bundle(raw)
    duplicate_of = await find_duplicate(store, user_id, parsed.bundle_id)
    view = preview(parsed, duplicate_of)
    if dry_run:
        return {"dry_run": True, **view}
    if duplicate_of is not None:
        raise BundleRefused("duplicate_import",
                            f"this bundle was already imported as meeting {duplicate_of}; "
                            "delete that meeting first to import it again")
    raw_segments = [{**s, "text": clean_text(s["text"], 8000) or "",
                     "speaker": clean_text(s["speaker"], 256) or None} for s in parsed.transcript["segments"]]
    if not raw_segments:
        raise BundleRefused("invalid_part", "transcript.json has no segments — a meeting bundle "
                                            "without a transcript cannot be imported by this version")

    meeting = parsed.meeting
    title = clean_text(meeting.get("title"), 512) or None
    platform, native = meeting["platform"], meeting.get("native_meeting_id")
    row = await store.create_planned_meeting(user_id, platform=platform, native_meeting_id=native,
                                             title=title, auto_join=False)
    if isinstance(row, dict) and row.get("error") == "duplicate":
        # A live row of the importer's already holds this meeting link; the imported meeting is kept
        # without the link rather than taking the live row's place.
        row = await store.create_planned_meeting(user_id, platform=platform, native_meeting_id=None,
                                                 title=title, auto_join=False)
    if not isinstance(row, dict) or not row.get("id"):
        raise RuntimeError("the meeting row for the import could not be created")
    mid = int(row["id"])
    session_uid = session_uid_for(SOURCE, mid)
    written_keys: list = []
    try:
        for blob in parsed.media:
            rec_id = new_recording_numeric_id()
            key = f"recordings/{user_id}/{rec_id}/{session_uid}/{blob.type}/master.{blob.format}"
            await storage.upload(key, blob.data, content_type=blob.content_type)
            written_keys.append(key)
            await recording_repo.mutate_recordings(
                mid, _append_recording(rec_id=rec_id, meeting_id=mid, user_id=user_id,
                                       session_uid=session_uid, blob=blob, key=key))

        provenance = {
            "bundle_id": parsed.bundle_id,
            "source_deployment_id": parsed.manifest["source"]["deployment_id"],
            "source_meeting_id": parsed.manifest["source"]["meeting_id"],
            "exported_at": parsed.manifest["exported_at"],
            "platform": platform,
            "native_meeting_id": native,
            "source_status": meeting["status"],
            "participants": meeting["participants"][:100],
        }
        metadata = {**parsed.annotations["metadata"], PROVENANCE_KEY: provenance, BUNDLE_ID_KEY: parsed.bundle_id}
        annotated = await store.annotate_meeting(user_id, mid, title=title, metadata=metadata)
        if annotated is None or annotated.get("error"):
            raise BundleRefused("invalid_part", (annotated or {}).get("detail")
                                or "annotations could not be stored on the new meeting")

        segments, reason = normalize_segments(raw_segments, session_uid)
        if reason:
            raise BundleRefused("invalid_part", f"transcript.json: {reason}")
        started, ended = _occurrence(meeting, parsed.manifest, segments)
        result = await store.complete_transcript_import(
            user_id, mid, segments=segments, started_at=started, ended_at=ended,
            source=SOURCE, session_uid=session_uid)
        if not result or result.get("error"):
            raise RuntimeError(f"the transcript could not be completed on meeting {mid}: {result}")
    except BaseException:
        # Nothing half-imported survives: the row is still planned (completion is the last write),
        # so it and every object written for it are removed before the failure is raised.
        for key in written_keys:
            try:
                await storage.delete(key)
            except Exception:  # noqa: BLE001
                pass
        try:
            await store.delete_planned_meeting(user_id, mid)
        except Exception:  # noqa: BLE001
            pass
        raise
    log_event("meeting_bundle_imported", audience="user", span="meetings.bundle.import",
              user_id=user_id, meeting_id=str(mid),
              fields={"bundle_id": parsed.bundle_id, "segments": len(segments),
                      "media": len(parsed.media), "skipped": [s["part"] for s in view["skipped"]]})
    return {"imported": True, "meeting_id": mid, "status": "completed",
            "start_time": result.get("start_time"), "end_time": result.get("end_time"), **view,
            "duplicate_of": None}


def _append_recording(*, rec_id, meeting_id, user_id, session_uid, blob: MediaBlob, key: str):
    now = _iso(datetime.now(timezone.utc))
    media_file = {
        "id": rec_id,
        "type": blob.type,
        "format": blob.format,
        "storage_path": key,
        "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
        "file_size_bytes": len(blob.data),
        "chunk_count": 0,
        "assembled_chunk_count": 0,
        "duration_seconds": blob.duration_seconds,
        "metadata": {},
        "created_at": now,
        "is_final": True,
        "finalized_at": now,
        "finalized_by": "meeting-bundle.import",
        # The bytes are kept exactly as exported (their hash is the bundle's), so the finalizer's
        # one-time WebM seek repair is recorded as already applied rather than rewriting them.
        **({"seekable_version": 1} if blob.format == "webm" else {}),
    }
    record = {
        "id": rec_id,
        "meeting_id": meeting_id,
        "user_id": user_id,
        "session_uid": session_uid,
        "source": "import",
        "status": "completed",
        "created_at": now,
        "completed_at": now,
        "media_files": [media_file],
        "playback_url": {
            "audio": f"/recordings/{rec_id}/master?type=audio" if blob.type == "audio" else None,
            "video": f"/recordings/{rec_id}/master?type=video" if blob.type == "video" else None,
        },
    }

    def mutator(recordings):
        return list(recordings) + [record], rec_id

    return mutator
