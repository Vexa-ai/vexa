"""meeting-bundle.v1 over the REAL stores: two Postgres databases (two deployments), the production
SQLAlchemy transcript store and recording repo, two object stores, two deployment secrets.

The in-memory suite (`test_meeting_bundle.py`) drives the fakes; this drives the SQL the import
actually runs — the planned-row insert, the recordings JSONB write under its row lock, the
annotation merge, the transcript upsert and completion, the metadata-containment duplicate check —
so a fake that agrees with itself but not with Postgres cannot hide here.

Opt-in: set MEETING_API_TEST_DATABASE_URL and MEETING_API_TEST_DATABASE_URL_B to two EMPTY databases
(postgresql+asyncpg://…). Each test creates the tables it needs and drops them afterwards.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime
from hashlib import sha256

import pytest
import httpx

URL_A = os.getenv("MEETING_API_TEST_DATABASE_URL")
URL_B = os.getenv("MEETING_API_TEST_DATABASE_URL_B")
pytestmark = pytest.mark.skipif(not (URL_A and URL_B),
                                reason="set MEETING_API_TEST_DATABASE_URL and _B to two empty Postgres databases")

OWNER, IMPORTER, OTHER = 501, 902, 903
WAV = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + bytes(24) + b"\x05\x06" * 40)

_EVENT_TIME = """
CREATE OR REPLACE FUNCTION meeting_event_time(data jsonb, start_time timestamp, created_at timestamp)
RETURNS timestamp LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'), start_time, created_at);
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END $fn$;
"""


class Deployment:
    def __init__(self, url, secret):
        self.url, self.secret = url, secret

    async def __aenter__(self):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from meeting_api.app import create_app
        from meeting_api.collector.adapters import SqlAlchemyTranscriptStore
        from meeting_api.recordings.adapters import SqlAlchemyRecordingRepo
        from meeting_api.recordings.fakes import InMemoryStorage
        from meeting_api.sessions.models import Base

        self.engine = create_async_engine(self.url)
        async with self.engine.begin() as conn:
            await conn.execute(text(_EVENT_TIME))
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        self.sf = async_sessionmaker(self.engine, expire_on_commit=False)
        self.storage = InMemoryStorage()
        self.repo = SqlAlchemyRecordingRepo(self.sf)
        # In-loop ASGI client: the engine's connections belong to this test's event loop.
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(
            transcript_store=SqlAlchemyTranscriptStore(self.sf), recording_repo=self.repo,
            storage=self.storage, token_secret=self.secret)), base_url="http://meeting-api")
        return self

    async def __aexit__(self, *exc):
        from meeting_api.sessions.models import Base

        await self.client.aclose()
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def seed(self) -> int:
        from meeting_api.sessions.models import Meeting, Transcription

        key = f"recordings/{OWNER}/700000000001/sess-pg/audio/master.wav"
        async with self.sf() as db:
            m = Meeting(user_id=OWNER, platform="google_meet", platform_specific_id="pgx-abcd-efg",
                        status="completed", start_time=datetime(2026, 10, 2, 10, 0, 0),
                        end_time=datetime(2026, 10, 2, 10, 20, 0),
                        data={"title": "Postgres round trip", "metadata": {"ticket": "PG-1"},
                              "transcript_viewers": [OTHER], "webhook_secret": "whsec_pg",
                              "recordings": [{"id": 700000000001, "user_id": OWNER, "session_uid": "sess-pg",
                                              "source": "bot", "status": "completed",
                                              "created_at": "2026-10-02T10:20:00Z",
                                              "media_files": [{"id": 1, "type": "audio", "format": "wav",
                                                               "storage_path": key, "is_final": True,
                                                               "chunk_count": 0, "duration_seconds": 1200.0}]}]})
            db.add(m)
            await db.flush()
            for i, (spk, txt, s, e) in enumerate([("Ada", "Opening the call.", 0.0, 2.5),
                                                  ("Grace", "Numbers look right.", 2.5, 6.0)]):
                db.add(Transcription(meeting_id=m.id, start_time=s, end_time=e, text=txt, speaker=spk,
                                     language="en", session_uid="sess-pg", segment_id=f"sess-pg-{i}"))
            await db.commit()
            mid = m.id
        self.storage.blobs[key] = WAV
        return mid


def _parts(archive):
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


async def test_round_trip_between_two_postgres_deployments():
    async with Deployment(URL_A, "pg-secret-a") as a, Deployment(URL_B, "pg-secret-b") as b:
        mid = await a.seed()
        exported = await a.client.get(f"/meetings/{mid}/export", headers={"x-user-id": str(OWNER)})
        assert exported.status_code == 200, exported.text
        assert (await a.client.get(f"/meetings/{mid}/export", headers={"x-user-id": str(OTHER)})).status_code == 403

        preview = await b.client.post("/meetings/import?dry_run=true", content=exported.content,
                                headers={"x-user-id": str(IMPORTER)})
        assert preview.status_code == 200 and preview.json()["duplicate_of"] is None
        imported = await b.client.post("/meetings/import", content=exported.content, headers={"x-user-id": str(IMPORTER)})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["meeting_id"]
        again = await b.client.post("/meetings/import", content=exported.content, headers={"x-user-id": str(IMPORTER)})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "duplicate_import"

        row = (await b.client.get(f"/meetings/{new_id}", headers={"x-user-id": str(IMPORTER)})).json()
        assert row["status"] == "completed" and row["user_id"] == IMPORTER
        assert row["data"]["metadata"]["ticket"] == "PG-1"
        assert row["data"]["metadata"]["imported_from"]["source_meeting_id"] == mid
        assert "transcript_viewers" not in row["data"] and "webhook_secret" not in row["data"]
        assert (await b.client.get(f"/meetings/{new_id}", headers={"x-user-id": str(OTHER)})).status_code == 404

        recs = (await b.client.get(f"/recordings?meeting_id={new_id}", headers={"x-user-id": str(IMPORTER)})).json()
        assert recs["total"] == 1
        rec = recs["recordings"][0]
        raw = await b.client.get(f"/recordings/{rec['id']}/media/{rec['media_files'][0]['id']}/raw",
                           headers={"x-user-id": str(IMPORTER)})
        assert raw.status_code == 200 and raw.content == WAV

        back = await b.client.get(f"/meetings/{new_id}/export", headers={"x-user-id": str(IMPORTER)})
        src, dst = _parts(exported.content), _parts(back.content)
        assert sha256(dst["transcript.json"]).digest() == sha256(src["transcript.json"]).digest()
        assert dst["media/recording-1-audio.wav"] == src["media/recording-1-audio.wav"]
        assert json.loads(dst["meeting.json"])["start_time"] == json.loads(src["meeting.json"])["start_time"]
