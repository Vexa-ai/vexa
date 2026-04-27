"""v0.10.5 Pack E.3.2 + future H.4 + E.1-sibling sweeps.

Long-running idle-loop equivalent for meeting-api. Each sweep is a
periodic scan that catches state-machine rows that genuinely got
stuck — escapes from the canonical durable mechanisms (Pack J's
exit-callback in callbacks.py, Pack E.1's chunk-finalize outbox, etc).

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
import time
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Meeting
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

# v0.10.5 Pack K.5 (meeting-api side analog).
# Module-level state for /health probe / Pack M metrics.
sweep_iterations: int = 0
sweep_last_iteration_at: float = 0.0

_stop_event: Optional[asyncio.Event] = None


async def _sweep_stale_stopping(
    db_session_factory: Callable[[], AsyncSession],
) -> int:
    """One iteration of the stale-stopping sweep.

    Scans for rows where status='stopping' AND updated_at older than
    STALE_STOPPING_THRESHOLD_SECONDS. Force-completes each with
    `completion_reason=STOPPED` + transition_reason='stale_stopping_sweep'
    so the source is visible in audit logs.

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
        stmt = (
            select(Meeting)
            .where(Meeting.status == MeetingStatus.STOPPING.value)
            .where(Meeting.updated_at < threshold)
            .limit(50)  # bound per-iteration to avoid sweep starving other work
        )
        rows = (await db.execute(stmt)).scalars().all()

        for meeting in rows:
            stuck_for = (datetime.utcnow() - meeting.updated_at).total_seconds()
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


async def start_sweeps(
    db_session_factory: Callable[[], AsyncSession],
) -> None:
    """Run sweeps in a periodic loop. Call via asyncio.create_task().

    Currently runs:
      - Pack E.3.2: stale-stopping sweep

    Future Packs E.1-sibling (sweep_unfinalized_recordings) and H.4
    (sweep aggregation_failed for retry) wire in here.

    Pattern mirrors webhook_retry_worker.start_retry_worker — same
    shape, different responsibility.
    """
    global _stop_event, sweep_iterations, sweep_last_iteration_at
    _stop_event = asyncio.Event()

    logger.info("[sweeps] Starting meeting-api idle sweeps loop (Pack E.3.2 + future)")

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
            logger.error(f"[sweeps] iteration {sweep_iterations} error: {e}", exc_info=True)

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
