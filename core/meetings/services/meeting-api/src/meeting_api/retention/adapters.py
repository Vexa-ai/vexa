"""Production PostgreSQL and object-storage adapters for Minutes erasure.

Heavy runtime dependencies stay lazy so the offline meeting-api suite does not need boto3,
SQLAlchemy or asyncpg.  Tests inject protocol-compatible clients/factories into these adapters.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import re
from typing import Optional

from ..recordings.ports import MEETING_WRITE_LOCK_NAMESPACE
from .ports import ErasurePlan


_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._:-]+$")


def recording_prefixes_for_meeting(user_id: int | str, data: dict) -> tuple[str, ...]:
    """Derive narrow recording/session prefixes from one owned meeting's JSONB.

    Stored paths are treated as integrity evidence, not as deletion instructions: every path must
    agree with the owner/recording/session identity before its derived prefix can reach storage.
    """

    prefixes: set[str] = set()
    owner = str(user_id)
    if not _KEY_SEGMENT.fullmatch(owner) or owner in {".", ".."}:
        raise ValueError("recording owner identity is invalid")
    intents = data.get("zaki_recording_prefixes", []) if isinstance(data, dict) else []
    if not isinstance(intents, list):
        raise ValueError("recording prefix intents are invalid")
    for prefix in intents:
        parts = prefix.split("/") if isinstance(prefix, str) else []
        if (
            len(parts) != 5
            or parts[0] != "recordings"
            or parts[1] != owner
            or parts[-1] != ""
            or any(
                not _KEY_SEGMENT.fullmatch(part) or part in {".", ".."}
                for part in parts[1:-1]
            )
        ):
            raise ValueError("recording prefix intent is invalid")
        prefixes.add(prefix)
    recordings = data.get("recordings", []) if isinstance(data, dict) else []
    if not isinstance(recordings, list):
        raise ValueError("recording metadata is invalid")
    for recording in recordings:
        if not isinstance(recording, dict):
            raise ValueError("recording metadata is invalid")
        media_files = recording.get("media_files", [])
        if not isinstance(media_files, list) or any(
            not isinstance(media_file, dict) for media_file in media_files
        ):
            raise ValueError("recording metadata is invalid")
        paths = [media_file.get("storage_path") for media_file in media_files]
        paths = [path for path in paths if path]
        if not paths:
            continue
        recording_id = str(recording.get("id", ""))
        session_uid = str(recording.get("session_uid", ""))
        if (
            not _KEY_SEGMENT.fullmatch(recording_id)
            or recording_id in {".", ".."}
            or not _KEY_SEGMENT.fullmatch(session_uid)
            or session_uid in {".", ".."}
        ):
            raise ValueError("recording storage identity is invalid")
        prefix = f"recordings/{owner}/{recording_id}/{session_uid}/"
        for path in paths:
            if not isinstance(path, str) or not path.startswith(prefix):
                raise ValueError("recording storage identity mismatch")
        prefixes.add(prefix)
    return tuple(sorted(prefixes))


def _summary_document_count(data: dict) -> int:
    count = 0
    summaries = data.get("summaries") if isinstance(data, dict) else None
    if isinstance(summaries, list):
        count += len(summaries)
    if isinstance(data, dict) and data.get("summary") is not None:
        count += 1
    return count


class SqlAlchemyRetentionRepo:
    """Owner-scoped erasure over the production ``meetings`` schema.

    The repository uses the same two-key PostgreSQL advisory-lock namespace as recording writers.
    Erasure takes the transaction-scoped exclusive lock, waits for shared writer locks to drain,
    stores ``data.zaki_retention.state=erasing``, then commits. Later writers acquire their shared
    lock and observe that durable state before touching object storage.
    """

    LOCK_NAMESPACE = MEETING_WRITE_LOCK_NAMESPACE

    def __init__(self, session_factory, *, statement_factory=None):
        self._session_factory = session_factory
        self._statement_factory = statement_factory

    def _statement(self, sql: str):
        if self._statement_factory is not None:
            return self._statement_factory(sql)
        from sqlalchemy import text

        return text(sql)

    @staticmethod
    def _meeting_id(meeting_id: str | int) -> int | None:
        try:
            value = int(meeting_id)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    async def _exclusive_lock(self, db, meeting_id: int) -> None:
        await db.execute(
            self._statement(
                "SELECT pg_advisory_xact_lock(:lock_namespace, :meeting_id)"
            ),
            {"lock_namespace": self.LOCK_NAMESPACE, "meeting_id": meeting_id},
        )

    async def _owned_meeting(self, db, meeting_id: int, user_id: str | int):
        result = await db.execute(
            self._statement(
                "SELECT id, user_id, data FROM meetings WHERE id = :meeting_id FOR UPDATE"
            ),
            {"meeting_id": meeting_id},
        )
        row = result.mappings().first()
        if row is None or str(row["user_id"]) != str(user_id):
            return None
        return row

    async def _persist_retention(self, db, meeting_id: int, metadata: dict) -> None:
        await db.execute(
            self._statement(
                "UPDATE meetings SET data = jsonb_set(COALESCE(data, '{}'::jsonb), "
                "'{zaki_retention}', CAST(:retention AS jsonb), true) "
                "WHERE id = :meeting_id"
            ),
            {"meeting_id": meeting_id, "retention": json.dumps(metadata, sort_keys=True)},
        )

    async def begin_erasure(self, user_id: str, meeting_id: str) -> ErasurePlan | None:
        mid = self._meeting_id(meeting_id)
        if mid is None:
            return None
        async with self._session_factory() as db:
            await self._exclusive_lock(db, mid)
            row = await self._owned_meeting(db, mid, user_id)
            if row is None:
                return None
            data = dict(row["data"]) if isinstance(row["data"], dict) else {}
            metadata = data.get("zaki_retention")
            if not isinstance(metadata, dict) or metadata.get("state") != "erasing":
                prefixes = recording_prefixes_for_meeting(row["user_id"], data)
                transcript_result = await db.execute(
                    self._statement(
                        "SELECT count(*) FROM transcriptions WHERE meeting_id = :meeting_id"
                    ),
                    {"meeting_id": mid},
                )
                transcript_rows = int(transcript_result.scalar_one())
                summary_documents = _summary_document_count(data)
                metadata = {
                    "state": "erasing",
                    "recording_prefixes": list(prefixes),
                    "recording_objects": None,
                    "transcript_rows": transcript_rows,
                    "summary_documents": summary_documents,
                }
                await self._persist_retention(db, mid, metadata)
                await db.commit()
            else:
                prefixes = tuple(metadata.get("recording_prefixes") or ())
                transcript_rows = int(metadata.get("transcript_rows") or 0)
                summary_documents = int(metadata.get("summary_documents") or 0)
            return ErasurePlan(
                user_id=str(user_id),
                meeting_id=str(meeting_id),
                transcript_rows=transcript_rows,
                summary_documents=summary_documents,
                recording_prefixes=tuple(prefixes),
                recording_objects=metadata.get("recording_objects"),
            )

    async def record_object_census(
        self, plan: ErasurePlan, recording_objects: int
    ) -> ErasurePlan:
        mid = self._meeting_id(plan.meeting_id)
        if mid is None or recording_objects < 0:
            raise RuntimeError("meeting erasure census is invalid")
        async with self._session_factory() as db:
            await self._exclusive_lock(db, mid)
            row = await self._owned_meeting(db, mid, plan.user_id)
            if row is None:
                raise RuntimeError("meeting erasure census lost its owner")
            data = dict(row["data"]) if isinstance(row["data"], dict) else {}
            metadata = data.get("zaki_retention")
            if not isinstance(metadata, dict) or metadata.get("state") != "erasing":
                raise RuntimeError("meeting erasure census has no durable plan")
            stable_count = metadata.get("recording_objects")
            if stable_count is None:
                stable_count = int(recording_objects)
                metadata = {**metadata, "recording_objects": stable_count}
                await self._persist_retention(db, mid, metadata)
                await db.commit()
            return replace(plan, recording_objects=int(stable_count))

    async def commit_erasure(self, plan: ErasurePlan) -> dict[str, int]:
        mid = self._meeting_id(plan.meeting_id)
        if mid is None:
            return {"meeting_rows": 0, "transcript_rows": 0, "summary_documents": 0}
        async with self._session_factory() as db:
            await self._exclusive_lock(db, mid)
            row = await self._owned_meeting(db, mid, plan.user_id)
            if row is None:
                return {"meeting_rows": 0, "transcript_rows": 0, "summary_documents": 0}
            data = row["data"] if isinstance(row["data"], dict) else {}
            metadata = data.get("zaki_retention")
            if not isinstance(metadata, dict) or metadata.get("state") != "erasing":
                raise RuntimeError("meeting erasure database commit has no durable plan")
            transcript_result = await db.execute(
                self._statement(
                    "DELETE FROM transcriptions WHERE meeting_id = :meeting_id"
                ),
                {"meeting_id": mid},
            )
            await db.execute(
                self._statement(
                    "DELETE FROM meeting_sessions WHERE meeting_id = :meeting_id"
                ),
                {"meeting_id": mid},
            )
            meeting_result = await db.execute(
                self._statement(
                    "DELETE FROM meetings WHERE id = :meeting_id AND user_id = :user_id"
                ),
                {"meeting_id": mid, "user_id": int(row["user_id"])},
            )
            await db.commit()
            return {
                "meeting_rows": int(meeting_result.rowcount or 0),
                "transcript_rows": int(transcript_result.rowcount or 0),
                "summary_documents": plan.summary_documents,
            }


class S3RetentionStorage:
    """Prefix census/deletion over one S3 or MinIO bucket.

    Unversioned current objects, or every version and delete marker in a versioned/suspended bucket,
    are deleted in batches of at most 1,000. Object Lock/legal-hold policy remains an Infra launch
    gate: storage refusal fails erasure and preserves the durable retry plan.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        client=None,
    ):
        self._bucket = bucket
        self._endpoint = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = client

    def _c(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
        return self._client

    async def _run(self, fn, *args, **kwargs):
        call = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError as cancelled:
            try:
                await call
            finally:
                raise cancelled

    async def _is_versioned(self) -> bool:
        response = await self._run(
            self._c().get_bucket_versioning,
            Bucket=self._bucket,
        )
        return response.get("Status") in {"Enabled", "Suspended"}

    async def _count_versions(self, prefix: str) -> int:
        count = 0
        key_marker: Optional[str] = None
        version_marker: Optional[str] = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix, "MaxKeys": 1000}
            if key_marker is not None:
                kwargs["KeyMarker"] = key_marker
            if version_marker is not None:
                kwargs["VersionIdMarker"] = version_marker
            response = await self._run(self._c().list_object_versions, **kwargs)
            count += len(response.get("Versions", []))
            count += len(response.get("DeleteMarkers", []))
            if not response.get("IsTruncated"):
                return count
            key_marker = response.get("NextKeyMarker")
            version_marker = response.get("NextVersionIdMarker")
            if key_marker is None:
                raise RuntimeError(
                    "object-version census returned a truncated page without a cursor"
                )

    async def _delete_versions(self, prefix: str) -> int:
        deleted = 0
        while True:
            # Re-read the first remaining page after each delete; advancing a stale cursor can skip
            # versions on S3-compatible implementations with positional pagination.
            response = await self._run(
                self._c().list_object_versions,
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=1000,
            )
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in response.get("Versions", []) + response.get("DeleteMarkers", [])
            ]
            if not objects:
                return deleted
            result = await self._run(
                self._c().delete_objects,
                Bucket=self._bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
            if result.get("Errors"):
                raise RuntimeError("object storage reported an incomplete version delete")
            deleted += len(objects)

    async def count_prefix(self, prefix: str) -> int:
        if await self._is_versioned():
            return await self._count_versions(prefix)
        count = 0
        token: Optional[str] = None
        while True:
            kwargs = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if token:
                kwargs["ContinuationToken"] = token
            response = await self._run(self._c().list_objects_v2, **kwargs)
            count += len(response.get("Contents", []))
            if not response.get("IsTruncated"):
                return count
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError("object census returned a truncated page without a cursor")

    async def delete_prefix(self, prefix: str) -> int:
        if await self._is_versioned():
            return await self._delete_versions(prefix)
        deleted = 0
        while True:
            # Always read the first remaining page. Continuing from a token after deleting earlier
            # pages can skip keys on S3-compatible implementations whose cursor is positional.
            response = await self._run(
                self._c().list_objects_v2,
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=1000,
            )
            keys = [obj["Key"] for obj in response.get("Contents", [])]
            if not keys:
                return deleted
            result = await self._run(
                self._c().delete_objects,
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
            )
            if result.get("Errors"):
                raise RuntimeError("object storage reported an incomplete prefix delete")
            deleted += len(keys)
