"""Stop-reconcile sweep — the backstop that GUARANTEES teardown (ADR-0024 / CC6).

A user stop publishes a fire-and-forget ``leave`` over redis pub/sub. A BOOTING bot that hasn't
subscribed is handled directly by the stop route (``stop_router`` B1). But an ACTIVE bot that simply
MISSED the leave (a redis blip, a wedged consumer) would stay live forever — the DB says ``stopping``,
a real bot is in the meeting: an orphan. This sweep is the backstop:

  for each meeting stuck ``stopping`` past the grace window →
    1. complete it through the bot's OWN lifecycle callback (so the FSM, webhook, and ws frame all fire
       identically — no duplicate logic), then
    2. **kill the workload** (``runtime.delete_workload``) so the orphan bot is actually gone (CC6) —
       a stop must GUARANTEE the effect, not merely request it (ADR-0024).

Pure + injectable: ``post_lifecycle`` is the callback poster (prod = httpx to this process's own
``/bots/internal/callback/lifecycle``; tests = an in-memory recorder), ``runtime`` is the RuntimeClient
port (prod = HttpRuntimeClient; tests = FakeRuntimeClient). Best-effort per meeting — never raises.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional


async def reconcile_stale_stopping_sweep(
    repo: Any,
    runtime: Optional[Any],
    post_lifecycle: Callable[[dict], Awaitable[Any]],
    *,
    stop_grace: float,
    log: Any,
) -> int:
    """Run ONE sweep. Returns the number of stale ``stopping`` meetings reconciled."""
    stale = await repo.list_stale_stopping(older_than_seconds=stop_grace)
    for meeting_id, session_uid, bot_container_id in stale:
        # 1. Complete it through the bot's own lifecycle callback.
        try:
            status = await post_lifecycle(
                {"connection_id": session_uid, "status": "completed", "completion_reason": "stopped"}
            )
            log.info("stop-reconcile completed stuck meeting %s (session %s) → %s",
                     meeting_id, session_uid, status)
        except Exception:
            log.exception("stop-reconcile completion failed for meeting %s", meeting_id)
        # 2. GUARANTEE teardown — kill the workload so an active bot that missed the leave is not orphaned.
        if runtime is not None and bot_container_id:
            try:
                await runtime.delete_workload(bot_container_id)
                log.info("stop-reconcile killed orphan workload %s for meeting %s",
                         bot_container_id, meeting_id)
            except Exception as e:  # noqa: BLE001 — best-effort; logged, never fails the sweep
                _log_orphan_kill_failed(meeting_id, bot_container_id, e)
    return len(stale)


def _log_orphan_kill_failed(meeting_id, workload_id, err) -> None:
    try:
        from ..obs import log_event

        log_event("stop_reconcile_orphan_kill_failed", audience="system", level="warning",
                  span="reconcile.stop",
                  fields={"meeting_id": meeting_id, "workload_id": workload_id, "error": str(err)})
    except Exception:
        pass
