"""A bot that was IN the meeting and was ended under it: not by the user, not by the meeting, by the
cluster it runs on. The record must say so instead of naming a reason that did not happen.

Two shapes reach meeting-api, and today both are laundered into an ordinary ending:

  * **SIGTERM** (Kubernetes preemption, kubelet eviction, a node drain or shutdown). The bot's signal
    handler leaves the meeting, flushes, and reports ``completed`` / ``stopped`` itself — the event a
    user's Stop produces through the ``leave`` act — so the row read as a user stop and
    ``lifecycle.occurrence`` classed it ``USER_STOPPED``. The bot now says which it was, first-hand:
    a run a termination signal ended carries ``ended_by: "termination_signal"`` (``signals.ts``,
    ``orchestrator.ts``). With that marker and NO stop requested on the row (the stop route and the
    service-authority teardown both write ``data.stop_requested`` before the bot is told to go, and
    the runtime's own SIGTERM after a user stop therefore finds the flag set), nobody in Vexa ended
    the run: it was terminated from outside. The scal-1104 stand (4 Nov 2026, run fix-4) showed the
    case: a coredns replica preempted a running bot.
  * **SIGKILL** (the OOM killer at the bot's memory limit, a hard eviction, a forced delete). The bot
    reports nothing; the runtime later confirms the workload gone with a non-zero exit (``oom`` when
    the Pod's status said OOMKilled). The synthetic terminal wrote ``completed`` / ``left_alone``:
    a claim that the bot was left alone in the room, with nothing behind it.

What this module changes, and what it does not:

  * The meeting's STATUS is what it was (``completed``: the bot was admitted and served until it was
    ended), so the meeting's service record and everything rated from it stay as they are.
  * The false ``completion_reason`` (``stopped`` / ``left_alone``) is not written. The sealed
    lifecycle.v1 / api.v1 enums have no value for "the cluster ended the bot", so the reason is left
    absent rather than filled with a wrong one, and the cause is named on its own typed block:

        data.bot_termination = {"cause": terminated_externally | oom_killed | killed,
                                "attribution": "system_fault",
                                "source": bot_callback | runtime,
                                "detail": <the evidence, bounded>}

    with the same evidence in ``data.reason``.

Anything short of that evidence is left exactly as before: a stop requested by anyone, a ``stopped``
without the bot's marker (an older bot, a voice note whose audio ended, a ``leave`` act), an older
session's late report, a runtime answer with no exit code or exit 0 (a Pod that is simply
gone reads exit 0 on Kubernetes, which proves nothing either way).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

KEY = "bot_termination"

#: SIGTERM with no stop requested by Vexa: preemption, eviction, node drain or shutdown.
TERMINATED_EXTERNALLY = "terminated_externally"
#: The runtime's own verdict: the Pod's container was OOM-killed at its memory limit.
OOM_KILLED = "oom_killed"
#: A non-zero exit the runtime confirmed, with no terminal report from the bot.
KILLED = "killed"
CAUSES = frozenset({TERMINATED_EXTERNALLY, OOM_KILLED, KILLED})

#: The same word ``join_evidence.JoinFailureAttribution.SYSTEM_FAULT`` uses: ours to fix.
SYSTEM_FAULT = "system_fault"
SOURCES = frozenset({"bot_callback", "runtime"})
DETAIL_MAX_CHARS = 300

#: Row statuses in which the bot is in the meeting (``stopping`` means a stop was requested).
IN_MEETING = frozenset({"active", "needs_help"})
#: The field and value the bot stamps on a terminal that a termination signal (not a command) caused.
ENDED_BY = "ended_by"
TERMINATION_SIGNAL = "termination_signal"


def block(cause: str, source: str, detail: str) -> dict:
    return {"cause": cause, "attribution": SYSTEM_FAULT, "source": source,
            "detail": str(detail)[:DETAIL_MAX_CHARS]}


def sanitize(value: Any) -> Optional[dict]:
    """The block as it may be persisted, or None. Total: a malformed value is dropped, never raised."""
    if not isinstance(value, Mapping):
        return None
    cause, source = value.get("cause"), value.get("source")
    if cause not in CAUSES or source not in SOURCES or value.get("attribution") != SYSTEM_FAULT:
        return None
    return block(cause, source, value.get("detail") or "")


def relabel_bot_terminal(body: Mapping[str, Any], row: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """The bot's own ``completed`` / ``stopped``, ended by a termination signal, from a bot that was in
    the meeting with no stop requested: the event as it should be applied (no completion reason, the
    named block), or None to apply it as sent."""
    if (body.get("status") != "completed" or body.get("completion_reason") != "stopped"
            or body.get(ENDED_BY) != TERMINATION_SIGNAL or not row):
        return None
    data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
    if (
        row.get("status") not in IN_MEETING
        or data.get("stop_requested")
        or (row.get("latest_session_uid") not in (None, body.get("connection_id")))
    ):
        return None
    detail = ("the bot received a termination signal while in the meeting and no stop was requested "
              "(Kubernetes preemption, eviction, node drain or shutdown)")
    out = {k: v for k, v in body.items() if k != "completion_reason"}
    out["reason"] = detail
    out[KEY] = block(TERMINATED_EXTERNALLY, "bot_callback", detail)
    return out


def _code(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def from_dead_workload(*, workload_id: Optional[str], state: Optional[str], exit_code: Any,
                       stop_reason: Optional[str]) -> Optional[dict]:
    """The block for a bot that was in the meeting and whose workload the runtime confirmed ended
    with no terminal report from the bot, or None when the runtime's answer proves no kill (no exit
    code, or exit 0 that is not an OOM verdict)."""
    code = _code(exit_code)
    if stop_reason == "oom":
        cause = OOM_KILLED
    elif code is not None and code != 0:
        cause = KILLED
    else:
        return None
    parts = [f"workload {workload_id or 'unknown'} state={state or 'unknown'}"]
    if code is not None:
        parts.append(f"exitCode={code}")
    if stop_reason:
        parts.append(f"stopReason={stop_reason}")
    detail = (" ".join(parts) + "; the bot's workload ended while it was in the meeting, "
              "with no terminal report from the bot and no stop requested")
    return block(cause, "runtime", detail)


def was_active_terminal(session_uid: str, termination: Mapping[str, Any]) -> dict:
    """The synthetic terminal for a bot that was in the meeting and was ended under it."""
    return {"connection_id": session_uid, "status": "completed", "reason": termination["detail"],
            KEY: dict(termination)}
