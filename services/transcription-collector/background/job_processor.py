"""
Background job processor for post-meeting transcription jobs.

Polls the database for pending TranscriptionJob rows, downloads the audio
from object storage, sends it to the transcription service, and stores the
resulting segments in PostgreSQL.
"""

import logging
import asyncio
import uuid
from datetime import datetime
from typing import Optional

import httpx

from shared_models.database import async_session_local
from shared_models.models import TranscriptionJob, Recording, MediaFile, Transcription, MeetingSession, User
from shared_models.storage import create_storage_client
from shared_models.webhook_url import validate_webhook_url
from sqlalchemy import select, and_

from config import TRANSCRIPTION_SERVICE_URL

logger = logging.getLogger(__name__)

JOB_POLL_INTERVAL = 15  # seconds between polls


async def process_transcription_jobs():
    """
    Long-running background task that polls for pending transcription jobs
    and processes them one at a time.
    """
    logger.info("Transcription job processor started")

    while True:
        try:
            await asyncio.sleep(JOB_POLL_INTERVAL)
            await _process_next_job()
        except asyncio.CancelledError:
            logger.info("Transcription job processor cancelled")
            break
        except Exception as e:
            logger.error(f"Transcription job processor unexpected error: {e}", exc_info=True)
            await asyncio.sleep(JOB_POLL_INTERVAL)


async def _process_next_job():
    """Find and process the oldest pending transcription job."""
    async with async_session_local() as db:
        # Grab the oldest pending job
        stmt = (
            select(TranscriptionJob)
            .where(TranscriptionJob.status == "pending")
            .order_by(TranscriptionJob.created_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        job = result.scalars().first()

        if not job:
            return  # nothing to do

        logger.info(f"Processing transcription job {job.id} for recording {job.recording_id}")

        # Mark as processing
        job.status = "processing"
        job.started_at = datetime.utcnow()
        await db.commit()

    # Do the actual work outside the db session to avoid long-held connections
    try:
        await _run_transcription(job.id)
    except Exception as e:
        logger.error(f"Transcription job {job.id} failed: {e}", exc_info=True)
        async with async_session_local() as db:
            job = await db.get(TranscriptionJob, job.id)
            if job:
                job.status = "failed"
                job.error_message = str(e)[:2000]
                await db.commit()


async def _run_transcription(job_id: int):
    """
    Core transcription pipeline for a single job:
    1. Download audio from object storage
    2. Send to transcription service
    3. Store resulting segments in PostgreSQL
    4. Update job status
    """
    async with async_session_local() as db:
        job = await db.get(TranscriptionJob, job_id)
        if not job:
            return

        recording = await db.get(Recording, job.recording_id)
        if not recording:
            job.status = "failed"
            job.error_message = "Recording not found"
            await db.commit()
            return

        # Find the audio media file
        stmt = select(MediaFile).where(
            and_(MediaFile.recording_id == recording.id, MediaFile.type == "audio")
        )
        result = await db.execute(stmt)
        audio_file = result.scalars().first()
        if not audio_file:
            job.status = "failed"
            job.error_message = "No audio file found for recording"
            await db.commit()
            return

        storage_path = audio_file.storage_path
        media_format = audio_file.format

    # Download audio from storage
    logger.info(f"Job {job_id}: downloading audio from {storage_path}")
    storage = create_storage_client()
    audio_data = storage.download_file(storage_path)
    logger.info(f"Job {job_id}: downloaded {len(audio_data)} bytes")

    # Send to transcription service
    content_type_map = {
        "wav": "audio/wav",
        "webm": "audio/webm",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
    }
    content_type = content_type_map.get(media_format, "application/octet-stream")
    filename = f"recording.{media_format}"

    logger.info(f"Job {job_id}: sending to transcription service at {TRANSCRIPTION_SERVICE_URL}")

    async with async_session_local() as db:
        job = await db.get(TranscriptionJob, job_id)
        language = job.language
        task = job.task

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
        form_data = {
            "model": (None, "large-v3-turbo"),
            "response_format": (None, "verbose_json"),
            "task": (None, task or "transcribe"),
        }
        if language:
            form_data["language"] = (None, language)

        files = {"file": (filename, audio_data, content_type)}

        response = await client.post(
            f"{TRANSCRIPTION_SERVICE_URL}/v1/audio/transcriptions",
            files=files,
            data={k: v[1] for k, v in form_data.items()},
        )

    if response.status_code != 200:
        error_msg = f"Transcription service returned {response.status_code}: {response.text[:500]}"
        logger.error(f"Job {job_id}: {error_msg}")
        async with async_session_local() as db:
            job = await db.get(TranscriptionJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = error_msg
                await db.commit()
        return

    result_json = response.json()
    segments = result_json.get("segments", [])
    detected_language = result_json.get("language")

    logger.info(f"Job {job_id}: received {len(segments)} segments, language={detected_language}")

    # Generate a unique session_uid for this transcription job's segments
    async with async_session_local() as db:
        job = await db.get(TranscriptionJob, job_id)
        if not job:
            return

        recording = await db.get(Recording, job.recording_id)
        session_uid = f"post_{job.id}_{recording.session_uid or uuid.uuid4().hex[:8]}"
        meeting_id = job.meeting_id

        # Create a MeetingSession for this post-transcription so segments
        # can be merged with a session_start_time
        if meeting_id:
            meeting_session = MeetingSession(
                meeting_id=meeting_id,
                session_uid=session_uid,
                session_start_time=recording.created_at or datetime.utcnow(),
            )
            db.add(meeting_session)

        # Store segments as Transcription rows
        transcription_rows = []
        for seg in segments:
            t = Transcription(
                meeting_id=meeting_id,
                start_time=seg.get("start", 0.0),
                end_time=seg.get("end", 0.0),
                text=seg.get("text", ""),
                language=detected_language or language,
                session_uid=session_uid,
                created_at=datetime.utcnow(),
            )
            transcription_rows.append(t)

        if transcription_rows:
            db.add_all(transcription_rows)

        # Update job as completed
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.segments_count = len(segments)
        job.session_uid = session_uid

        await db.commit()

    logger.info(f"Job {job_id}: completed — stored {len(segments)} segments with session_uid={session_uid}")

    # Fire webhook
    await _send_job_webhook(job.user_id, "transcription_job.completed", {
        "transcription_job": {
            "id": job_id,
            "recording_id": job.recording_id,
            "meeting_id": meeting_id,
            "status": "completed",
            "segments_count": len(segments),
            "session_uid": session_uid,
            "language": detected_language,
        }
    })


async def _send_job_webhook(user_id: int, event_type: str, payload: dict):
    """Fire-and-forget webhook for transcription job events."""
    try:
        async with async_session_local() as db:
            user = await db.get(User, user_id)
            if not user or not user.data or not isinstance(user.data, dict):
                return
            webhook_url = user.data.get('webhook_url')
            if not webhook_url:
                return
            try:
                validate_webhook_url(webhook_url)
            except ValueError:
                return

            headers = {'Content-Type': 'application/json'}
            secret = user.data.get('webhook_secret')
            if secret and isinstance(secret, str) and secret.strip():
                headers['Authorization'] = f'Bearer {secret.strip()}'

        async with httpx.AsyncClient(follow_redirects=True) as client:
            await client.post(
                webhook_url,
                json={'event_type': event_type, **payload},
                timeout=30.0,
                headers=headers,
            )
    except Exception as e:
        logger.warning(f"Job webhook ({event_type}) failed for user {user_id}: {e}")
