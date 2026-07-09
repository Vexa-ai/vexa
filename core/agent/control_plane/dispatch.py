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
import threading
import time
from typing import Optional

import contracts
from control_plane.workspace_attach import active_workspaces, shared_active_mounts
from control_plane.workspace_purpose import read_purpose
from control_plane.system_mounts import GLOBAL_SLUG, SYSTEM_SLUG, global_mount, system_mount
from shared.config import Settings
from shared.ports import IdentityPort, RuntimePort
from shared.units import chat_session, dispatch_id, input_topic, output_topic

logger = logging.getLogger("agent_api.dispatch")


def build_active_set(settings: Settings, subject: str, memberships: Optional[list[dict]] = None) -> list[dict]:
    """The subject's NORMAL active workspaces (the MIDDLE tier of the stack — WP-A1.1/A2.1): one entry
    per ACTIVE workspace in the additive set. Each entry: ``{slug, path, role, write, primary}`` with
    ``path`` the ABSOLUTE container path under the bound store root (the private baseline at the legacy
    ``<root>/<subject>``; every other active member in its store slot ``<root>/.attached/<subject>/<slug>``).

    Deterministic (primary first), generalizes to N mounts. A subject with no activated extras yields
    exactly the private baseline — identical to today's single-workspace behavior.

    ``memberships`` (Lane A) = the subject's ``users.data.memberships[]`` index (the dispatcher resolves it
    once and passes the data in). When present, the SHARED workspaces the subject is a member of are
    appended after their private set, WRITABLE per the member's role (contributor/owner → rw, viewer → ro).
    NOTE: concurrent shared writes are not yet serialized (Lane W) — sequential attributed writes work
    (author = principal, via the per-mount commit path); true concurrency-safety lands with the writer.

    Fails SOFT: any error resolving the on-disk set (a never-seeded subject, a store hiccup) falls back to
    the lone private-baseline mount so a dispatch never dies on mount resolution."""
    root = settings.workspaces_dir
    try:
        mounts = active_workspaces(root, subject)
    except Exception:  # noqa: BLE001 — mount resolution must never break a dispatch; fall back to the baseline
        logger.warning("active-set resolution failed for subject=%s — mounting the private baseline only", subject)
        mounts = None
    if mounts is None:
        # Resolution ERROR → safe fallback to the lone baseline (a dispatch never dies with no home on a hiccup).
        private = [{"slug": subject, "path": f"{root}/{subject}", "role": "private", "write": True, "primary": True}]
    else:
        # A resolved-but-EMPTY set is intentional (the subject switched their baseline OFF and has no other
        # private workspace active) — respect it; the turn simply carries no private mount. NOT the error path.
        private = [
            {"slug": m.slug, "path": m.path, "role": m.role, "write": m.write, "primary": m.primary,
             "purpose": read_purpose(m.path)}
            for m in mounts
        ]
    if not memberships:
        return private
    # Lane A: append the shared workspaces the subject is a member of — WRITABLE per role (contributor/owner
    # write; viewer read-only). A shared-mount hiccup must never break the dispatch → fall soft.
    try:
        shared = shared_active_mounts(root, subject, memberships)
    except Exception:  # noqa: BLE001
        logger.warning("shared-mount resolution failed for subject=%s — mounting private workspaces only", subject)
        shared = []
    return private + [
        {"slug": s.slug, "path": s.path, "role": s.role, "write": s.write, "primary": False,
         "purpose": read_purpose(s.path)}
        for s in shared
    ]


def build_mount_set(settings: Settings, subject: str, memberships: Optional[list[dict]] = None) -> list[dict]:
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
    active = build_active_set(settings, subject, memberships)
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


def _allowlisted(model: str, allowlist: str) -> bool:
    """The operator's model gate (``VEXA_MODEL_ALLOWLIST``, comma-separated): empty = anything goes."""
    allowed = {m.strip() for m in allowlist.split(",") if m.strip()}
    return not allowed or model in allowed


