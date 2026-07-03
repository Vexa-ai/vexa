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


# Statuses where the bot NEVER reported `active` — a hung row here means the bot never started/joined
# and never will, so it reconciles to `failed` (attributed to the stage it died in). `active`/`stopping`
# (the bot WAS live) reconcile to `completed`.
_PRE_ACTIVE_NONTERMINAL = frozenset({"requested", "joining", "awaiting_admission", "needs_help"})

# Statuses where a BOT IS (or was) IN THE MEETING and may be legitimately quiet — silence (no segments)
# does NOT mean the bot is gone, so the active-reap on these is gated on POSITIVE evidence the bot's
# workload is no longer alive (runtime liveness), NOT on `updated_at`/segment staleness. `stopping` is
# EXCLUDED: a stop was requested, so it reaps on its short grace regardless (its bot SHOULD be leaving).
_LIVE_NONTERMINAL = frozenset({"active", "needs_help"})

# runtime.v1 workload states that mean the bot is STILL ALIVE in the meeting (the workload exists and is
# not torn down). Anything else — or a 404 (workload gone, ``get_workload`` → None) — is "bot gone".
_ALIVE_WORKLOAD_STATES = frozenset({"starting", "running", "stopping"})


async def _bot_workload_gone(
    runtime: Optional[Any], bot_container_id: Optional[str], *, log: Any
) -> Optional[bool]:
    """Liveness probe for the active-reap gate. Returns:

      * ``True``  — POSITIVE evidence the bot is gone (workload 404'd, or in a terminal state).
      * ``False`` — the workload is ALIVE (``starting``/``running``/``stopping``): a quiet-but-live bot.
      * ``None``  — UNKNOWN (no runtime, no container id, or the probe errored): the caller must NOT reap
                    on this — fail safe toward keeping a possibly-live meeting.
    """
    if runtime is None or not bot_container_id or not hasattr(runtime, "get_workload"):
        return None
    try:
        info = await runtime.get_workload(bot_container_id)
    except Exception:  # noqa: BLE001 — probe is best-effort; unknown ⇒ do NOT reap
        log.warning("nonterminal-reconcile: get_workload(%s) failed; not reaping", bot_container_id)
        return None
    if info is None:  # 404 — kernel no longer tracks it ⇒ bot gone
        return True
    return info.get("state") not in _ALIVE_WORKLOAD_STATES


async def reconcile_stale_nonterminal_sweep(
    repo: Any,
    runtime: Optional[Any],
    post_lifecycle: Callable[[dict], Awaitable[Any]],
    *,
    stop_grace: float,
    active_grace: float,
    log: Any,
) -> int:
    """The GENERAL backstop: any meeting hung in a non-terminal status whose bot is GONE (its row has
    been quiet — no status change, no segment/heartbeat — past the grace window) converges to a
    terminal state through the bot's OWN lifecycle callback (so the FSM → persist → webhook → ws
    publish path fires identically, never bypassed).

      * `active` / `stopping` (the bot WAS live) → `completed`. `stop_requested` is preserved (carried
        back into `meeting.data`) so the UI's derived `stopped` still shows.
      * `requested` / `joining` / `awaiting_admission` / `needs_help` (never reached `active`) → `failed`,
        attributed to the stage it died in.

    Two grace windows (env-configurable): ``stop_grace`` for `stopping` (a stop was requested — clear it
    fast), ``active_grace`` for everything else (a longer idle so a momentarily-quiet live bot is not
    reaped). Best-effort per meeting — never raises. Idempotent: an already-terminal row is not listed by
    ``list_stale_nonterminal``, and a redelivered terminal is an idempotent 200 no-op at the callback.

    Returns the number of meetings reconciled."""
    if repo is None or not hasattr(repo, "list_stale_nonterminal"):
        return 0
    try:
        stale = await repo.list_stale_nonterminal(stop_grace=stop_grace, active_grace=active_grace)
    except Exception:
        log.exception("nonterminal-reconcile: list_stale_nonterminal failed")
        return 0
    reconciled = 0
    for meeting_id, status, session_uid, bot_container_id, stop_requested in stale:
        # LIVENESS GATE (the correctness fix): for a status where a bot is in the meeting and may be
        # legitimately QUIET (`active`/`needs_help`), `updated_at` staleness is NOT evidence the bot is
        # gone — segments stop bumping it during silence. Only reap on POSITIVE evidence the bot's
        # workload is no longer alive. `stopping` is exempt (a stop was requested → reap on its grace).
        if status in _LIVE_NONTERMINAL and bot_container_id:
            gone = await _bot_workload_gone(runtime, bot_container_id, log=log)
            if gone is not True:
                # ALIVE (False) or UNKNOWN (None) → do not reap a possibly-live, bot-present meeting.
                # (No bot_container_id at all falls through to the time-based reap — there is no live
                #  workload that could be holding the meeting open, so a quiet row IS a gone bot.)
                log.info("nonterminal-reconcile: skip live/unknown bot for meeting %s "
                         "(status %s, workload %s, gone=%s)",
                         meeting_id, status, bot_container_id, gone)
                continue
        terminal = "failed" if status in _PRE_ACTIVE_NONTERMINAL else "completed"
        body: dict[str, Any] = {"connection_id": session_uid, "status": terminal}
        if terminal == "completed":
            body["completion_reason"] = "stopped" if stop_requested else "left_alone"
            if stop_requested:
                body["data"] = {"stop_requested": True}
        else:
            body["completion_reason"] = "left_alone"
            body["reason"] = f"bot gone while {status}; reconciled to failed (never reached active)"
        try:
            result = await post_lifecycle(body)
            reconciled += 1
            log.info("nonterminal-reconcile %s meeting %s (status %s, session %s) → %s",
                     terminal, meeting_id, status, session_uid, result)
        except Exception:
            log.exception("nonterminal-reconcile failed for meeting %s (status %s)", meeting_id, status)
            continue
        if runtime is not None and bot_container_id:
            try:
                await runtime.delete_workload(bot_container_id)
            except Exception as e:  # noqa: BLE001 — best-effort; logged, never fails the sweep
                _log_orphan_kill_failed(meeting_id, bot_container_id, e)
    return reconciled


