"""Stop-reconcile sweep — the backstop that GUARANTEES teardown (ADR-0024 / CC6).

A user stop publishes a fire-and-forget ``leave`` over redis pub/sub. A BOOTING bot that hasn't
subscribed is handled directly by the stop route (``stop_router`` B1). But an ACTIVE bot that simply
MISSED the leave (a redis blip, a wedged consumer) would stay live forever — the DB says ``stopping``,
a real bot is in the meeting: an orphan. This sweep is the backstop:

  for each meeting stuck ``stopping`` past the grace window →
    1. **kill the workload** (``runtime.delete_workload``) so the orphan bot is actually gone (CC6) —
       a stop must GUARANTEE the effect, not merely request it (ADR-0024) — and require the kernel to
       CONFIRM it. Then, and only then,
    2. complete it through the bot's OWN lifecycle callback (so the FSM, webhook, and ws frame all fire
       identically — no duplicate logic).

Teardown-BEFORE-complete, and evidence-gated (the orphaned-live-bot fix): a runtime 404
(``WorkloadUnknown``) means the kernel does not know the workload — NOT that the container is gone
(a recreated runtime forgets live bots). On an unconfirmed teardown the meeting is NOT completed:
it stays ``stopping`` (truthful — the stop is not done), the failure is logged LOUD, and the next
sweep retries (the re-adopting runtime answers truthfully once it has booted).

Pure + injectable: ``post_lifecycle`` is the callback poster (prod = httpx to this process's own
``/bots/internal/callback/lifecycle``; tests = an in-memory recorder), ``runtime`` is the RuntimeClient
port (prod = HttpRuntimeClient; tests = FakeRuntimeClient). Best-effort per meeting — never raises.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional

from ..bot_spawn.ports import WorkloadUnknown


async def _teardown_verdict(
    runtime: Optional[Any], bot_container_id: Optional[str], *, meeting_id: Any, log: Any
) -> str:
    """Kill the workload and report the teardown verdict.

      * ``"confirmed"`` — the kernel confirmed the teardown (or there is no runtime/workload id at
                          all: nothing that could be orphaned, so the FSM may proceed on its
                          declared timeout).
      * ``"untracked"`` — the kernel 404'd (``WorkloadUnknown``): a container may still be live.
                          Logged LOUD; the caller must NOT advance the meeting to a terminal state
                          on this alone — the next sweep retries (bounded by the untracked
                          escalation window, see ``reconcile_stale_nonterminal_sweep``).
      * ``"failed"``    — the delete errored (transient runtime trouble): retry on the next sweep.
    """
    if runtime is None or not bot_container_id:
        return "confirmed"
    try:
        await runtime.delete_workload(bot_container_id)
        return "confirmed"
    except WorkloadUnknown as e:
        log.error(
            "reconcile: runtime does not know workload %s for meeting %s — termination "
            "UNCONFIRMED, a live container may be orphaned; NOT advancing the meeting (%s)",
            bot_container_id, meeting_id, e,
        )
        _log_orphan_kill_failed(meeting_id, bot_container_id, e, unconfirmed=True)
        return "untracked"
    except Exception as e:  # noqa: BLE001 — transient runtime error: retry on the next sweep
        _log_orphan_kill_failed(meeting_id, bot_container_id, e)
        return "failed"


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
    reconciled = 0
    for meeting_id, session_uid, bot_container_id in stale:
        # 1. GUARANTEE teardown FIRST — and require confirmation. Completing before a confirmed
        #    kill is how the incident produced a `completed` meeting with a live ghost bot.
        #    (Untracked here is NOT escalated: the general sweep owns the bounded escalation.)
        if await _teardown_verdict(
            runtime, bot_container_id, meeting_id=meeting_id, log=log
        ) != "confirmed":
            continue  # stays `stopping` (truthful); retried next sweep, loud in the logs
        # 2. Complete it through the bot's own lifecycle callback.
        try:
            status = await post_lifecycle(
                {"connection_id": session_uid, "status": "completed", "completion_reason": "stopped"}
            )
            reconciled += 1
            log.info("stop-reconcile completed stuck meeting %s (session %s) → %s",
                     meeting_id, session_uid, status)
        except Exception:
            log.exception("stop-reconcile completion failed for meeting %s", meeting_id)
    return reconciled


# Statuses where the bot NEVER reported `active` — a hung row here means the bot never started/joined
# and never will, so it reconciles to `failed` (attributed to the stage it died in). `active`/`stopping`
# (the bot WAS live) reconcile to `completed`.
_PRE_ACTIVE_NONTERMINAL = frozenset({"requested", "joining", "awaiting_admission", "needs_help"})

# Statuses where a BOT MAY BE ALIVE and legitimately QUIET — either in the meeting (`active`/
# `needs_help`) or on its way in (`requested`/`joining`/`awaiting_admission`). Silence does NOT mean
# the bot is gone: an ACTIVE bot stops bumping `updated_at` through a silent room, and a bot parked in
# a waiting room reports `awaiting_admission` ONCE and then polls for up to the lobby budget the
# control plane itself handed it (``bot_spawn.service.LOBBY_BUDGET_MS``) — there is no heartbeat on
# either path. So the reap on ALL of these is gated on POSITIVE evidence the bot's workload is no
# longer alive (runtime liveness), NOT on `updated_at`/segment staleness. `stopping` is EXCLUDED: a
# stop was requested, so it reaps on its short grace regardless (its bot SHOULD be leaving).
_LIVENESS_GATED = frozenset(
    {"requested", "joining", "awaiting_admission", "needs_help", "active"}
)

# runtime.v1 workload states that mean the bot is STILL ALIVE in the meeting (the workload exists and is
# not torn down). A TRACKED workload in any other state is positive evidence the bot exited. A 404
# (``get_workload`` → None) is NEITHER: the kernel merely does not know the workload.
_ALIVE_WORKLOAD_STATES = frozenset({"starting", "running", "stopping"})


async def _probe_bot_workload(
    runtime: Optional[Any], bot_container_id: Optional[str], *, log: Any
) -> tuple[str, Optional[dict]]:
    """Liveness probe for the reap gate. Returns ``(verdict, workload_info)`` — the info is the
    kernel's own answer, carried back so a reap can ATTRIBUTE itself to real evidence instead of
    manufacturing a reason. Verdicts:

      * ``"gone"``      — POSITIVE evidence the bot exited: the kernel TRACKS the workload and
                          reports a terminal state (stopped/destroyed…, with an exit code).
      * ``"alive"``     — the workload is ALIVE (``starting``/``running``/``stopping``).
      * ``"untracked"`` — the kernel 404'd. NOT evidence of anything (the orphaned-live-bot
                          incident: a recreated runtime 404'd over a live, capturing bot). The
                          caller must NOT reap on this — fail LOUD instead.
      * ``"unknown"``   — no runtime / no container id / the probe errored: do NOT reap — fail
                          safe toward keeping a possibly-live meeting.
    """
    if runtime is None or not bot_container_id or not hasattr(runtime, "get_workload"):
        return "unknown", None
    try:
        info = await runtime.get_workload(bot_container_id)
    except Exception:  # noqa: BLE001 — probe is best-effort; unknown ⇒ do NOT reap
        log.warning("nonterminal-reconcile: get_workload(%s) failed; not reaping", bot_container_id)
        return "unknown", None
    if info is None:  # 404 — the kernel does not KNOW; never mistake amnesia for evidence
        return "untracked", None
    if info.get("state") in _ALIVE_WORKLOAD_STATES:
        return "alive", info
    return "gone", info


def _workload_evidence(bot_container_id: Optional[str], info: Optional[dict]) -> str:
    """The probe's answer, rendered for the terminal transition's ``reason``. This is what replaces
    the manufactured "bot gone while {status}": a reader learns WHAT the kernel reported (state,
    exit code, stop reason) — or, honestly, that there was nothing to ask about."""
    if not bot_container_id:
        return "no workload recorded for this meeting"
    if not info:
        return f"workload {bot_container_id}: no runtime evidence (probe inconclusive)"
    parts = [f"workload {bot_container_id} state={info.get('state') or 'unknown'}"]
    code = info.get("exitCode", info.get("exit_code"))
    if code is not None:
        parts.append(f"exitCode={code}")
    stop_reason = info.get("stopReason") or info.get("stop_reason")
    if stop_reason:
        parts.append(f"stopReason={stop_reason}")
    return " ".join(parts)


def default_preactive_grace() -> float:
    """The pre-active reap floor, DERIVED from the lobby budget the control plane itself issues.

    A not-yet-admitted bot holds a deadline WE wrote (``bot_spawn.service.LOBBY_BUDGET_MS``, the
    spawn's ``waitingRoomTimeout``); the window we then measure it against must outlast that deadline,
    or the control plane kills bots that are still inside the budget it granted them (#862). Deriving
    the floor — the budget plus a minute of headroom for the bot's own terminal callback to land —
    keeps the two in lockstep: shorten the budget and the floor follows."""
    from ..bot_spawn.service import LOBBY_BUDGET_MS

    return LOBBY_BUDGET_MS / 1000.0 + 60.0


# ── bounded untracked escalation (the zombie-loop fix) ───────────────────────────────────────────
# "Untracked, never reap" is right as a REFLEX (a recreated runtime forgets live bots — amnesia is
# not evidence) but wrong as a STEADY STATE: on the process backend the workers die WITH the runtime
# (adopt() is a no-op, no callback will ever come), and on k8s pod GC/eviction removes objects while
# the runtime is down — in both, every meeting live across a runtime restart would loop
# `untracked` + a failed DELETE at error level every sweep, FOREVER. So the sweep counts CONTINUOUS
# untracked observations per meeting; once one spans the escalation window with no recovery (no
# runtime re-adoption, no bot callback), the meeting advances to `failed` carrying the evidence note
# (what was unaccountable, and for how long) — and the retry loop converges. The tracker is
# in-process by design: a meeting-api restart merely restarts a window, which only ever delays an
# escalation (conservative), never fabricates one.
_UNTRACKED_SINCE: dict[Any, float] = {}


def _untracked_window_elapsed(
    tracker: dict, meeting_id: Any, seen: set, *, grace: float, now: float
) -> bool:
    """Record one untracked observation; True once the CONTINUOUS window exceeds ``grace``.
    (Strictly greater: the first observation only starts the window — a single blip never
    escalates, whatever the configured grace.)"""
    seen.add(meeting_id)
    first = tracker.setdefault(meeting_id, now)
    return (now - first) > grace


async def _escalate_untracked_zombie(
    meeting_id: Any,
    status: str,
    session_uid: str,
    bot_container_id: Optional[str],
    post_lifecycle: Callable[[dict], Awaitable[Any]],
    tracker: dict,
    *,
    grace: float,
    log: Any,
    stop_requested: bool = False,
) -> bool:
    """Advance a continuously-untracked meeting to ``failed`` — through the bot's OWN lifecycle
    callback, with the evidence note as the transition's reason. WARN, not error: this is the
    system converging on its declared policy, not a fresh surprise.

    A PRE-ACTIVE row is attributed to the stage it was lost in (``_pre_active_completion_reason``),
    so a bot that never reached the meeting stays RE-SPAWNABLE (#862 — ``left_alone`` is
    ``_PERMANENT`` in ``retry.py``, and using it here cancelled the legitimate retry)."""
    reason = (
        f"workload {bot_container_id} untracked for >{int(grace)}s; presumed lost — "
        "runtime restart or external removal"
    )
    pre_active = status in _PRE_ACTIVE_STATUSES
    stage = status if pre_active else "active"
    body = {
        "connection_id": session_uid,
        "status": "failed",
        "failure_stage": stage,
        "completion_reason": (
            _pre_active_completion_reason(status, stop_requested) if pre_active else "left_alone"
        ),
        "reason": reason,
    }
    log.warning(
        "nonterminal-reconcile: meeting %s (%s) escalated to failed — %s",
        meeting_id, status, reason,
    )
    _log_untracked_escalated(meeting_id, status, bot_container_id, reason)
    try:
        await post_lifecycle(body)
        tracker.pop(meeting_id, None)
        return True
    except Exception:  # noqa: BLE001 — best-effort; the window stays open, retried next sweep
        log.exception("untracked-escalation failed for meeting %s", meeting_id)
        return False


async def reconcile_stale_nonterminal_sweep(
    repo: Any,
    runtime: Optional[Any],
    post_lifecycle: Callable[[dict], Awaitable[Any]],
    *,
    stop_grace: float,
    active_grace: float,
    log: Any,
    preactive_grace: Optional[float] = None,
    untracked_grace: float = 600.0,
    untracked_since: Optional[dict] = None,
) -> int:
    """The GENERAL backstop: any meeting hung in a non-terminal status whose bot is GONE (its row has
    been quiet — no status change, no segment/heartbeat — past the grace window) converges to a
    terminal state through the bot's OWN lifecycle callback (so the FSM → persist → webhook → ws
    publish path fires identically, never bypassed).

      * `active` / `stopping` (the bot WAS live) → `completed`. `stop_requested` is preserved (carried
        back into `meeting.data`) so the UI's derived `stopped` still shows.
      * `requested` / `joining` / `awaiting_admission` / `needs_help` (never reached `active`) → `failed`,
        attributed to the stage it died in.

    THREE grace windows (env-configurable): ``stop_grace`` for `stopping` (a stop was requested — clear
    it fast), ``preactive_grace`` for a bot that has not reached the meeting yet (it holds a lobby
    budget from the control plane — our patience must OUTLAST the deadline we issued, #862), and
    ``active_grace`` for everything else (a longer idle so a momentarily-quiet live bot is not reaped).
    Best-effort per meeting — never raises. Idempotent: an already-terminal row is not listed by
    ``list_stale_nonterminal``, and a redelivered terminal is an idempotent 200 no-op at the callback.

    Returns the number of meetings reconciled."""
    if repo is None or not hasattr(repo, "list_stale_nonterminal"):
        return 0
    try:
        stale = await repo.list_stale_nonterminal(
            stop_grace=stop_grace, active_grace=active_grace,
            preactive_grace=active_grace if preactive_grace is None else preactive_grace,
        )
    except Exception:
        log.exception("nonterminal-reconcile: list_stale_nonterminal failed")
        return 0
    reconciled = 0
    tracker = _UNTRACKED_SINCE if untracked_since is None else untracked_since
    seen_untracked: set = set()
    now = time.monotonic()
    for meeting_id, status, session_uid, bot_container_id, stop_requested in stale:
        probe, probe_info = "unknown", None
        # LIVENESS GATE (the correctness fix): for a status where a bot may be alive and legitimately
        # QUIET — in the meeting (`active`/`needs_help`) or on its way in (`requested`/`joining`/
        # `awaiting_admission`) — `updated_at` staleness is NOT evidence the bot is gone. Segments
        # stop bumping it through a silent room, and a lobby bot emits `awaiting_admission` ONCE and
        # then polls silently for the whole budget we handed it (#862: the sweep force-deleted
        # HEALTHY bots that were still waiting to be let in, at 300s of a 600s wait). Only POSITIVE
        # evidence ("gone": the kernel TRACKS the workload and reports it terminal) reaps. A 404
        # ("untracked") is NOT evidence — a recreated runtime forgets live bots (the orphaned-live-bot
        # incident advanced a live, capturing meeting to `completed` on exactly that 404). `stopping`
        # is exempt (a stop was requested → it converges on its grace, gated on a CONFIRMED teardown
        # below).
        if status in _LIVENESS_GATED and bot_container_id:
            probe, probe_info = await _probe_bot_workload(runtime, bot_container_id, log=log)
            if probe == "untracked":
                _log_workload_untracked(meeting_id, status, bot_container_id)
                log.error(
                    "nonterminal-reconcile: runtime does not know workload %s for %s meeting %s — "
                    "NOT evidence the bot is gone; not reaping (waiting for runtime re-adoption / "
                    "the bot's own callback)",
                    bot_container_id, status, meeting_id,
                )
                # BOUNDED (the zombie-loop fix): once the meeting has been CONTINUOUSLY untracked
                # past the escalation window — no re-adoption, no bot callback — it converges to
                # `failed` with the evidence note instead of looping this error forever.
                if _untracked_window_elapsed(
                    tracker, meeting_id, seen_untracked, grace=untracked_grace, now=now
                ) and await _escalate_untracked_zombie(
                    meeting_id, status, session_uid, bot_container_id, post_lifecycle,
                    tracker, grace=untracked_grace, log=log, stop_requested=stop_requested,
                ):
                    reconciled += 1
                continue
            if probe != "gone":
                # ALIVE or UNKNOWN → do not reap a possibly-live, bot-present meeting.
                # (No bot_container_id at all falls through to the time-based reap — there is no
                #  live workload that could be holding the meeting open.)
                log.info("nonterminal-reconcile: skip live/unknown bot for meeting %s "
                         "(status %s, workload %s, probe=%s)",
                         meeting_id, status, bot_container_id, probe)
                continue
        # GUARANTEE teardown BEFORE the FSM advances (CC6 + the incident fix): a terminal meeting
        # must never leave a live container behind. Unconfirmed (runtime 404 / delete failure) →
        # the meeting keeps its current status, loud in the logs, retried next sweep — except a
        # CONTINUOUSLY untracked workload (`stopping`/pre-active rows land here), which escalates
        # on the same bounded window instead of retrying the dead DELETE every sweep forever.
        verdict = await _teardown_verdict(
            runtime, bot_container_id, meeting_id=meeting_id, log=log
        )
        if verdict != "confirmed":
            if verdict == "untracked" and _untracked_window_elapsed(
                tracker, meeting_id, seen_untracked, grace=untracked_grace, now=now
            ) and await _escalate_untracked_zombie(
                meeting_id, status, session_uid, bot_container_id, post_lifecycle,
                tracker, grace=untracked_grace, log=log, stop_requested=stop_requested,
            ):
                reconciled += 1
            continue
        terminal = "failed" if status in _PRE_ACTIVE_NONTERMINAL else "completed"
        body: dict[str, Any] = {"connection_id": session_uid, "status": terminal}
        if terminal == "completed":
            body["completion_reason"] = "stopped" if stop_requested else "left_alone"
            if stop_requested:
                body["data"] = {"stop_requested": True}
        else:
            # ATTRIBUTE, never manufacture (#862). The reason is DERIVED from the stage the bot
            # died in, and the note carries the probe's own answer (workload state, exit code) —
            # the only things this sweep actually knows. A default of `left_alone` would be a claim
            # with no evidence behind it (the sweep issued the delete itself), and `left_alone` is
            # `_PERMANENT` in ``retry.py``, so it would also cancel the legitimate re-spawn.
            body["completion_reason"] = _pre_active_completion_reason(status, stop_requested)
            body["reason"] = (
                f"{_workload_evidence(bot_container_id, probe_info)}; "
                f"reconciled to failed at {status} (never reached active)"
            )
            if stop_requested:
                body["data"] = {"stop_requested": True}
        try:
            result = await post_lifecycle(body)
            reconciled += 1
            log.info("nonterminal-reconcile %s meeting %s (status %s, session %s) → %s",
                     terminal, meeting_id, status, session_uid, result)
        except Exception:
            log.exception("nonterminal-reconcile failed for meeting %s (status %s)", meeting_id, status)
    # RECOVERY resets the window: any meeting NOT observed untracked in THIS sweep — the runtime
    # re-adopted it (probe alive/gone), a bot callback bumped/terminated the row (no longer listed
    # stale), or it was reconciled — drops its tracker entry. Only CONTINUOUS untracked escalates.
    for mid in [m for m in tracker if m not in seen_untracked]:
        tracker.pop(mid, None)
    return reconciled


def _log_orphan_kill_failed(meeting_id, workload_id, err, *, unconfirmed: bool = False) -> None:
    try:
        from ..obs import log_event

        log_event(
            "stop_reconcile_orphan_kill_unconfirmed" if unconfirmed
            else "stop_reconcile_orphan_kill_failed",
            audience="system",
            level="error" if unconfirmed else "warning",
            span="reconcile.stop",
            fields={"meeting_id": meeting_id, "workload_id": workload_id, "error": str(err)},
        )
    except Exception:
        pass


def _log_workload_untracked(meeting_id, status, workload_id) -> None:
    """LOUD system event: the runtime does not know a workload the DB says is this meeting's live
    bot. Either the runtime lost its registry (pre-re-adoption restart) or the container truly
    vanished outside the kernel — both need eyes; neither is licence to complete the meeting."""
    try:
        from ..obs import log_event

        log_event("meeting_workload_untracked", audience="system", level="error",
                  span="reconcile.liveness",
                  fields={"meeting_id": meeting_id, "status": status, "workload_id": workload_id})
    except Exception:
        pass


def _log_untracked_escalated(meeting_id, status, workload_id, reason) -> None:
    """The bounded escalation fired: a meeting whose workload stayed untracked past the window was
    advanced to `failed` with the evidence note. WARN (declared policy converging), not error —
    and it fires exactly once per meeting: the terminal row leaves the sweep's listing."""
    try:
        from ..obs import log_event

        log_event("meeting_workload_untracked_escalated", audience="system", level="warning",
                  span="reconcile.liveness",
                  fields={"meeting_id": meeting_id, "status": status,
                          "workload_id": workload_id, "reason": reason})
    except Exception:
        pass


# Workload states the runtime kernel reports as TERMINAL (the workload is gone).
TERMINAL_WORKLOAD_STATES = frozenset({"destroyed", "failed", "exited", "crashed", "stopped", "error"})
# Meeting statuses where the bot has NOT yet reported `active` — a terminal workload here means the bot
# never started and never will (image-pull fail, OOM, crash on boot), so it can be classed `failed`
# unambiguously.
_PRE_ACTIVE_STATUSES = frozenset({"requested", "joining", "awaiting_admission"})
# Meeting statuses where a bot WAS (or is being) live in the meeting — a `stopping` row is a user-stop
# in flight, and `active`/`needs_help` mean the bot reported live. `stopping` belongs here because the
# stop path writes it ONLY over a status the bot reached the meeting in; a stop against a pre-active
# bot leaves that stage in place instead (``stop_router``), so this set never has to guess.
# A runtime-confirmed TERMINAL workload for one of these is real terminal evidence the run is over → the
# meeting completes (it reached active, so `completed`, not `failed`). See
# ``synthesize_terminal_for_dead_workload``.
_WAS_ACTIVE_STATUSES = frozenset({"stopping", "active", "needs_help"})


def _pre_active_completion_reason(status: Optional[str], stop_requested: bool = False) -> str:
    """Attribute a pre-active teardown to the stage the bot died in: a bot whose workload is torn
    down while it sits in the waiting room (``awaiting_admission``) was never admitted →
    ``awaiting_admission_timeout``; any earlier pre-active stage (``requested``/``joining``) died
    before it could join → ``join_failure``. Keyed on ``awaiting_admission`` EXPLICITLY: an
    escalation state like ``needs_help`` is not an admission wait and must never earn the
    admission-timeout reason. Both values are TRANSIENT (see ``retry.py``).

    ``stop_requested`` overrides both: the workload died because the USER stopped it, so the run
    ended for a reason no re-spawn can improve on. ``stopped`` is the sealed user-terminal reason
    and is PERMANENT, which is what keeps a deliberate cancellation from being re-spawned three
    times (#807 — the stage still lands in ``failure_stage``, so no attribution is lost)."""
    if stop_requested:
        return "stopped"
    return "awaiting_admission_timeout" if status == "awaiting_admission" else "join_failure"


async def synthesize_terminal_for_dead_workload(
    repo: Any,
    workload_id: Optional[str],
    state: Optional[str],
    drive_terminal: Callable[[dict], Awaitable[Any]],
    *,
    log: Any,
) -> bool:
    """Consume a runtime-confirmed TERMINAL workload callback (``destroyed``/``exited``/…) as EVIDENCE the
    run is over, and advance its still-non-terminal meeting through the bot's OWN lifecycle callback
    (``drive_terminal`` — POST-to-self in prod) so the FSM → persist → webhook → ws path fires identically.

    This is the evidence-based principle of #50, applied to the OTHER terminal source. #50 requires
    real evidence before completing a meeting; a runtime ``destroyed`` for a workload the kernel just tore
    down IS that evidence (not a bare 404 / amnesia — the runtime is affirmatively reporting the workload
    gone). Two cases by the meeting's current stage:

      * PRE-ACTIVE (``requested``/``joining``/``awaiting_admission``) — the bot never reported ``active`` and
        never will (image-pull fail, OOM, crash on boot, or a stop that killed it in the waiting room
        before it sent its own terminal callback) → synthetic ``failed`` attributed to the stage it died in
        (CC5).
      * WAS-ACTIVE (``stopping``/``active``/``needs_help``) — the bot reached the meeting, but its workload
        is now confirmed gone WITHOUT its own terminal callback having landed (e.g. it was SIGKILLed at
        teardown before it could POST ``completed``). This is exactly the reaper-loop incident: DELETE
        ``/workloads/{id}`` → 200, runtime posts ``state=destroyed``, but the meeting stayed ``stopping``
        and the stop-reconcile sweep re-DELETEs (now 404) every 15s forever. The confirmed destroy is
        terminal evidence → complete the meeting (it WAS active, so ``completed``), stopping the loop.

    Returns True iff a synthetic terminal was driven. No-op (False) when the state isn't terminal, the
    workload is unknown, or the meeting already reached a terminal status (the bot's own callback already
    landed — a redelivered terminal is an idempotent 200 no-op at the callback anyway). Best-effort:
    never raises."""
    if not workload_id or state not in TERMINAL_WORKLOAD_STATES or repo is None:
        return False
    try:
        info = await repo.find_by_container(bot_container_id=workload_id)
    except Exception as e:  # noqa: BLE001 — lookup is best-effort
        log.warning("runtime-callback: find_by_container failed for %s: %s", workload_id, e)
        return False
    if not info or not info.get("session_uid"):
        return False
    status = info.get("status")
    stop_requested = bool(info.get("stop_requested"))
    if status in _PRE_ACTIVE_STATUSES:
        body = {
            "connection_id": info["session_uid"],
            "status": "failed",
            "failure_stage": status,                    # the stage the bot died IN (requested/joining/…)
            "completion_reason": _pre_active_completion_reason(status, stop_requested),
            "reason": (
                f"stopped by the user at {status} (never admitted)"
                if stop_requested
                else f"workload {state} while awaiting admission (never admitted)"
                if status == "awaiting_admission"
                else f"workload {state} before the bot reported (never started)"
            ),
        }
        if stop_requested:
            body["data"] = {"stop_requested": True}
    elif status in _WAS_ACTIVE_STATUSES:
        # It reached the meeting and its workload is now runtime-confirmed gone with no terminal callback
        # of its own — complete it (it WAS active). `completion_reason=stopped` when the stop was in
        # flight (`stopping`), else `left_alone` (the bot's workload simply vanished while live).
        body = {
            "connection_id": info["session_uid"],
            "status": "completed",
            "completion_reason": "stopped" if status == "stopping" else "left_alone",
            "reason": f"stopped (workload {state}, confirmed by runtime)",
        }
        if status == "stopping":
            body["data"] = {"stop_requested": True}
    else:
        return False  # already terminal (completed/failed) — the bot's own callback is authoritative
    try:
        await drive_terminal(body)
        log.info("runtime-callback: drove synthetic %s for meeting %s (workload %s %s, was %s)",
                 body["status"], info.get("meeting_id"), workload_id, state, status)
        return True
    except Exception as e:  # noqa: BLE001 — best-effort; the stop/stale sweeps remain the backstop
        log.warning("runtime-callback: synthetic terminal POST failed for %s: %s", workload_id, e)
        return False


async def synthesize_failed_for_dead_workload(
    repo: Any,
    workload_id: Optional[str],
    state: Optional[str],
    drive_failed: Callable[[dict], Awaitable[Any]],
    *,
    log: Any,
) -> bool:
    """Back-compat shim (CC5): pre-#50 name that drove ONLY the pre-active → ``failed`` case. Now the
    runtime callback consumes BOTH terminal cases (pre-active → failed, was-active → completed) via
    ``synthesize_terminal_for_dead_workload``; this thin wrapper is retained so existing callers/tests
    that only exercised the pre-active path keep working. It delegates for pre-active rows and returns
    False (no-op) for anything else, preserving the old contract exactly."""
    if not workload_id or state not in TERMINAL_WORKLOAD_STATES or repo is None:
        return False
    try:
        info = await repo.find_by_container(bot_container_id=workload_id)
    except Exception as e:  # noqa: BLE001 — lookup is best-effort
        log.warning("runtime-callback: find_by_container failed for %s: %s", workload_id, e)
        return False
    if not info or info.get("status") not in _PRE_ACTIVE_STATUSES:
        return False  # old contract: only pre-active drove a synthetic failed
    return await synthesize_terminal_for_dead_workload(
        repo, workload_id, state, drive_failed, log=log
    )