def overlay_model_config(env: dict[str, str], config: dict, *, allowlist: str = "") -> None:
    """Overlay the subject's effective model config (Settings → Models: user pref > platform
    setting, resolved by admin-api) onto the dispatch env — field-by-field over the deployment
    env defaults, which stay the bottom fallback for anything unset.

    ``mode: custom`` points BOTH call shapes at the supplied gateway (an Anthropic-/OpenAI-
    compatible endpoint, e.g. LiteLLM/OpenRouter in front of an open-source model): the
    claude-code harness via ``ANTHROPIC_BASE_URL``/``ANTHROPIC_AUTH_TOKEN`` and the completion
    adapters via ``VEXA_LLM_PROVIDER=openai-compat`` + ``VEXA_LLM_BASE_URL``/``VEXA_LLM_API_KEY``.
    ``mode: subscription`` (or unset) keeps the deployment's brokered credential — the mounted
    Claude Code subscription / deployment key — and only the model names apply.

    Dispatch-stamped values WIN downstream (the runtime copies its own env only for keys absent
    here — docker_backend's ``key not in spawn_env``). Models are gated by the operator's
    allowlist: a non-allowlisted model is DROPPED (deployment default applies), never an error —
    a stale pref must not brick a turn."""
    model = (config.get("model") or "").strip()
    if model and _allowlisted(model, allowlist):
        env["VEXA_AGENT_MODEL"] = model     # harness turns (chat/docs/routines)
        env["VEXA_LLM_MODEL"] = model       # completion beats' default (meeting_model beats it)
    elif model:
        logger.warning("model %r not in VEXA_MODEL_ALLOWLIST — using deployment default", model)
    meeting_model = (config.get("meeting_model") or "").strip()
    if meeting_model and _allowlisted(meeting_model, allowlist):
        env["VEXA_MEETING_MODEL"] = meeting_model
    elif meeting_model:
        logger.warning("meeting model %r not in VEXA_MODEL_ALLOWLIST — using deployment default",
                       meeting_model)
    if (config.get("mode") or "").strip() != "custom":
        return
    base_url = (config.get("base_url") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    if not base_url:
        return  # custom mode without an endpoint is inert — deployment credentials still apply
    env["ANTHROPIC_BASE_URL"] = base_url
    env["VEXA_LLM_PROVIDER"] = "openai-compat"
    env["VEXA_LLM_BASE_URL"] = base_url
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
        env["VEXA_LLM_API_KEY"] = api_key


def _worker_cwd(root: str, subject: str, mounts: list[dict]) -> str:
    """The worker's CWD — the workspace it 'lives in', whose ``CLAUDE.md`` auto-loads as project memory.

    Normally the private baseline (the primary mount). But the baseline can be switched OFF, in which case
    it is absent from the mount set — the cwd must then FOLLOW the active set (the first NORMAL writable
    workspace, never a system tier), not stay pinned to the disconnected baseline home (which would make the
    agent describe/read a workspace the user turned off). Falls back to the baseline home only when nothing
    normal is active (a degenerate turn with only the system tiers)."""
    primary = next((m for m in mounts if m.get("primary") and m.get("path")), None)
    if primary:
        return primary["path"]
    normal = next((m for m in mounts
                   if m.get("write") and m.get("role") not in ("global", "system") and m.get("path")), None)
    return normal["path"] if normal else f"{root}/{subject}"


def build_unit_env(settings: Settings, invocation: dict, *, unit_id: str, token: str,
                   memberships: Optional[list[dict]] = None,
                   model_config: Optional[dict] = None) -> dict[str, str]:
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
    mounts = build_mount_set(settings, subject, memberships)
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
        "VEXA_WORKSPACE_PATH": _worker_cwd(root, subject, mounts),  # the worker's cwd — the primary baseline, or (if it's switched off) the first active normal workspace
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
    # Settings → Models (per-user/platform config from admin-api) beats the deployment env
    # defaults stamped above, field-by-field; anything it leaves unset falls through unchanged.
    if model_config:
        overlay_model_config(env, model_config, allowlist=settings.model_allowlist)
    # The chat conversation thread (default "main") — the worker namespaces its continuity session file
    # by this so multiple threads coexist in the one user workspace. Meeting/digest paths ignore it.
    if invocation["trigger"] == "message":
        env["VEXA_CHAT_SESSION"] = chat_session(invocation)
        # The warm-serve window: how long the worker keeps serving unit:<id>:in after its last turn.
        # The engine's own default is a tight 120s; chat stamps the (longer) configured window so a
        # follow-up message lands on the WARM worker (no container/CLI cold start).
        env["VEXA_IDLE_TIMEOUT_SEC"] = str(settings.chat_idle_timeout_sec)
    # A live meeting dispatch consumes the meeting's transcript.v1 Stream (the meetings⊥agent seam).
    ctx = invocation.get("context") or {}
    meeting = ctx.get("meeting") if ctx.get("kind") == "meeting" else None
    if meeting and meeting.get("meeting_id"):
        # P0 (cross-tenant leak fix): the transcript carrier keys on the meetings-domain ROW id
        # (``numeric_meeting_id`` — unique per meeting run), NOT the native meeting id. The native id
        # is NOT unique: it collides across DIFFERENT users of the same meeting link (a shared
        # ``tc:meeting:{native}`` LEAKED one tenant's transcript to another) AND across ONE user's
        # repeated rows (wrong-row hydration). ``meeting['meeting_id']`` is the routing key the watcher
        # froze (the native id today); the row id rides SEPARATELY as ``numeric_meeting_id``. Key the
        # carrier by the row id when known, falling back to the routing key only for a meeting that
        # never resolved a row id (surfaced under its own key, still isolated per that key).
        row_id = meeting.get("numeric_meeting_id") or meeting["meeting_id"]
        env["VEXA_TRANSCRIPT_STREAM"] = f"tc:meeting:{row_id}"
        env["VEXA_IDLE_TIMEOUT_SEC"] = str(settings.meeting_idle_timeout_sec)
        # Carry the meeting facts the post-meeting WRITE turn stamps into the kg entity frontmatter.
        # VEXA_MEETING_ID is the human-readable NATIVE id (nuance #1: the readable kg doc name
        # ``kg/entities/meeting/{native}.md`` must survive even though the carriers key by row id).
        # The watcher now routes by the ROW id (``meeting_id`` == row id) and carries the native
        # SEPARATELY as ``native_id`` for display; older callers (``/api/meeting/start|process``) still
        # pass the native as ``meeting_id``. Prefer the explicit ``native_id`` hint, falling back to
        # ``meeting_id`` (native there) — never the numeric row id, which is unreadable.
        display_native = meeting.get("native_id") or meeting["meeting_id"]
        env["VEXA_MEETING_ID"] = str(display_native)
        if meeting.get("session_uid"):
            env["VEXA_MEETING_SESSION_UID"] = str(meeting["session_uid"])
        if meeting.get("platform"):
            env["VEXA_MEETING_PLATFORM"] = str(meeting["platform"])
        if meeting.get("transcript_start_id"):
            env["VEXA_TRANSCRIPT_START_ID"] = str(meeting["transcript_start_id"])
        if meeting.get("numeric_meeting_id"):
            # The meetings-domain ROW id (unique per meeting run). The worker keys its
            # processed-notes stream AND its transcript-consume stream by it
            # (tc:/proc:meeting:{numeric}) so a re-sent bot on the same native link — or a DIFFERENT
            # tenant on the same link — can never mix/clobber/read another meeting's data. The
            # meeting-api db-writer (which knows its own row ids) drains proc:meeting:{numeric} into the
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
# id a re-sent bot reuses) — the worker keys its transcript/processed streams by it so re-sends (or a
# DIFFERENT tenant on the same link) can never clobber/read another meeting's data. ``native_id`` is
# the human-readable Meet code carried for DISPLAY only (the kg doc name / title); the routing
# ``meeting_id`` is the row id. Both are agent-api internal — the sealed MeetingRef forbids them.
_INTERNAL_MEETING_HINTS = frozenset({"transcript_start_id", "numeric_meeting_id", "native_id"})


def _without_chat_session(invocation: dict) -> dict:
    """A shallow copy with internal routing hints removed for the unit.v1 contract check. Also strips
    ``identity.principal`` — an internal attribution hint (the human editor's display id/email) that
    ``build_unit_env`` reads off the in-memory dispatch, but which the sealed identity schema
    (additionalProperties: false) forbids on the wire."""
    ctx = invocation.get("context")
    identity = invocation.get("identity")
    has_principal = isinstance(identity, dict) and "principal" in identity
    ctx_dict = ctx if isinstance(ctx, dict) else None
    meeting = ctx_dict.get("meeting") if ctx_dict and ctx_dict.get("kind") == "meeting" else None
    needs_clean = has_principal or (ctx_dict is not None and (
        "session" in ctx_dict or (isinstance(meeting, dict) and bool(_INTERNAL_MEETING_HINTS & meeting.keys()))
    ))
    if not needs_clean:
        return invocation
    clean = dict(invocation)
    if has_principal:
        clean["identity"] = {k: v for k, v in identity.items() if k != "principal"}
    if ctx_dict is not None:
        clean_ctx = {k: v for k, v in ctx_dict.items() if k != "session"}
        if isinstance(meeting, dict) and (_INTERNAL_MEETING_HINTS & meeting.keys()):
            clean_ctx["meeting"] = {k: v for k, v in meeting.items() if k not in _INTERNAL_MEETING_HINTS}
        clean["context"] = clean_ctx
    return clean


class Dispatcher:
    """Turns a ``unit.v1`` dispatch into a runtime.v1 agent workload — the one path every trigger funnels
    through. Validates the envelope at the seam (fail loud, P18), mints the token, and spawns."""

    def __init__(self, settings: Settings, runtime: RuntimePort, identity: IdentityPort,
                 membership_index=None, model_config=None, warm_stream=None) -> None:
        self._settings = settings
        self._runtime = runtime
        self._identity = identity
        # Warm delivery (the lost-turn fix): the redis client used to pre-deliver message-trigger
        # prompts to unit:<id>:in and to watch for the worker's turn-accepted ack. Injectable for
        # tests; None → built lazily from settings.redis_url (unreachable redis fails soft into the
        # legacy spawn-only path — a dispatch never dies on the warm seam; retried after 60s).
        self._warm_stream = warm_stream
        self._warm_retry_at = 0.0
        # Lane A: the derived memberships index (users.data.memberships[]). Used to resolve, per dispatch,
        # the SHARED workspaces the subject is a member of so they enter the mount set. None → no shared
        # mounts (the private stack still dispatches exactly as before).
        self._membership_index = membership_index
        # Settings → Models: the subject's effective model config resolver (shared.adapters.
        # AdminApiModelConfig — user pref > platform setting over the admin-api internal edge).
        # None → deployment env defaults only, exactly as before.
        self._model_config = model_config
        self.dispatched: list[dict] = []  # observability — the dispatches that fired

    @property
    def settings(self) -> Settings:
        return self._settings

    def resolve_model_config(self, subject: str) -> Optional[dict]:
        """The subject's effective Settings → Models config (user pref > platform setting).
        ``{}`` = resolved empty / no resolver wired; ``None`` = the lookup FAILED — callers fail
        OPEN (a down identity service must never block a turn, same contract as dispatch)."""
        if self._model_config is None:
            return {}
        try:
            return self._model_config.resolve(subject) or {}
        except Exception:  # noqa: BLE001
            logger.warning("model-config lookup failed for subject=%s — treating as env defaults", subject)
            return None

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
        # Lane A: resolve the subject's shared memberships once (fail soft — a membership-index hiccup must
        # never break a dispatch; the private stack still mounts). Passed as data into the mount builder.
        memberships = None
        if self._membership_index is not None:
            try:
                memberships = self._membership_index.list(identity["subject"])
            except Exception:  # noqa: BLE001
                logger.warning("membership-index lookup failed for subject=%s — dispatching private mounts only",
                               identity["subject"])
        # Settings → Models: resolve the subject's effective model config (fail soft — a down
        # identity service must never block a turn; the deployment env defaults still dispatch).
        # NOTE: /api/chat gates message-triggers upstream (credential preflight) — this path stays
        # ungated so async triggers (scheduled/event/transcription) never lose a dispatch; their
        # credential-less failure mode is the clean rewritten done frame (llm/errors taxonomy).
        model_config = self.resolve_model_config(identity["subject"])
        env = build_unit_env(self._settings, invocation, unit_id=uid, token=token, memberships=memberships,
                             model_config=model_config)
        # WARM DELIVERY (the lost-turn fix). The runtime's create is an IDEMPOTENT TOUCH for a
        # workload that is still starting/running (ADR-0027) — it returns the live status and
        # DISCARDS the spec env, where a chat message's prompt rides. So a message sent while the
        # thread's worker is alive (mid-turn, or parked in its serve() idle window) used to
        # dispatch NOWHERE: the UI hung on "Starting agent" until the worker idled out. Fix:
        # pre-deliver every message-trigger prompt to unit:<id>:in BEFORE the spawn call —
        #   · worker WARM  → create touches; the parked serve() loop consumes the message (no
        #     container / CLI cold start — this is also the fast path);
        #   · worker GONE  → create really spawns; the fresh worker anchors its in-topic read at
        #     the boot tail, SKIPS the pre-delivered copy, and runs the same prompt as its
        #     entrypoint (no double turn).
        # A watchdog then waits for the worker's turn-accepted ack and respawns once if the worker
        # exited in the XADD↔idle-exit race window without taking the message.
        delivery = self._predeliver(uid, invocation) if invocation["trigger"] == "message" else None
        acked = self._runtime.spawn(uid, self._settings.agent_profile, env)
        if delivery is not None:
            self._watch_delivery(uid, env, tail=delivery)
        logger.info(
            "dispatch SPAWN workload=%s trigger=%s subject=%s launcher=%s warm_delivery=%s",
            acked, invocation["trigger"], identity["subject"], identity["launcher"], delivery is not None,
        )
        return acked

    # ── warm delivery (message triggers) ─────────────────────────────────────

    _ACK_DEADLINE_SEC = 10.0   # worker boot is ~1-2s; a warm pickup acks in ms
    _ACK_POLL_SEC = 0.5

    def _redis(self):
        """The warm-delivery redis client — lazy, fail-soft (None = warm path off this dispatch),
        retried a minute after a failure so a transient redis blip doesn't disable warm delivery
        until restart."""
        if self._warm_stream is not None:
            return self._warm_stream
        if time.monotonic() < self._warm_retry_at:
            return None
        try:
            import redis

            self._warm_stream = redis.from_url(
                self._settings.redis_url, decode_responses=True,
                socket_connect_timeout=0.5, socket_timeout=2,
            )
        except Exception:  # noqa: BLE001
            logger.warning("warm-delivery redis client unavailable — dispatching spawn-only")
            self._warm_retry_at = time.monotonic() + 60.0
            return None
        return self._warm_stream

    def _warm_fail(self) -> None:
        """An op on the warm client failed — drop it and back off (the spawn path still dispatched)."""
        self._warm_stream = None
        self._warm_retry_at = time.monotonic() + 60.0

    def _predeliver(self, uid: str, invocation: dict) -> Optional[str]:
        """XADD the message's prompt to ``unit:<uid>:in`` with a matching nonce; returns the
        out-stream TAIL id the watchdog reads the ack from (None = warm path unavailable)."""
        prompt = ((invocation.get("start") or {}).get("entrypoint") or {}).get("inline")
        if not prompt:
            return None  # session-only starts have no inline prompt to deliver
        r = self._redis()
        if r is None:
            return None
        nonce = f"{uid}:{time.time_ns()}"
        try:
            entries = r.xrevrange(output_topic(uid), count=1)
            tail = entries[0][0] if entries else "0-0"
            r.xadd(input_topic(uid), {"turn": json.dumps({"type": "message", "prompt": prompt, "nonce": nonce})})
        except Exception:  # noqa: BLE001 — warm delivery must never break a dispatch
            logger.warning("warm pre-delivery failed for unit=%s — relying on the spawn path", uid)
            self._warm_fail()
            return None
        return tail

    def _workload_gone(self, uid: str) -> bool:
        """True when the runtime says the workload is NOT alive. Errors read as gone: a respawn on
        uncertainty is SAFE — the runtime's create is a touch for a live workload, never a kill."""
        try:
            return self._runtime.await_done(uid, timeout_sec=0.0) not in ("starting", "running")
        except Exception:  # noqa: BLE001
            return True

    def _watch_delivery(self, uid: str, env: dict[str, str], *, tail: str) -> None:
        """Background ack watchdog: a ``turn-accepted`` event after ``tail`` proves the unit took a
        turn (ours warm, or the cold entrypoint running the same prompt). None + worker gone = the
        idle-exit race ate the message → respawn ONCE (the fresh worker's entrypoint re-runs the
        prompt; the stale in-topic copy is behind its boot anchor). None + worker alive = the
        message is queued behind a long-running turn — leave it be, log at deadline."""
        def watch() -> None:
            deadline = time.monotonic() + self._ACK_DEADLINE_SEC
            cursor = tail
            respawned = False
            while time.monotonic() < deadline:
                time.sleep(self._ACK_POLL_SEC)
                r = self._redis()
                if r is None:
                    return
                try:
                    entries = r.xrange(output_topic(uid), f"({cursor}", "+", count=200)
                except Exception:  # noqa: BLE001
                    self._warm_fail()
                    return
                for entry_id, fields in entries:
                    cursor = entry_id
                    try:
                        ev = json.loads(fields.get("event", "{}"))
                    except (TypeError, ValueError):
                        continue
                    if ev.get("type") == "turn-accepted":
                        return  # the turn is running
                if not respawned and self._workload_gone(uid):
                    logger.warning("warm delivery missed for unit=%s (worker exited) — respawning", uid)
                    try:
                        self._runtime.spawn(uid, self._settings.agent_profile, env)
                    except Exception:  # noqa: BLE001
                        logger.exception("delivery-watchdog respawn failed for unit=%s", uid)
                        return
                    respawned = True
            if not respawned:
                logger.warning("no turn-accepted within %.0fs for unit=%s — turn queued behind a long "
                               "turn, or lost to a concurrent boot", self._ACK_DEADLINE_SEC, uid)

        threading.Thread(target=watch, daemon=True, name=f"warm-watch-{uid}").start()
