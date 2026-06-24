"""dispatch.py — the unit dispatcher (the trigger layer).

Generalizes the meeting `bridge` + the runtime `scheduler` into ONE fan-in: any trigger source
(message / scheduled / event / transcription) builds a ``unit.v1`` Invocation and the dispatcher turns
it into a ``runtime.v1`` agent worker (profile ``agent``, the per-person workspace + scoped identity in
its env). It validates the Invocation at the seam (fail loud, P18) and keys quota on the PERSON
(``VEXA_OWNER`` = ``subject``) — so a person can't spawn unbounded warm units.

This is a MODULE in agent-api (P10 — no independent-scale force): the durable cron stays
``runtime/scheduler.py`` (a routine is a ``schedule.v1`` job whose body is a unit Invocation), the
meetings adapter stays ``bridge.py``; this is the union they both feed.
"""
from __future__ import annotations

import hashlib
import logging

from . import contracts
from .config import Settings
from .ports import RuntimePort
from .spawn import build_worker_env

logger = logging.getLogger("agent_api.dispatch")


def build_unit_env(settings: Settings, invocation: dict) -> dict[str, str]:
    """Map a ``unit.v1`` Invocation to the worker's ``runtime.v1`` env (extends ``build_worker_env``)."""
    env = build_worker_env(settings, invocation["workspace_repo"])
    env["VEXA_OWNER"] = invocation["subject"]              # quota + cred-brokerage axis = the person
    env["VEXA_UNIT_TRIGGER"] = invocation["trigger"]
    env["VEXA_UNIT_LIFECYCLE"] = invocation.get("lifecycle", "oneshot")
    ref = invocation.get("workspace_ref")
    if ref:
        env["VEXA_WORKSPACE_REF"] = ref
    return env


def unit_id(invocation: dict) -> str:
    """A stable workload id for the invocation. Meeting units key on the session uid (one warm unit
    per live meeting); others on the output topic, else a subject+trigger digest."""
    ctx = invocation.get("context", {}) or {}
    if ctx.get("kind") == "meeting" and ctx.get("meeting"):
        return f"agent-{ctx['meeting']['session_uid']}"
    topic = (invocation.get("output") or {}).get("topic")
    if topic:
        return "agent-" + topic.removeprefix("unit:").removesuffix(":out")
    digest = hashlib.sha1(
        f"{invocation['subject']}|{invocation['trigger']}|{invocation.get('plan', {})}".encode()
    ).hexdigest()[:10]
    return f"agent-{invocation['subject']}-{invocation['trigger']}-{digest}"


class Dispatcher:
    """Turns a ``unit.v1`` Invocation into a ``runtime.v1`` agent spawn. The one path every trigger
    source funnels through (message / scheduled / event / transcription)."""

    def __init__(self, settings: Settings, runtime: RuntimePort) -> None:
        self._settings = settings
        self._runtime = runtime
        self.dispatched: list[dict] = []  # observability — the invocations that fired a spawn

    def dispatch(self, invocation: dict) -> str:
        """Validate + spawn. Returns the acknowledged workloadId. Raises on a non-conformant envelope."""
        contracts.validate_unit_invocation(invocation)  # fail loud at the seam (P18)
        env = build_unit_env(self._settings, invocation)
        wid = unit_id(invocation)
        acked = self._runtime.spawn(wid, self._settings.agent_profile, env)
        self.dispatched.append(invocation)
        logger.info(
            "dispatch SPAWN workload=%s trigger=%s subject=%s lifecycle=%s",
            acked, invocation["trigger"], invocation["subject"],
            invocation.get("lifecycle", "oneshot"),
        )
        return acked
