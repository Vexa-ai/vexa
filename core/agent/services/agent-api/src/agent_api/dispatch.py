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
import threading
from typing import Iterable, Iterator, Optional, Protocol

from . import contracts
from .config import Settings
from .ports import RuntimePort
from .spawn import build_worker_env

logger = logging.getLogger("agent_api.dispatch")


class UnitRunner(Protocol):
    """In-container execution of a unit turn over a subject's workspace (the proven MVP0/MVP1 claude
    path). A scheduled/event unit with an inline plan prompt runs through this rather than a separate
    runtime-spawned agent container — same as MVP0 chat. The runtime-workload spawn (per-person
    container isolation) stays the production target (see DECISIONS D5)."""

    def run(
        self, prompt: str, *, subject: str, session: Optional[str] = None, tools: Iterable[str] = (),
    ) -> Iterator[dict]: ...


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


def _can_run_local(invocation: dict) -> bool:
    """A non-meeting unit with an inline plan prompt runs in-container via the chat runner (MVP2);
    meeting/warm-without-prompt units take the runtime-spawn path."""
    ctx = invocation.get("context") or {}
    plan = invocation.get("plan") or {}
    return ctx.get("kind") != "meeting" and bool(plan.get("prompt"))


class Dispatcher:
    """Turns a ``unit.v1`` Invocation into execution. The one path every trigger source funnels through
    (message / scheduled / event / transcription). Two execution strategies: a non-meeting inline-prompt
    unit runs in-container via the injected ``local_runner`` (the proven claude path, MVP2); everything
    else spawns a ``runtime.v1`` agent workload (the production isolation target)."""

    def __init__(
        self,
        settings: Settings,
        runtime: RuntimePort,
        *,
        local_runner: Optional[UnitRunner] = None,
        local_sync: bool = False,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._local_runner = local_runner
        self._local_sync = local_sync  # tests drain synchronously; production backgrounds the turn
        self.dispatched: list[dict] = []  # observability — the invocations that fired execution

    def dispatch(self, invocation: dict) -> str:
        """Validate + execute. Returns an execution id. Raises on a non-conformant envelope (P18)."""
        contracts.validate_unit_invocation(invocation)  # fail loud at the seam
        self.dispatched.append(invocation)
        if self._local_runner is not None and _can_run_local(invocation):
            return self._run_local(invocation)
        return self._spawn(invocation)

    def _spawn(self, invocation: dict) -> str:
        env = build_unit_env(self._settings, invocation)
        wid = unit_id(invocation)
        acked = self._runtime.spawn(wid, self._settings.agent_profile, env)
        logger.info(
            "dispatch SPAWN workload=%s trigger=%s subject=%s lifecycle=%s",
            acked, invocation["trigger"], invocation["subject"],
            invocation.get("lifecycle", "oneshot"),
        )
        return acked

    def _run_local(self, invocation: dict) -> str:
        uid = unit_id(invocation)
        subject = invocation["subject"]
        prompt = invocation["plan"]["prompt"]
        tools = invocation.get("tools", [])

        def _drain() -> None:
            commit = None
            try:
                for ev in self._local_runner.run(prompt, subject=subject, tools=tools):
                    if ev.get("type") == "commit":
                        commit = ev.get("sha")
            except Exception as e:  # noqa: BLE001 — a unit failure must not crash the dispatcher
                logger.warning("dispatch LOCAL unit=%s subject=%s failed: %s", uid, subject, e)
                return
            logger.info("dispatch LOCAL unit=%s subject=%s commit=%s", uid, subject, commit or "(none)")

        logger.info("dispatch LOCAL unit=%s trigger=%s subject=%s", uid, invocation["trigger"], subject)
        if self._local_sync:
            _drain()
        else:
            threading.Thread(target=_drain, name=f"unit-{uid}", daemon=True).start()
        return uid
