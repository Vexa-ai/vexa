"""Production adapters — the real ``Storage`` (MinIO/S3) + ``RecordingRepo`` (SQLAlchemy).

Thin translations of the ports to the concrete clients, as the parent's
``recordings.internal_upload_recording`` (storage upload + the ``SELECT ... FOR UPDATE`` row lock on
``meeting.data``) and ``recording_finalizer`` (master build + upload) do. They carry NO test logic.

Heavy imports (boto3/minio, SQLAlchemy) are LAZY (inside the methods / ``build_production_router``)
so the package imports + unit-tests with the in-memory fakes without those runtime deps in the gate
venv — which is why ``pyproject.toml`` needs no extra pins.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import os
from typing import Optional

from .ports import (
    MEETING_WRITE_LOCK_NAMESPACE,
    RECORDING_CHUNK_LOCK_NAMESPACE,
    RecordingWriteRefused,
)


class S3Storage:
    """``Storage`` over an S3/MinIO bucket (boto3). Lazy client so the package imports without boto3."""

    def __init__(self, *, bucket: str, endpoint_url: Optional[str] = None,
                 access_key: Optional[str] = None, secret_key: Optional[str] = None):
        self._bucket = bucket
        self._endpoint = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = None

    def _c(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3", endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key, aws_secret_access_key=self._secret_key,
            )
        return self._client

    async def _run(self, fn, *args, **kwargs):
        """Run a BLOCKING boto3 call off the event loop (G4). boto3 is synchronous; calling it directly
        inside an async method stalls the whole control plane (a multi-MB master finalize fetches many
        objects). ``asyncio.to_thread`` offloads it to the default thread pool so the loop keeps serving
        lifecycle/webhook/ws traffic. Cancellation waits for the sync call: otherwise a write lease
        could release while its worker thread can still create an object after erasure. Overridable in
        tests."""
        import asyncio

        call = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError as cancelled:
            try:
                await call
            finally:
                raise cancelled

    async def upload(self, key: str, data: bytes, *, content_type: str) -> None:
        await self._run(self._c().put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    async def delete(self, key: str) -> None:
        await self._run(self._c().delete_object, Bucket=self._bucket, Key=key)

    async def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if token is not None:
                kwargs["ContinuationToken"] = token
            response = await self._run(self._c().list_objects_v2, **kwargs)
            keys.extend(obj["Key"] for obj in response.get("Contents", []))
            if not response.get("IsTruncated"):
                return sorted(keys)
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError(
                    "recording object listing returned a truncated page without a cursor"
                )

    async def get(self, key: str) -> bytes:
        obj = await self._run(self._c().get_object, Bucket=self._bucket, Key=key)
        return await self._run(obj["Body"].read)

    async def size(self, key: str) -> int:
        head = await self._run(self._c().head_object, Bucket=self._bucket, Key=key)
        return int(head["ContentLength"])

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        # Pass the byte range through to S3 (inclusive offsets) so we fetch only the requested window.
        resp = await self._run(self._c().get_object, Bucket=self._bucket, Key=key, Range=f"bytes={start}-{end}")
        return await self._run(resp["Body"].read)

    async def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            await self._run(self._c().head_object, Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False


class SqlAlchemyRecordingRepo:
    """``RecordingRepo`` over a SQLAlchemy-async ``session_factory`` (``meetings`` /
    ``meeting_sessions``; recordings live in ``meetings.data`` JSONB)."""

    def __init__(self, session_factory, *, statement_factory=None):
        self._session_factory = session_factory
        self._statement_factory = statement_factory

    def _statement(self, sql: str):
        if self._statement_factory is not None:
            return self._statement_factory(sql)
        from sqlalchemy import text

        return text(sql)

    @asynccontextmanager
    async def recording_write(self, meeting_id: int):
        """Hold a session-level shared advisory lock across object + JSONB mutation.

        The erasure adapter queues for the exclusive transaction lock in the same namespace, then
        stores ``zaki_retention.state=erasing`` before releasing it. A writer queued behind erasure
        therefore acquires only after that commit and is refused by the state check.
        """

        async with self._session_factory() as db:
            params = {
                "lock_namespace": MEETING_WRITE_LOCK_NAMESPACE,
                "meeting_id": int(meeting_id),
            }
            await db.execute(
                self._statement(
                    "SELECT pg_advisory_lock_shared(:lock_namespace, :meeting_id)"
                ),
                params,
            )
            try:
                result = await db.execute(
                    self._statement(
                        "SELECT data FROM meetings WHERE id = :meeting_id"
                    ),
                    {"meeting_id": int(meeting_id)},
                )
                row = result.mappings().first()
                data = row["data"] if row and isinstance(row["data"], dict) else {}
                retention = data.get("zaki_retention") if row else None
                if row is None or (
                    isinstance(retention, dict) and retention.get("state") == "erasing"
                ):
                    raise RecordingWriteRefused("meeting is not writable")
                yield
            finally:
                await db.execute(
                    self._statement(
                        "SELECT pg_advisory_unlock_shared(:lock_namespace, :meeting_id)"
                    ),
                    params,
                )

    @asynccontextmanager
    async def chunk_write(self, key: str):
        """Hold one cross-process exclusive advisory lock for a deterministic chunk key."""

        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big", signed=True)
        params = {
            "lock_namespace": RECORDING_CHUNK_LOCK_NAMESPACE,
            "chunk_lock_id": lock_id,
        }
        async with self._session_factory() as db:
            await db.execute(
                self._statement(
                    "SELECT pg_advisory_lock(:lock_namespace, :chunk_lock_id)"
                ),
                params,
            )
            try:
                yield
            finally:
                await db.execute(
                    self._statement(
                        "SELECT pg_advisory_unlock(:lock_namespace, :chunk_lock_id)"
                    ),
                    params,
                )

    async def register_recording_prefix(self, meeting_id: int, prefix: str) -> None:
        from sqlalchemy.orm.attributes import flag_modified

        async with self._session_factory() as db:
            meeting = await self._meeting(db, meeting_id)
            if meeting is None:
                raise RecordingWriteRefused("meeting is not writable")
            data = dict(meeting.data) if isinstance(meeting.data, dict) else {}
            retention = data.get("zaki_retention")
            if isinstance(retention, dict) and retention.get("state") == "erasing":
                raise RecordingWriteRefused("meeting is not writable")
            prefixes = list(data.get("zaki_recording_prefixes") or [])
            if prefix in prefixes:
                return
            prefixes.append(prefix)
            data["zaki_recording_prefixes"] = prefixes
            meeting.data = data
            flag_modified(meeting, "data")
            await db.commit()

    async def find_session(self, session_uid):
        from sqlalchemy import select

        from ..sessions.models import MeetingSession

        async with self._session_factory() as db:
            s = (
                await db.execute(
                    select(MeetingSession).where(MeetingSession.session_uid == session_uid)
                )
            ).scalars().first()
            return {"meeting_id": s.meeting_id, "session_uid": s.session_uid} if s else None

    async def _meeting(self, db, meeting_id):
        from sqlalchemy import select

        from ..sessions.models import Meeting

        return (
            await db.execute(select(Meeting).where(Meeting.id == meeting_id).with_for_update())
        ).scalars().first()

    async def get_recordings(self, meeting_id):
        async with self._session_factory() as db:
            m = await self._meeting(db, meeting_id)
            data = m.data if isinstance(m.data, dict) else {}
            return list(data.get("recordings", []))

    async def put_recordings(self, meeting_id, recordings):
        from sqlalchemy.orm.attributes import flag_modified

        async with self._session_factory() as db:
            m = await self._meeting(db, meeting_id)
            data = dict(m.data) if isinstance(m.data, dict) else {}
            data["recordings"] = list(recordings)
            m.data = data
            flag_modified(m, "data")
            await db.commit()

    async def mutate_recordings(self, meeting_id, mutator):
        """Atomic read→modify→write under ONE ``SELECT … FOR UPDATE`` row lock (G3). The lock spans the
        whole mutation (held from the read through commit), so concurrent chunk-upload / finalize calls
        serialize instead of clobbering each other (the old get+put released the lock between)."""
        from sqlalchemy.orm.attributes import flag_modified

        async with self._session_factory() as db:
            m = await self._meeting(db, meeting_id)  # SELECT … FOR UPDATE
            data = dict(m.data) if isinstance(m.data, dict) else {}
            recordings = list(data.get("recordings", []))
            new_recordings, result = mutator(recordings)
            data["recordings"] = list(new_recordings)
            m.data = data
            flag_modified(m, "data")
            await db.commit()
            return result

    async def owner_of(self, meeting_id):
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            m = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalars().first()
            return m.user_id if m else None

    async def list_meeting_recordings(self, user_id):
        from sqlalchemy import select

        from ..sessions.models import Meeting

        async with self._session_factory() as db:
            rows = (
                await db.execute(select(Meeting).where(Meeting.user_id == user_id))
            ).scalars().all()
            out = []
            for m in rows:
                data = m.data if isinstance(m.data, dict) else {}
                for r in data.get("recordings", []):
                    out.append({**r, "meeting_id": m.id})
            return out


def build_production_router(*, database_url: Optional[str] = None):
    """Construct the recordings router with real MinIO/S3 + SQLAlchemy adapters from env."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from .router import build_router

    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@postgres:5432/vexa"
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = S3Storage(
        bucket=os.getenv("RECORDING_BUCKET", "recordings"),
        endpoint_url=os.getenv("S3_ENDPOINT"),
        access_key=os.getenv("S3_ACCESS_KEY"),
        secret_key=os.getenv("S3_SECRET_KEY"),
    )
    return build_router(SqlAlchemyRecordingRepo(session_factory), storage)
