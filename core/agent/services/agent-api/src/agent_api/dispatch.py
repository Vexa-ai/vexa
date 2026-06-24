"""dispatch.py — the unit dispatcher: turn a ``unit.v1`` DISPATCH into a runtime.v1 agent container.

Every trigger source (chat *now*, scheduled, event, transcription) funnels through ONE
``Dispatcher.dispatch``. It mints the per-dispatch identity token (``IdentityPort``), derives the
workload id + the output Stream, builds the worker ``env``, and asks the **Runtime** to spawn an
ISOLATED container. Agents **never** run in the control plane — isolation is the enforcement of the
governance, so there is no in-process path. Quota keys on the PERSON (``VEXA_OWNER`` = subject).

The runtime kernel runs ``profile`` + ``env`` opaquely; the worker reads its env (mounted workspaces,
the minted token, ``REDIS_URL`` + the ``unit:<id>:in/out`` topics, the ``start``) and runs the turn.
"""
from __future__ import annotations

import json
import logging

from . import contracts
from .config import Settings
from .ports import IdentityPort, RuntimePort
from .units import dispatch_id, input_topic, output_topic

logger = logging.getLogger("agent_api.dispatch")


def build_unit_env(settings: Settings, invocation: dict, *, unit_id: str, token: str) -> dict[str, str]:
    """Map a ``unit.v1`` dispatch to the worker's ``runtime.v1`` env (12-factor, P7). The minted token +
    the workspace LIST + the per-dispatch Stream topics travel here; the runtime injects them opaquely."""
    identity = invocation["identity"]
    subject = identity["subject"]
    # The dispatch's personal (rw) workspace folder is mounted at <root>/<subject>; the Runtime binds the
    # backing store (a host path / named volume) at <root>, and the worker works in the subject subdir.
    root = settings.workspaces_dir
    env = {
        "VEXA_OWNER": subject,                                    # quota + cred-brokerage axis = the person
        "VEXA_LAUNCHER": identity["launcher"],
        "VEXA_AGENT_IDENTITY_TOKEN": token,                      # the per-dispatch SIGNED token (verified at boundaries)
        "VEXA_RUNNER": invocation.get("runner", "claude-code"),
        "VEXA_UNIT_ID": unit_id,
        "VEXA_UNIT_TRIGGER": invocation["trigger"],
        "VEXA_UNIT_OUT_TOPIC": output_topic(unit_id),
        "VEXA_UNIT_IN_TOPIC": input_topic(unit_id),
        "VEXA_WORKSPACES": json.dumps(invocation["workspaces"]),  # the granted [{id,mode}] list to mount
        "VEXA_START": json.dumps(invocation["start"]),            # entrypoint(inline|path) | session(ref)
        "VEXA_WORKSPACE_MOUNT_SOURCE": settings.workspace_mount_source,  # host path / named volume (the store backing)
        "VEXA_WORKSPACE_MOUNT_TARGET": root,                      # where the Runtime binds it in the container
        "VEXA_WORKSPACE_PATH": f"{root}/{subject}",               # the worker's cwd (the subject's rw folder)
        "VEXA_WORKSPACE_STORE_URL": settings.workspace_store_url,
        "REDIS_URL": settings.redis_url,
    }
    # A live meeting dispatch consumes the meeting's transcript.v1 Stream (the meetings⊥agent seam).
    ctx = invocation.get("context") or {}
    meeting = ctx.get("meeting") if ctx.get("kind") == "meeting" else None
    if meeting and meeting.get("meeting_id"):
        env["VEXA_TRANSCRIPT_STREAM"] = f"tc:meeting:{meeting['meeting_id']}"
    return env


class Dispatcher:
    """Turns a ``unit.v1`` dispatch into a runtime.v1 agent workload — the one path every trigger funnels
    through. Validates the envelope at the seam (fail loud, P18), mints the token, and spawns."""

    def __init__(self, settings: Settings, runtime: RuntimePort, identity: IdentityPort) -> None:
        self._settings = settings
        self._runtime = runtime
        self._identity = identity
        self.dispatched: list[dict] = []  # observability — the dispatches that fired

    def dispatch(self, invocation: dict) -> str:
        """Validate + spawn. Returns the workload id. Raises on a non-conformant envelope (P18)."""
        contracts.validate_unit_invocation(invocation)  # fail loud at the seam
        self.dispatched.append(invocation)
        identity = invocation["identity"]
        uid = dispatch_id(invocation)
        token = self._identity.mint(
            identity["subject"], identity["launcher"], invocation["workspaces"], invocation.get("tools", []),
        )
        env = build_unit_env(self._settings, invocation, unit_id=uid, token=token)
        acked = self._runtime.spawn(uid, self._settings.agent_profile, env)
        logger.info(
            "dispatch SPAWN workload=%s trigger=%s subject=%s launcher=%s",
            acked, invocation["trigger"], identity["subject"], identity["launcher"],
        )
        return acked
