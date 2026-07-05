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
import os

import contracts
from control_plane.workspace_attach import active_workspaces
from control_plane.system_mounts import GLOBAL_SLUG, SYSTEM_SLUG, global_mount, system_mount
from shared.config import Settings
from shared.ports import IdentityPort, RuntimePort
from shared.units import chat_session, dispatch_id, input_topic, output_topic

logger = logging.getLogger("agent_api.dispatch")


def build_active_set(settings: Settings, subject: str) -> list[dict]:
    """The subject's NORMAL active workspaces (the MIDDLE tier of the stack — WP-A1.1/A2.1): one entry
    per ACTIVE workspace in the additive set. Each entry: ``{slug, path, role, write, primary}`` with
    ``path`` the ABSOLUTE container path under the bound store root (the private baseline at the legacy
    ``<root>/<subject>``; every other active member in its store slot ``<root>/.attached/<subject>/<slug>``).

    Deterministic (primary first), generalizes to N mounts. A subject with no activated extras yields
    exactly the private baseline — identical to today's single-workspace behavior.

    Fails SOFT: any error resolving the on-disk set (a never-seeded subject, a store hiccup) falls back to
    the lone private-baseline mount so a dispatch never dies on mount resolution."""
    root = settings.workspaces_dir
    try:
        mounts = active_workspaces(root, subject)
    except Exception:  # noqa: BLE001 — mount resolution must never break a dispatch; fall back to the baseline
        logger.warning("active-set resolution failed for subject=%s — mounting the private baseline only", subject)
        mounts = []
    if not mounts:
        return [{"slug": subject, "path": f"{root}/{subject}", "role": "private", "write": True, "primary": True}]
    return [
        {"slug": m.slug, "path": m.path, "role": m.role, "write": m.write, "primary": m.primary}
        for m in mounts
    ]


def build_mount_set(settings: Settings, subject: str) -> list[dict]:
    """The full THREE-TIER mount STACK (AMENDMENT 4) the worker materializes — an ORDERED LIST, never
    special-cased slots, so it generalizes uniformly across all three runtime backends:

      1. ``_global``  GLOBAL SYSTEM  — platform-owned, READ-ONLY, ALWAYS mounted (when configured +
                      present; absent → skipped + logged). Behaviour/skills/tools. Agents never write it.
      2. active set   NORMAL private + shared workspaces — READ-WRITE (the additive set, WP-A2.1).
      3. ``_system``  PRIVATE SYSTEM — per-user, READ-WRITE, ALWAYS mounted. Create-if-absent (thin
                      template). Chats migrate here in a later WP.

    Order: ``[_global?, *active, _system]``. ``_global`` (RO) and ``_system`` (RW) are ALWAYS present
    (barring an unconfigured/absent _global); the normal active workspaces sit between them. Both system
    tiers fail SOFT into the active set so a dispatch never dies on system-mount resolution — but a
    system-tier failure is LOGGED loudly (it degrades the model's base behaviour / private memory)."""
    active = build_active_set(settings, subject)
    stack: list[dict] = []

    # Tier 1 — GLOBAL SYSTEM (read-only), when configured + present. Absent → skip (the stack still runs).
    try:
        g = global_mount(settings, settings.workspaces_dir)
        if g is not None:
            stack.append(g)
    except Exception:  # noqa: BLE001 — a bad _global must never break a dispatch; run without it
        logger.warning("global-system (_global) mount resolution failed — running the turn without it")

    # Tier 2 — the NORMAL active set (private baseline + activated extras).
    stack.extend(active)

    # Tier 3 — PRIVATE SYSTEM (read-write), always present (create-if-absent). A failure here degrades the
    # user's durable private-system memory — log loudly but never abort the dispatch.
    try:
        stack.append(system_mount(settings.workspaces_dir, subject))
    except Exception:  # noqa: BLE001
        logger.warning("private-system (_system) mount resolution failed for subject=%s — running without it", subject)

    return stack

# ── model-auth passthrough (the k8s/helm credential seam) ────────────────────
# The worker needs a MODEL credential, and delivery used to differ by substrate: the docker backend
# brokers creds itself (the HOST_CLAUDE_CREDENTIALS bind-mount + copying ANTHROPIC_*/VEXA_LLM_* from
# the runtime service env), but the k8s and process backends deliver ONLY this spec env — so a helm
# worker booted with no credential at all (claude CLI: "Not logged in" → chat "Model inference
# error"). agent-api therefore stamps an EXPLICIT allowlist from its own environment into every
# dispatch, making credential delivery uniform across backends. Never blanket-forward env (P14/P15):
# each entry is a var a core/agent/llm adapter (or the claude CLI itself) actually reads.
MODEL_AUTH_ENV_ALLOWLIST = (
    "CLAUDE_CODE_OAUTH_TOKEN",  # claude CLI subscription OAuth — the env twin of the docker credentials mount
    "ANTHROPIC_API_KEY",        # claude CLI + the llm/ completion adapters (last-resort fallback)
    "ANTHROPIC_AUTH_TOKEN",     # claude CLI gateway/OpenRouter token; llm/ adapters fall back to it
    "ANTHROPIC_BASE_URL",       # claude CLI gateway endpoint; openai_compat base-url fallback
    "VEXA_LLM_API_KEY",         # llm/ completion adapters' first-class credential (deliberately no Settings field)
    "VEXA_LLM_BASE_URL",        # llm/ completion adapters' first-class endpoint (pairs with the key above)
)


