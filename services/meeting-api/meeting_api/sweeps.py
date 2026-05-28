"""v0.10.5 Pack E.3.2 + Pack D.2 + H.4 + E.1-sibling sweeps.

Long-running idle-loop equivalent for meeting-api. Each sweep is a
periodic scan that catches state-machine rows that genuinely got
stuck — escapes from the canonical durable mechanisms (Pack J's
exit-callback in callbacks.py, Pack E.1's chunk-finalize outbox, etc).

Active responsibilities:
  - Pack E.3.2: stale-stopping sweep (postgres scan + force-finalize).
  - Pack H.4: aggregation retry for transient infra failures.
  - Pack E.1-sibling: unfinalized recording repair/finalize.
  - Pack D.2 (#266): durable container-stop outbox consumer
    (Redis Stream `meeting-api:container-stops` → runtime-api DELETE
    with retry + DLQ). The producer side is `_delayed_container_stop`
    in meetings.py, which now XADDs onto the stream instead of running
    an in-process timer.

Principle filter: every sweep is OBSERVABLE. Rows found = the canonical
mechanism failed somewhere; operators must see it. Loud warning logs
on each row + a per-iteration summary count. Pack M wires Prometheus
counter increments here when metrics infra ships.

Pattern mirrors webhook_retry_worker.py — same shape, different
responsibility. Spawned from main.py startup alongside the retry worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from .models import Meeting, MeetingSession
from .schemas import MeetingStatus, MeetingCompletionReason

logger = logging.getLogger("meeting_api.sweeps")

# v0.10.5 Pack E.3.2 — stale-stopping sweep config.
#
# Threshold is generous: the runtime-api exit callback (260421 Pack J)
# is the canonical durable mechanism for stopping → completed; legitimate
# stops complete in well under 90 s. A row stuck in 'stopping' for 5+ min
# means the canonical path genuinely failed — log loud, force-finalize.
STALE_STOPPING_THRESHOLD_SECONDS = 300  # 5 min
STALE_STOPPING_POLL_INTERVAL = 60  # check every 60 s
UNFINALIZED_RECORDINGS_MIN_AGE_SECONDS = 5
UNFINALIZED_RECORDINGS_LIMIT = 100

# v0.10.5 Pack K.5 (meeting-api side analog).
# Module-level state for /health probe / Pack M metrics.
sweep_iterations: int = 0
sweep_last_iteration_at: float = 0.0

_stop_event: Optional[asyncio.Event] = None


async def _sweep_stale_stopping(
    db_session_factory: Callable[[], AsyncSession],
) -> int:
    """One iteration of the stale-stopping sweep.

    Scans for rows where status='stopping' AND the time since the meeting
    last *progressed* (last status_transition.timestamp/at, fall back to
    created_at) exceeds STALE_STOPPING_THRESHOLD_SECONDS. Force-completes
    each with
    `completion_reason=STOPPED` + transition_reason='stale_stopping_sweep'
    so the source is visible in audit logs.

    #313 — pre-fix used `updated_at` as the staleness predicate, which is
    bumped by every webhook-retry. A meeting stuck in 'stopping' with
    active webhook retries kept looking fresh and the sweep never fired.
    Now we read the immutable transition timestamps from
    `data.status_transition` (append-only history), which reflects actual
    progress; webhook retries do not append to that list.

    Returns the number of rows swept. Operators reading logs see:
      WARNING [sweep] meeting <id> stuck stopping for X s — finalizing
    Each row found indicates the canonical exit-callback path failed.

    Idempotent: force-completing an already-completed meeting is a no-op
    (status is already terminal).
    """
    from datetime import datetime, timedelta
    from .meetings import update_meeting_status, publish_meeting_status_change, get_redis

    threshold = datetime.utcnow() - timedelta(seconds=STALE_STOPPING_THRESHOLD_SECONDS)
    swept = 0

    async with db_session_factory() as db:
        # SQL pre-filter: status='stopping' AND created_at < threshold.
        # created_at is immutable so it's safe; updated_at is poisoned by
        # webhook retries (#313) and other JSONB writes, so it cannot prove
        # lifecycle progress. Post-filter by status_transition below.
        stmt = (
            select(Meeting)
            .where(Meeting.status == MeetingStatus.STOPPING.value)
            .where(Meeting.created_at < threshold)
            .limit(200)  # candidate cap — we post-filter in Python
        )
        candidates = (await db.execute(stmt)).scalars().all()

        # Post-filter: compute the actual last-progress timestamp from
        # data.status_transition (append-only). Falls back to created_at
        # for rows missing the JSONB history (legacy data).
        rows = []
        for meeting in candidates:
            data = (meeting.data or {}) if isinstance(meeting.data, dict) else {}
            transitions = data.get("status_transition") or []
            last_progress_at = meeting.created_at
            for transition in transitions:
                if not isinstance(transition, dict):
                    continue
                # Pack 4 writes 'timestamp'; pre-pack-4 history wrote 'at'.
                at_str = transition.get("timestamp") or transition.get("at")
                if not at_str:
                    continue
                try:
                    at_dt = datetime.fromisoformat(at_str.replace("Z", "+00:00"))
                    # Strip tzinfo to compare with naive utcnow()-derived threshold.
                    if at_dt.tzinfo is not None:
                        at_dt = at_dt.replace(tzinfo=None)
                except (TypeError, ValueError, AttributeError):
                    continue
                if at_dt > last_progress_at:
                    last_progress_at = at_dt
            if last_progress_at < threshold:
                rows.append((meeting, last_progress_at))
            if len(rows) >= 50:  # bound work per iteration
                break

        for meeting, last_progress_at in rows:
            stuck_for = (datetime.utcnow() - last_progress_at).total_seconds()
            logger.warning(
                f"[sweep] meeting {meeting.id} stuck stopping for {stuck_for:.0f}s — "
                f"finalizing via stale-stopping sweep "
                f"(canonical exit-callback path appears to have failed)"
            )
            try:
                # Use Pack J's classifier to route correctly — even though
                # we're forcing the finalize, the classifier's principle
                # (positive proof of success vs default-to-failed) still
                # applies. If the meeting genuinely had no segments, this
                # routes to STOPPED_WITH_NO_AUDIO; if it ran clean, STOPPED.
                from .callbacks import _classify_stopped_exit
                target_status, classified_reason = await _classify_stopped_exit(
                    meeting, db, MeetingCompletionReason.STOPPED
                )
                success = await update_meeting_status(
                    meeting,
                    target_status,
                    db,
                    completion_reason=classified_reason,
                    transition_reason="stale_stopping_sweep",
                    transition_metadata={
                        "sweep_source": "Pack E.3.2",
                        "stuck_for_seconds": int(stuck_for),
                        "pack_j_classification": classified_reason.value,
                    },
                )
                if success:
                    swept += 1
                    # Notify dashboard via WS pubsub
                    redis_client = get_redis()
                    if redis_client:
                        await publish_meeting_status_change(
                            meeting.id,
                            target_status.value,
                            redis_client,
                            meeting.platform,
                            meeting.platform_specific_id,
                            meeting.user_id,
                        )
            except Exception as e:
                logger.error(
                    f"[sweep] failed to finalize stuck meeting {meeting.id}: {e}",
                    exc_info=True,
                )

    return swept


async def _sweep_aggregation_retry(
    db_session_factory: Callable[[], AsyncSession],
) -> int:
    """v0.10.5 Pack H.4 — retry meetings stuck on transient-infra aggregation failure.

    Scans `data->>'aggregation_failure_class' = 'transient_infra'` AND
    `data->>'aggregation_last_retry_at'` older than the next-attempt
    backoff window. For each, re-attempts aggregate_transcription. On
    success: clears failure_class. On 24-attempt budget exhaustion
    (~7 days at exponential backoff): flips to 'permanent_infra' +
    fires critical alert (Pack M wires the actual Prometheus counter
    when metrics infra ships).

    Returns count of rows successfully retried this iteration.
    """
    from datetime import datetime, timedelta
    from .models import Meeting

    BUDGET_ATTEMPTS = 24  # 7 days at exponential backoff
    swept = 0

    # Backoff schedule: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 16h, 24h × N
    # Keep simple — use retry_count to determine next-eligible time.
    def _eligible_for_retry(retry_count: int, last_retry_at_str: str) -> bool:
        try:
            last_retry = datetime.fromisoformat(last_retry_at_str)
        except (ValueError, TypeError):
            return True
        # Backoff: 60s base, 2× per attempt, capped at 24h
        backoff_s = min(60 * (2 ** min(retry_count, 10)), 86400)
        return datetime.utcnow() - last_retry > timedelta(seconds=backoff_s)

    async with db_session_factory() as db:
        from sqlalchemy import text
        # Use JSONB query — meetings.data->>'aggregation_failure_class' = 'transient_infra'
        stmt = text("""
            SELECT id FROM meetings
            WHERE data->>'aggregation_failure_class' = :cls
            ORDER BY (data->>'aggregation_last_retry_at')::timestamp NULLS FIRST
            LIMIT 50
        """)
        rows = (await db.execute(stmt, {"cls": "transient_infra"})).fetchall()

        if not rows:
            return 0

        from .post_meeting import (
            aggregate_transcription,
            set_aggregation_failure_class,
            AggregationFailureClass,
        )

        for row in rows:
            meeting_id = row[0]
            meeting = await db.get(Meeting, meeting_id)
            if not meeting:
                continue
            data = meeting.data or {}
            retry_count = data.get("aggregation_retry_count") or 0
            last_retry = data.get("aggregation_last_retry_at") or ""

            # Budget exhausted — flip to permanent + emit critical event
            if retry_count >= BUDGET_ATTEMPTS:
                logger.error(
                    f"[sweep] Pack H.4: meeting {meeting_id} exhausted aggregation "
                    f"retry budget after {retry_count} attempts — flipping to "
                    f"'permanent_infra' + critical alert"
                )
                set_aggregation_failure_class(
                    meeting, AggregationFailureClass.PERMANENT_INFRA
                )
                await db.commit()
                # TODO: emit meeting.aggregation_failed_permanent webhook event
                # (Pack H.3 wire-up — webhook_delivery infrastructure exists;
                # event dispatch lands in next commit)
                continue

            # Within budget — check eligibility
            if not _eligible_for_retry(retry_count, last_retry):
                continue

            try:
                ok = await aggregate_transcription(meeting, db)
                if ok:
                    logger.info(
                        f"[sweep] Pack H.4: meeting {meeting_id} aggregation "
                        f"retry {retry_count + 1} succeeded"
                    )
                    swept += 1
                else:
                    # Still transient — set_aggregation_failure_class inside
                    # aggregate_transcription already incremented retry_count.
                    logger.debug(
                        f"[sweep] Pack H.4: meeting {meeting_id} aggregation "
                        f"retry {retry_count + 1} still transient"
                    )
            except Exception as e:
                logger.error(
                    f"[sweep] Pack H.4 aggregation retry failed for {meeting_id}: "
                    f"{type(e).__name__}: {e!r}",
                    exc_info=True,
                )

    return swept


async def _sweep_container_stops() -> dict:
    """v0.10.5 Pack D.2 (#266) — durable container-stop outbox consumer.

    One iteration of the consumer for the
    `meeting-api:container-stops` Redis Stream. Producer side is
    `_delayed_container_stop` in meetings.py. The consumer reads all
    entries due-now (fire_at <= now), invokes `_stop_via_runtime_api`
    (idempotent — runtime-api 200 no-op for already-stopped), and
    handles retry / DLQ on failure.

    Returns the consumer's per-iteration summary dict (succeeded /
    retried / dlq / deferred), or {} on Redis unavailability.

    Why here, not in a dedicated worker: the per-iteration sweep cadence
    (60 s) is sufficient for the BOT_STOP_DELAY_SECONDS=90 window, and
    co-locating with the other sweeps keeps the operational surface
    small (one supervisor, one task). Same shape as Pack H.4's
    aggregation-retry sweep above.
    """
    from .meetings import get_redis, _stop_via_runtime_api
    from .container_stop_outbox import consume_pending_stops

    redis_client = get_redis()
    if redis_client is None:
        return {}

    return await consume_pending_stops(redis_client, _stop_via_runtime_api)


def _new_recording_numeric_id() -> int:
    return int(uuid.uuid4().int % 900000000000 + 100000000000)


def _parse_recording_chunk_key(user_id: int, session_uid: str, key: str) -> Optional[tuple[int, str, str]]:
    """Return (recording_id, media_type, media_format) for a canonical chunk key."""
    parts = key.split("/")
    if len(parts) < 6:
        return None
    if parts[0] != "recordings" or parts[1] != str(user_id) or parts[3] != session_uid:
        return None
    media_type = parts[4]
    filename = parts[-1]
    if media_type not in {"audio", "video"} or filename.startswith("master."):
        return None
    if "." not in filename:
        return None
    try:
        recording_id = int(parts[2])
    except (TypeError, ValueError):
        return None
    return recording_id, media_type, filename.rsplit(".", 1)[-1].lower()


def _recording_has_playback_url(rec: dict) -> bool:
    playback_url = rec.get("playback_url") if isinstance(rec, dict) else None
    if not isinstance(playback_url, dict):
        return False
    return bool(playback_url.get("audio") or playback_url.get("video"))


async def recover_recordings_jsonb_from_storage(
    meeting,
    db: AsyncSession,
) -> bool:
    """Inline counterpart to the sweep's "recover JSONB from chunks" path.

    Used when recording_finalizer is invoked but meeting.data.recordings is
    empty even though chunks have already been written to storage. This
    happens when the bot's exit callback fires before the chunk-write
    handler has populated meeting.data — a race that previously waited
    for the sweep (up to UNFINALIZED_RECORDINGS_MIN_AGE_SECONDS + sweep
    interval, ~90-180s) to recover.

    Returns True if at least one recording was seeded.
    """
    from .storage import create_storage_client

    data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
    if data.get("recording_enabled") is False:
        return False

    sessions = (await db.execute(
        select(MeetingSession).where(MeetingSession.meeting_id == meeting.id)
    )).scalars().all()
    if not sessions:
        return False

    storage = create_storage_client()
    now = datetime.utcnow().isoformat()
    recordings = list(data.get("recordings") or [])
    existing_sessions = {
        rec.get("session_uid")
        for rec in recordings
        if isinstance(rec, dict) and rec.get("session_uid")
    }
    changed = False

    for session in sessions:
        if session.session_uid in existing_sessions:
            continue
        prefix = f"recordings/{meeting.user_id}/"
        try:
            keys = storage.list_objects_bounded(prefix)
        except Exception as e:
            logger.warning(
                "[finalizer-recovery] storage list failed meeting_id=%s prefix=%s error=%s",
                meeting.id, prefix, str(e)[:200],
            )
            continue

        grouped: dict[tuple[int, str, str], list[str]] = {}
        for key in keys:
            parsed = _parse_recording_chunk_key(meeting.user_id, session.session_uid, key)
            if parsed is None:
                continue
            grouped.setdefault(parsed, []).append(key)
        if not grouped:
            continue

        media_files = []
        recording_id = None
        for (rec_id, media_type, media_format), chunk_keys in sorted(grouped.items()):
            chunk_keys = sorted(chunk_keys)
            recording_id = recording_id or rec_id
            media_files.append({
                "id": _new_recording_numeric_id(),
                "type": media_type,
                "format": media_format,
                "storage_path": chunk_keys[-1],
                "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
                "file_size_bytes": None,
                "last_chunk_size_bytes": None,
                "chunk_count": len(chunk_keys),
                "duration_seconds": None,
                "chunk_seq": len(chunk_keys) - 1,
                "first_chunk_at": getattr(session.session_start_time, "isoformat", lambda: now)(),
                "metadata": {},
                "created_at": now,
                "is_final": False,
                "finalized_at": None,
                "finalized_by": None,
            })
        if recording_id is None or not media_files:
            continue
        recordings.append({
            "id": recording_id,
            "meeting_id": meeting.id,
            "user_id": meeting.user_id,
            "session_uid": session.session_uid,
            "source": "bot",
            "status": "completed",
            "created_at": now,
            "completed_at": now,
            "media_files": media_files,
        })
        existing_sessions.add(session.session_uid)
        changed = True
        logger.info(
            "[finalizer-recovery] recovered JSONB metadata meeting_id=%s recording_id=%s session_uid=%s media_files=%s",
            meeting.id, recording_id, session.session_uid, len(media_files),
        )

    if changed:
        data["recordings"] = recordings
        meeting.data = data
        attributes.flag_modified(meeting, "data")
        await db.commit()

    return changed


async def _sweep_unfinalized_recordings(
    db_session_factory: Callable[[], AsyncSession],
) -> int:
    """Repair terminal meetings whose durable chunks never became playback_url.

    Canonical path: bot exit callback calls recording_finalizer before terminal
    status. This sweep is the local OSS safety net for escaped states:
      * meeting is terminal and recording was requested;
      * JSONB recording exists but lacks playback_url; or
      * chunks exist in storage for a MeetingSession but meeting.data.recordings
        was lost by a stale JSONB writer.

    The sweep is idempotent. It only seeds enough JSONB metadata for
    recording_finalizer to own the master path/playback_url write.
    """
    from datetime import datetime, timedelta
    from .recording_finalizer import finalize_recording_master
    from .storage import create_storage_client

    cutoff = datetime.utcnow() - timedelta(seconds=UNFINALIZED_RECORDINGS_MIN_AGE_SECONDS)
    swept = 0

    storage = create_storage_client()

    async with db_session_factory() as db:
        id_rows = (await db.execute(
            select(Meeting.id)
            .where(Meeting.status.in_([MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value]))
            .where(Meeting.created_at < cutoff)
            .order_by(Meeting.id.desc())
            .limit(UNFINALIZED_RECORDINGS_LIMIT)
        )).fetchall()

        for row in id_rows:
            meeting_id = row[0]
            meeting = (await db.execute(
                select(Meeting)
                .where(Meeting.id == meeting_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )).scalar_one_or_none()
            if meeting is None:
                continue

            data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
            if data.get("recording_enabled") is False:
                continue

            recordings = list(data.get("recordings") or [])
            has_unfinalized_jsonb = any(
                isinstance(rec, dict)
                and rec.get("status") != "failed"
                and rec.get("media_files")
                and not _recording_has_playback_url(rec)
                for rec in recordings
            )

            changed = False
            sessions = (await db.execute(
                select(MeetingSession).where(MeetingSession.meeting_id == meeting.id)
            )).scalars().all()

            existing_sessions = {
                rec.get("session_uid")
                for rec in recordings
                if isinstance(rec, dict) and rec.get("session_uid")
            }

            for session in sessions:
                if session.session_uid in existing_sessions:
                    continue

                prefix = f"recordings/{meeting.user_id}/"
                try:
                    keys = storage.list_objects_bounded(prefix)
                except Exception as e:
                    logger.warning(
                        "[sweep] unfinalized-recordings storage list failed "
                        "meeting_id=%s prefix=%s error=%s",
                        meeting.id, prefix, str(e)[:200],
                    )
                    continue

                grouped: dict[tuple[int, str, str], list[str]] = {}
                for key in keys:
                    parsed = _parse_recording_chunk_key(meeting.user_id, session.session_uid, key)
                    if parsed is None:
                        continue
                    grouped.setdefault(parsed, []).append(key)

                if not grouped:
                    continue

                now = datetime.utcnow().isoformat()
                media_files = []
                recording_id = None
                for (rec_id, media_type, media_format), chunk_keys in sorted(grouped.items()):
                    chunk_keys = sorted(chunk_keys)
                    recording_id = recording_id or rec_id
                    media_files.append({
                        "id": _new_recording_numeric_id(),
                        "type": media_type,
                        "format": media_format,
                        "storage_path": chunk_keys[-1],
                        "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
                        "file_size_bytes": None,
                        "last_chunk_size_bytes": None,
                        "chunk_count": len(chunk_keys),
                        "duration_seconds": None,
                        "chunk_seq": len(chunk_keys) - 1,
                        "first_chunk_at": getattr(session.session_start_time, "isoformat", lambda: now)(),
                        "metadata": {},
                        "created_at": now,
                        "is_final": False,
                        "finalized_at": None,
                        "finalized_by": None,
                    })

                if recording_id is None or not media_files:
                    continue

                recordings.append({
                    "id": recording_id,
                    "meeting_id": meeting.id,
                    "user_id": meeting.user_id,
                    "session_uid": session.session_uid,
                    "source": "bot",
                    "status": "completed",
                    "created_at": now,
                    "completed_at": now,
                    "media_files": media_files,
                })
                existing_sessions.add(session.session_uid)
                changed = True
                logger.warning(
                    "[sweep] unfinalized-recordings recovered JSONB metadata "
                    "meeting_id=%s recording_id=%s session_uid=%s media_files=%s",
                    meeting.id, recording_id, session.session_uid, len(media_files),
                )

            if changed:
                data["recordings"] = recordings
                meeting.data = data
                attributes.flag_modified(meeting, "data")
                await db.commit()

            if changed or has_unfinalized_jsonb:
                try:
                    await finalize_recording_master(meeting.id, db)
                    swept += 1
                    logger.warning(
                        "[sweep] unfinalized-recordings finalized meeting_id=%s "
                        "changed=%s had_unfinalized_jsonb=%s",
                        meeting.id, changed, has_unfinalized_jsonb,
                    )
                except Exception as e:
                    logger.error(
                        "[sweep] unfinalized-recordings finalize failed meeting_id=%s: %s",
                        meeting.id, str(e)[:200], exc_info=True,
                    )
                    await db.rollback()

    return swept


async def start_sweeps(
    db_session_factory: Callable[[], AsyncSession],
) -> None:
    """Run sweeps in a periodic loop. Call via asyncio.create_task().

    Currently runs:
      - Pack E.3.2: stale-stopping sweep
      - Pack H.4: aggregation_failure_class='transient_infra' retry
      - Pack D.2: container-stop outbox consumer (durable retry + DLQ)
      - Pack E.1-sibling: unfinalized recordings repair/finalize

    Pattern mirrors webhook_retry_worker.start_retry_worker — same
    shape, different responsibility.
    """
    global _stop_event, sweep_iterations, sweep_last_iteration_at
    _stop_event = asyncio.Event()

    logger.info("[sweeps] Starting meeting-api idle sweeps loop (Pack E.3.2 + H.4 + E.1-sibling + D.2)")

    while not _stop_event.is_set():
        sweep_iterations += 1
        sweep_last_iteration_at = time.time()

        try:
            swept = await _sweep_stale_stopping(db_session_factory)
            if swept > 0:
                logger.warning(
                    f"[sweeps] iteration {sweep_iterations}: "
                    f"swept {swept} stale-stopping rows "
                    f"(operators should investigate why exit-callback path failed)"
                )
        except Exception as e:
            logger.error(f"[sweeps] iteration {sweep_iterations} stale-stopping error: {e}", exc_info=True)

        try:
            retried = await _sweep_aggregation_retry(db_session_factory)
            if retried > 0:
                logger.info(
                    f"[sweeps] iteration {sweep_iterations}: "
                    f"successfully retried {retried} aggregation_failed rows (Pack H.4)"
                )
        except Exception as e:
            logger.error(f"[sweeps] iteration {sweep_iterations} aggregation-retry error: {e}", exc_info=True)

        try:
            finalized = await _sweep_unfinalized_recordings(db_session_factory)
            if finalized > 0:
                logger.warning(
                    f"[sweeps] iteration {sweep_iterations}: "
                    f"repaired/finalized {finalized} unfinalized recording meeting(s)"
                )
        except Exception as e:
            logger.error(f"[sweeps] iteration {sweep_iterations} unfinalized-recordings error: {e}", exc_info=True)

        try:
            stop_summary = await _sweep_container_stops()
            if stop_summary and (
                stop_summary.get("processed") or stop_summary.get("dlq")
            ):
                logger.info(
                    f"[sweeps] iteration {sweep_iterations} container-stops (Pack D.2): {stop_summary}"
                )
                if stop_summary.get("dlq", 0) > 0:
                    logger.warning(
                        f"[sweeps] iteration {sweep_iterations}: "
                        f"{stop_summary['dlq']} container-stop entries moved to DLQ "
                        f"(meeting-api:container-stop-dlq) — operator must investigate "
                        f"persistent runtime-api communication failures"
                    )
        except Exception as e:
            logger.error(
                f"[sweeps] iteration {sweep_iterations} container-stops error: {e}",
                exc_info=True,
            )

        # Wait for POLL_INTERVAL or until stopped.
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=STALE_STOPPING_POLL_INTERVAL)
            break  # stop_event was set
        except asyncio.TimeoutError:
            pass  # normal — poll again

    logger.info(f"[sweeps] Stopped after {sweep_iterations} iterations")


async def stop_sweeps() -> None:
    """Signal the sweep loop to stop."""
    global _stop_event
    if _stop_event is not None:
        _stop_event.set()