def _log_orphan_kill_failed(meeting_id, workload_id, err) -> None:
    try:
        from ..obs import log_event

        log_event("stop_reconcile_orphan_kill_failed", audience="system", level="warning",
                  span="reconcile.stop",
                  fields={"meeting_id": meeting_id, "workload_id": workload_id, "error": str(err)})
    except Exception:
        pass


# Workload states the runtime kernel reports as TERMINAL (the workload is gone).
TERMINAL_WORKLOAD_STATES = frozenset({"destroyed", "failed", "exited", "crashed", "stopped", "error"})
# Meeting statuses where the bot has NOT yet reported `active` — a terminal workload here means the bot
# never started and never will (image-pull fail, OOM, crash on boot), so it can be classed `failed`
# unambiguously. `active`/`stopping` are owned by the bot's own callback + the stop-reconcile (a normal
# teardown destroys the workload AFTER the meeting is already terminal) — covered separately to avoid
# racing a legitimate completion.
_PRE_ACTIVE_STATUSES = frozenset({"requested", "joining", "awaiting_admission"})


async def synthesize_failed_for_dead_workload(
    repo: Any,
    workload_id: Optional[str],
    state: Optional[str],
    drive_failed: Callable[[dict], Awaitable[Any]],
    *,
    log: Any,
) -> bool:
    """CC5 — a workload that reached a TERMINAL state while its meeting is still PRE-ACTIVE means the bot
    never started/reported and never will. Drive a synthetic ``failed`` (through ``drive_failed`` — the
    bot's own lifecycle callback in prod) so the meeting does not hang ``requested``/``joining`` forever.

    Returns True iff a synthetic ``failed`` was driven. No-op (False) when the state isn't terminal, the
    workload is unknown, or the meeting already advanced to ``active``/a terminal status (the bot's
    callback is the source of truth for a STARTED bot). Best-effort: never raises."""
    if not workload_id or state not in TERMINAL_WORKLOAD_STATES or repo is None:
        return False
    try:
        info = await repo.find_by_container(bot_container_id=workload_id)
    except Exception as e:  # noqa: BLE001 — lookup is best-effort
        log.warning("runtime-callback: find_by_container failed for %s: %s", workload_id, e)
        return False
    if not info or info.get("status") not in _PRE_ACTIVE_STATUSES or not info.get("session_uid"):
        return False
    synthetic = {
        "connection_id": info["session_uid"],
        "status": "failed",
        "failure_stage": info["status"],            # the stage the bot died IN (requested/joining/…)
        "completion_reason": "join_failure",        # workload died before the bot could join/report
        "reason": f"workload {state} before the bot reported (never started)",
    }
    try:
        await drive_failed(synthetic)
        log.info("runtime-callback: drove synthetic failed for meeting %s (workload %s %s)",
                 info.get("meeting_id"), workload_id, state)
        return True
    except Exception as e:  # noqa: BLE001 — best-effort; the stop/stale sweeps remain the backstop
        log.warning("runtime-callback: synthetic failed POST failed for %s: %s", workload_id, e)
        return False