def build_unit_env(settings: Settings, invocation: dict, *, unit_id: str, token: str) -> dict[str, str]:
    """Map a ``unit.v1`` dispatch to the worker's ``runtime.v1`` env (12-factor, P7). The minted token +
    the workspace LIST + the per-dispatch Stream topics travel here; the runtime injects them opaquely."""
    identity = invocation["identity"]
    subject = identity["subject"]
    # The dispatch's personal (rw) workspace folder is mounted at <root>/<subject>; the Runtime binds the
    # backing store (a host path / named volume) at <root>, and the worker works in the subject subdir.
    root = settings.workspaces_dir
    # The ORDERED mount set (WP-A1.1 + WP-A2.1): the private baseline first, then every activated extra.
    # The whole store root is already bound by the runtime, so this is a WORKER-FACING contract (the paths
    # + roles the turn respects), not a per-mount bind — it generalizes uniformly across all three backends.
    mounts = build_mount_set(settings, subject)
    env = {
        "VEXA_OWNER": subject,                                    # quota + cred-brokerage axis = the person
        "VEXA_LAUNCHER": identity["launcher"],
        "VEXA_AGENT_IDENTITY_TOKEN": token,                      # the per-dispatch SIGNED token (minted now; boundary verification lands in Stage 2)
        "VEXA_RUNNER": invocation.get("runner", "claude-code"),
        "VEXA_UNIT_ID": unit_id,
        "VEXA_UNIT_TRIGGER": invocation["trigger"],
        "VEXA_UNIT_OUT_TOPIC": output_topic(unit_id),
        "VEXA_UNIT_IN_TOPIC": input_topic(unit_id),
        "VEXA_WORKSPACES": json.dumps(invocation["workspaces"]),  # the granted [{id,mode}] list to mount
        "VEXA_START": json.dumps(invocation["start"]),            # entrypoint(inline|path) | session(ref)
        "VEXA_WORKSPACE_MOUNT_SOURCE": settings.workspace_mount_source,  # host path / named volume (the store backing)
        "VEXA_WORKSPACE_MOUNT_TARGET": root,                      # where the Runtime binds it in the container
        "VEXA_WORKSPACE_PATH": f"{root}/{subject}",               # the worker's cwd (the PRIVATE baseline — mount set primary)
        "VEXA_MOUNTS": json.dumps(mounts),                       # the ordered active mount set [{slug,path,role,write,primary}]
        "VEXA_WORKSPACE_STORE_URL": settings.workspace_store_url,
        "REDIS_URL": settings.redis_url,
    }
    # Attribution (D4 / WP-A1.2): the per-mount turn commit is authored by the dispatch PRINCIPAL (the
    # authenticated human whose input drives the turn), committer stays the platform. Until membership/
    # sharing lands (later WPs) the principal IS the subject; a caller that already resolved a distinct
    # principal (VEXA_PRINCIPAL_NAME/EMAIL in agent-api's env, or on the invocation identity) wins.
    principal = invocation["identity"].get("principal") or {}
    env["VEXA_PRINCIPAL_NAME"] = (
        os.environ.get("VEXA_PRINCIPAL_NAME") or principal.get("name") or subject
    )
    env["VEXA_PRINCIPAL_EMAIL"] = (
        os.environ.get("VEXA_PRINCIPAL_EMAIL") or principal.get("email") or f"{subject}@vexa.local"
    )
    if settings.agent_model:
        env["VEXA_AGENT_MODEL"] = settings.agent_model
    if settings.meeting_model:
        env["VEXA_MEETING_MODEL"] = settings.meeting_model
    # llm-module dials (non-secret): completion provider + deployment-default model + the optional
    # operator model gate. The SECRETS (VEXA_LLM_API_KEY/BASE_URL) are brokered by the runtime.
    if settings.llm_provider:
        env["VEXA_LLM_PROVIDER"] = settings.llm_provider
    if settings.llm_model:
        env["VEXA_LLM_MODEL"] = settings.llm_model
    if settings.model_allowlist:
        env["VEXA_MODEL_ALLOWLIST"] = settings.model_allowlist
    # The chat conversation thread (default "main") — the worker namespaces its continuity session file
    # by this so multiple threads coexist in the one user workspace. Meeting/digest paths ignore it.
    if invocation["trigger"] == "message":
        env["VEXA_CHAT_SESSION"] = chat_session(invocation)
    # A live meeting dispatch consumes the meeting's transcript.v1 Stream (the meetings⊥agent seam).
    ctx = invocation.get("context") or {}
    meeting = ctx.get("meeting") if ctx.get("kind") == "meeting" else None
    if meeting and meeting.get("meeting_id"):
        env["VEXA_TRANSCRIPT_STREAM"] = f"tc:meeting:{meeting['meeting_id']}"
        env["VEXA_IDLE_TIMEOUT_SEC"] = str(settings.meeting_idle_timeout_sec)
        # Carry the meeting facts the post-meeting WRITE turn stamps into the kg entity frontmatter.
        env["VEXA_MEETING_ID"] = str(meeting["meeting_id"])
        if meeting.get("session_uid"):
            env["VEXA_MEETING_SESSION_UID"] = str(meeting["session_uid"])
        if meeting.get("platform"):
            env["VEXA_MEETING_PLATFORM"] = str(meeting["platform"])
        if meeting.get("transcript_start_id"):
            env["VEXA_TRANSCRIPT_START_ID"] = str(meeting["transcript_start_id"])
        if meeting.get("numeric_meeting_id"):
            # The meetings-domain ROW id (unique per meeting run). The worker keys its
            # processed-notes stream by it (proc:meeting:{numeric}) so a re-sent bot on the same
            # native link cannot mix/clobber a previous meeting's processed doc — and the
            # meeting-api db-writer (which knows its own row ids) drains that stream into the
            # meeting row's data JSONB for durability.
            env["VEXA_MEETING_NUMERIC_ID"] = str(meeting["numeric_meeting_id"])
    elif meeting and meeting.get("native_id"):
        # Chat GROUNDED in a live meeting (cookbook #1): no numeric meeting_id, but the meeting-scoped
        # tool needs the native id + platform to target meetings' published /transcripts. (The
        # serve_meeting path keys on meeting_id above; this is the chat-grounding seam.)
        env["VEXA_MEETING_NATIVE_ID"] = str(meeting["native_id"])
        if meeting.get("platform"):
            env["VEXA_MEETING_PLATFORM"] = str(meeting["platform"])
    # Model-auth passthrough (see MODEL_AUTH_ENV_ALLOWLIST above): stamp the explicit allowlist from
    # agent-api's own env. Set-and-nonblank only — an unset var stays ABSENT so the worker's
    # preflight/auth taxonomy (llm/errors.py) still reports the actionable missing-credential error
    # and a creds-less CI boot is unaffected. Backends that also broker creds keep the
    # dispatch-stamped value (docker_backend copies a key only when it is NOT already in the spec env).
    for key in MODEL_AUTH_ENV_ALLOWLIST:
        value = (os.environ.get(key) or "").strip()
        if value and key not in env:
            env[key] = value
    return env


# Internal routing hints that ride on context.meeting but are NOT part of the sealed MeetingRef
# (additionalProperties: false) — stripped before the unit.v1 contract check, like ctx.session.
# ``numeric_meeting_id`` is the meetings-domain ROW id (unique per meeting run, unlike the native
# id a re-sent bot reuses) — the worker keys its processed-notes stream by it so re-sends can never
# clobber a previous meeting's processed doc.
_INTERNAL_MEETING_HINTS = frozenset({"transcript_start_id", "numeric_meeting_id"})


def _without_chat_session(invocation: dict) -> dict:
    """A shallow copy with internal routing hints removed for the unit.v1 contract check."""
    ctx = invocation.get("context")
    if not isinstance(ctx, dict):
        return invocation
    meeting = ctx.get("meeting") if ctx.get("kind") == "meeting" else None
    needs_clean = "session" in ctx or (
        isinstance(meeting, dict) and bool(_INTERNAL_MEETING_HINTS & meeting.keys())
    )
    if not needs_clean:
        return invocation
    clean = dict(invocation)
    clean_ctx = {k: v for k, v in ctx.items() if k != "session"}
    if isinstance(meeting, dict) and (_INTERNAL_MEETING_HINTS & meeting.keys()):
        clean_ctx["meeting"] = {k: v for k, v in meeting.items() if k not in _INTERNAL_MEETING_HINTS}
    clean["context"] = clean_ctx
    return clean


class Dispatcher:
    """Turns a ``unit.v1`` dispatch into a runtime.v1 agent workload — the one path every trigger funnels
    through. Validates the envelope at the seam (fail loud, P18), mints the token, and spawns."""

    def __init__(self, settings: Settings, runtime: RuntimePort, identity: IdentityPort) -> None:
        self._settings = settings
        self._runtime = runtime
        self._identity = identity
        self.dispatched: list[dict] = []  # observability — the dispatches that fired

    @property
    def settings(self) -> Settings:
        return self._settings

    def dispatch(self, invocation: dict) -> str:
        """Validate + spawn. Returns the workload id. Raises on a non-conformant envelope (P18).

        ``context.session`` (the chat conversation thread) is an agent-api routing hint, not part of the
        published unit.v1 wire contract — it is stripped before the schema check so the envelope stays
        conformant, while ``dispatch_id`` / ``build_unit_env`` still read it off the in-memory dispatch."""
        contracts.validate_unit_invocation(_without_chat_session(invocation))  # fail loud at the seam
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
