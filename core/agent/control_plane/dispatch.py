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
from control_plane.workspace_attach import SEED_SLOT, active_workspaces, shared_active_mounts
from control_plane.workspace_membership import reconciled_memberships
from control_plane.workspace_purpose import read_purpose
from control_plane import global_layer
from control_plane.meeting_room import group_desk_mount, resolve_desks
from control_plane.system_mounts import GLOBAL_SLUG, SYSTEM_SLUG, global_mount, system_mount
from shared.config import Settings
from shared import delegation
from shared.ports import IdentityPort, RuntimePort
from shared import units
from shared.units import chat_session, dispatch_id, input_topic, output_topic

logger = logging.getLogger("agent_api.dispatch")


def _ensure_workspace_exists(settings: Settings, subject: str) -> bool:
    """Make sure this subject HAS a workspace directory before we ask the runtime to mount it.

    The seeding seam is documented as lazy — "seeded on first turn" — but it can never run,
    because the worker BIND-MOUNTS ``<root>/<subject>`` and the runtime refuses to start a
    container whose bind source does not exist:

        docker start vexa-worker-75-chat-meet-32 failed (404): cannot access path
        /var/lib/docker/volumes/..._agent-workspaces/_data/75: no such file or directory

    which surfaces to the caller as a bare ``500 Internal Server Error`` from ``/api/chat``.
    In practice the directory only ever existed because the EMAIL ONBOARDING flow called
    ``POST /api/workspace/init`` explicitly. Every path that reaches a chat WITHOUT going
    through that flow therefore 500s on its very first turn — and the most important such path
    is the one the growth loop is built on: an attendee who presses the button in a meeting
    follow-up is a brand-new platform user, has never onboarded, and lands on an error.

    Idempotent and fail-soft: an existing workspace is untouched, and a seeding failure logs
    and lets the spawn proceed exactly as before rather than turning a degraded turn into no
    turn at all.
    """
    try:
        from pathlib import Path

        from shared.seeding import resolve_seed_dir, seed_workspace
        ws = Path(settings.workspaces_dir) / str(subject)
        if (ws / ".git").exists():
            return False
        seed_workspace(ws, resolve_seed_dir(
            getattr(settings, "default_template", None),
            seeds_root=getattr(settings, "workspace_seeds_dir", None)))
        logger.info("dispatch SEEDED workspace for subject=%s (first turn, never onboarded)", subject)
        return True
    except Exception:  # noqa: BLE001 — never let this be the reason a turn does not happen
        logger.warning("workspace ensure failed for subject=%s — spawning anyway", subject, exc_info=True)
        return False


def build_active_set(settings: Settings, subject: str, memberships: Optional[list[dict]] = None) -> list[dict]:
    """The subject's NORMAL active workspaces (the MIDDLE tier of the stack — WP-A1.1/A2.1): one entry
    per ACTIVE workspace in the additive set. Each entry: ``{slug, path, role, write, primary}`` with
    ``path`` the ABSOLUTE container path under the bound store root (the private baseline at the legacy
    ``<root>/<subject>``; every other active member in its store slot ``<root>/.attached/<subject>/<slug>``).

    Deterministic (primary first), generalizes to N mounts. A subject with no activated extras yields
    exactly the private baseline — identical to today's single-workspace behavior.

    ``memberships`` (Lane A) = the subject's ``users.data.memberships[]`` index rows (the dispatcher
    resolves them once and passes the data in); ``None`` = Lane A off (no index wired) — no shared mounts.
    When non-None (an empty list included), the rows are UNIONed with the authoritative
    ``policy/members.json`` scan, so a dead or incomplete index cannot silently drop a locally-held grant
    from the mount set; the SHARED workspaces the subject is a member of are then appended after their
    private set, WRITABLE per the member's role (contributor/owner → rw, viewer → ro).
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
    if memberships is None:
        return private
    # Lane A: append the shared workspaces the subject is a member of — WRITABLE per role (contributor/owner
    # write; viewer read-only). The passed rows are the INDEX's view and may be incomplete (the lost-write
    # incident) — union in the authoritative policy/members.json scan, which only ever ADDS candidates;
    # shared_active_mounts still re-checks the role authoritatively per workspace. A shared-mount hiccup
    # must never break the dispatch → fall soft.
    rows, _ = reconciled_memberships(root, subject, lambda _subject: memberships)
    try:
        shared = shared_active_mounts(root, subject, rows)
    except Exception:  # noqa: BLE001
        logger.warning("shared-mount resolution failed for subject=%s — mounting private workspaces only", subject)
        shared = []
    return private + [
        {"slug": s.slug, "path": s.path, "role": s.role, "write": s.write, "primary": False,
         "purpose": read_purpose(s.path)}
        for s in shared
    ]


def build_mount_set(settings: Settings, subject: str, memberships: Optional[list[dict]] = None,
                    room: Optional[dict] = None,
                    scaffold_workspaces: Optional[list[str]] = None) -> list[dict]:
    """The full THREE-TIER mount STACK (AMENDMENT 4) the worker materializes — an ORDERED LIST, never
    special-cased slots, so it generalizes uniformly across all three runtime backends:

      1. ``_global``  GLOBAL SYSTEM  — platform-owned and ALWAYS mounted. Missing configuration fails
                      the dispatch closed; a worker never runs without organisation context.
      2. active set   NORMAL private + shared workspaces — READ-WRITE (the additive set, WP-A2.1).
      3. ``_system``  PRIVATE SYSTEM — per-user, READ-WRITE, ALWAYS mounted. Create-if-absent (thin
                      template). Chats migrate here in a later WP.

    Order: ``[_global, *active, *room, _system]``. The normal active workspaces sit between the system
    tiers. ``_global`` fails CLOSED because organisation context is a hard invariant; ``_system``
    remains fail-soft so a private-memory storage fault cannot suppress the user's turn.

    ── THE ROOM (tier 2b, additive) ────────────────────────────────────────────────────────────────
    ``room`` (``{"meeting_id", "subjects": [...]}``) is the post-meeting MEETING ROOM: the OTHER
    attendees of one meeting, whose own workspaces this turn may READ. It is resolved SERVER-SIDE by
    the caller of this function (``api._resolve_room`` — meeting entitlement + owner check + the
    meeting's reader roster) and is NEVER anything a request body asserted; see
    ``control_plane/meeting_room.py`` for the three gates.

    Room entries are appended AFTER the subject's own active set and BEFORE ``_system``, so the
    existing order/semantics are untouched: with ``room=None`` (every dispatch that names no meeting)
    the stack is byte-identical to before. They are always ``write: False`` (the runtime binds them
    ``:ro``) and ``primary: False``, they can never shadow or duplicate a path the subject's own set
    already holds, and no other subject's ``_system`` is reachable through them.

    DECISION 22 — A ROOM RUN WRITES NO DESK. The run reads desks and writes ONE shared artefact whose
    home is the meeting row; flows distributes it into every attendee's desk afterwards, organizer
    included, nobody special.

    THE ROOM IS THE OTHER ATTENDEES' DESKS, AND ONLY THOSE (ruling 2026-09-02, correcting this
    docstring). This code used to read "not the organizer's either" as a mount-mode instruction and
    demote the SUBJECT'S OWN desk to ``write: False`` as well. That is not a narrowing, it is a
    broken turn: the worker writes its delegation credential to ``<cwd>/.claude``, the cwd IS the
    subject's own desk when the meeting has no group, and the spawn therefore died on
    ``OSError: [Errno 30] Read-only file system: '/workspaces/129/.claude'`` before reaching the
    model — every post-meeting turn for every non-admin subject, invisible because the instance's
    only admin is the founder. Whether the turn WRITES a desk is enforced where it belongs, by
    ``process_meeting``'s HEAD-before/HEAD-after check, not by taking away a mount mode the runtime
    needs to start at all.

    So in room mode: the ROOM entries are ``write: False`` (they always were, where they are
    appended below), the subject's own desk KEEPS its write bit, and the subject's OTHER activated
    workspaces are still demoted — they are neither the room nor the subject's own desk, and a run
    scoped to one meeting has no business writing them. The GROUP DESK, when the meeting is bound to
    a shared workspace and the subject is a contributor/owner, keeps its write bit AND becomes the
    turn's cwd (``primary``), because that desk is the room's shared state and the run maintains it.
    ``_system`` is NOT a desk and stays read-write — chat continuity anchors there
    (``worker/engine._continuity_root``), and taking it away would break the turn, not narrow it.

    ── THE SCAFFOLD (PRD 5.5) ─────────────────────────────────────────────────────────────────────
    ``scaffold_workspaces`` is the mount list a SCAFFOLD RECORD states (agent-api resolved the
    record and checked it belongs to this subject before passing it here — it never arrives from
    a request body). It does two things and deliberately not a third:

      * it ORDERS the middle tier, scaffold-named workspaces first, so the turn's cwd and the
        worker's reading order are the ones the link was composed for;
      * it ADDS a named workspace the subject has not activated — the meeting's group desk, most
        often — resolved through ``group_desk_mount``, which asks the workspace's own
        ``policy/members.json`` whether this subject may write it. Naming a slug is not a grant.

    It does NOT REMOVE anything. For a human chat ``workspaces[]`` is ATTENTION, not permission
    (PRD 7: "soft for a human, hard for a run") — a person's other desks stay mounted, because a
    chat restores what was in focus and never a sandbox. The hard isolation axis is ``room``,
    above, and it is a different argument on purpose so the two can never be confused."""
    active = build_active_set(settings, subject, memberships)
    stack: list[dict] = []

    # Tier 1 — GLOBAL SYSTEM. Fail before spawn rather than silently run an under-grounded agent.
    # The INSTANCE ADMIN receives the one sanctioned read-write setup mount: their setup
    # conversation is the only writer the organisation tier has. The role is `users.data.is_admin`,
    # claimed at first sign-in and asked of admin-api (global_layer.is_admin, which keeps the env
    # allow-list as an operator override and fails CLOSED when it cannot resolve the role) — an env
    # list could never have been the definition, because the admin does not exist yet when the
    # deployment env is written.
    g = global_mount(settings, settings.workspaces_dir)
    if global_layer.is_admin(settings, str(subject)):
        g = {**g, "write": True}
    stack.append(g)

    # Tier 2 — the NORMAL active set (private baseline + activated extras). In ROOM mode the
    # subject's OTHER activated workspaces are demoted to read-only; the subject's OWN desk and the
    # meeting's group desk are not (see DECISION 22 in the docstring — the room is the other
    # attendees' desks, and those are appended below already read-only).
    if room:
        group = str(room.get("group_workspace_id") or "")
        # THE SUBJECT'S OWN DESK keeps its write bit — the runtime needs a writable cwd to start at
        # all (F59). Their OTHER activated workspaces are still demoted: those are neither the room
        # nor the subject's own desk, and a run scoped to one meeting has no business writing them.
        active = [dict(m) if (m.get("primary") or (group and m.get("slug") == group))
                  else {**m, "write": False, "primary": False}
                  for m in active]
        if group and not any(m.get("slug") == group for m in active):
            # The meeting names a group the subject has not activated — resolve it directly through
            # the authoritative Lane-A seam so the run can maintain the group's memory. Membership
            # and the write bit are decided THERE, from policy/members.json, not here.
            g_mount = group_desk_mount(settings.workspaces_dir, subject, group)
            if g_mount is not None:
                active.append(dict(g_mount))
    # THE CWD, STATED rather than reached by elimination. The group desk takes it when the meeting
    # has one AND this subject may actually write it; otherwise the subject's own desk keeps it.
    #
    # `_worker_cwd` picks the first `primary`, else the first writable non-system mount, else the
    # baseline home. Under the old blanket demotion NOTHING was primary and nothing was writable, so
    # the cwd arrived through that last fallback — the baseline home, mounted read-only — and the
    # worker died on `mkdir`. Deciding it here means a viewer's group desk (readable, not writable)
    # can never become the cwd either, which is the same bug through a quieter door.
    if room:
        _g = str(room.get("group_workspace_id") or "")
        _gm = next((m for m in active if _g and m.get("slug") == _g and m.get("write")), None)
        if _gm is not None:
            for m in active:
                m["primary"] = m is _gm
    # THE SCAFFOLD'S ORDER + ITS ONE ADDITION (see the docstring). Fails SOFT in both halves: a
    # scaffold naming a workspace that cannot be resolved costs an ordering, never the turn.
    if scaffold_workspaces:
        wanted = [str(w).strip() for w in scaffold_workspaces
                  if str(w).strip() and str(w).strip() not in (GLOBAL_SLUG, SYSTEM_SLUG)]
        # A SCAFFOLD NAMES THE RECIPIENT'S OWN DESK BY THEIR SUBJECT ID, because at mint time
        # that is the only handle the minter has — flows knows an address and a uid, never a
        # slot name. On the store the same desk is the SEED SLOT (`workspace_attach.SEED_SLOT`,
        # resolving in place at `<root>/<subject>`), so a literal slug comparison would miss it,
        # then send it to `group_desk_mount`, which would correctly refuse a private desk and
        # log it as a missing group. Two names for one desk, resolved here, once.
        own = {str(subject), SEED_SLOT}
        own_path = f"{settings.workspaces_dir}/{subject}"

        def _is(mount: dict, want: str) -> bool:
            return mount.get("slug") == want or (want in own and mount.get("path") == own_path)

        for want in wanted:
            if any(_is(m, want) for m in active):
                continue
            if want in own:
                # Their own desk is the private baseline by construction; if it is not in the
                # active set the person switched it OFF, and a link does not switch it back on.
                continue
            try:
                extra_mount = group_desk_mount(settings.workspaces_dir, subject, want)
            except Exception:  # noqa: BLE001
                extra_mount = None
            if extra_mount is not None:
                active.append(extra_mount)

        def _rank(mount: dict) -> int:
            for i, want in enumerate(wanted):
                if _is(mount, want):
                    return i
            return len(wanted)
        active = sorted(active, key=_rank)
        logger.info("dispatch SCAFFOLD MOUNTS subject=%s wanted=%s mounted=%s",
                    subject, wanted, [m.get("slug") for m in active])
    stack.extend(active)

    # Tier 2b — THE ROOM (read-only, additive, absent unless a meeting was named and authorised
    # upstream). Fails SOFT: a room that cannot be materialized degrades the post-meeting turn's
    # context, and must never be the reason the turn does not happen.
    if room:
        try:
            taken = {m["path"] for m in stack if m.get("path")}
            extra, audit = resolve_desks(
                settings.workspaces_dir, room.get("ordered") or [],
                lookup=room.get("lookup") or (lambda _address: None),
                meeting_id=str(room.get("meeting_id") or ""),
                cap=room.get("read_max"), taken_paths=taken)
            stack.extend(extra)
            # OBSERVABILITY — a silent widening of what an agent may read is the thing that must
            # never happen. THE AUDIT LINE. One row per participant — address, subject, and WHY (matched-and-spoke
            # / unmatched-invite-order / skipped-no-subject / skipped-no-desk / skipped-over-cap).
            # This is how anyone ever answers "which desks could that run read, and why those?" —
            # a widening that cannot be reconstructed afterwards is a widening nobody can audit.
            logger.info(
                "dispatch ROOM MOUNTS subject=%s meeting=%s source=%s mounted=%s read_only=%s "
                "group_desk=%s writable_desks=%s audit=%s",
                subject, room.get("meeting_id"), room.get("source") or "-",
                [m["slug"] for m in extra], all(m.get("write") is False for m in extra),
                room.get("group_workspace_id") or "-",
                [m["slug"] for m in stack if m.get("write") and m.get("role") not in ("global", "system")],
                audit,
            )
        except Exception:  # noqa: BLE001
            logger.warning("room mount resolution failed for subject=%s meeting=%s — running without "
                           "the room", subject, (room or {}).get("meeting_id"), exc_info=True)

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
    "VEXA_LLM_EXTRA_BODY",      # server-specific request fields the OpenAI dialect cannot express
                                # (e.g. a self-hosted Qwen needs {"chat_template_kwargs":
                                # {"enable_thinking": false}} or it returns no valid JSON at all)
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
    # Reasoning-effort pin for the claude-code harness (Settings → Models "effort"). Empty ⇒ unset ⇒
    # the CLI's own default (no flag on the argv); an explicit value reaches the worker env and the
    # harness passes it through as --effort. Backends that validate the OpenAI-compatible
    # reasoning_effort field (vLLM/LiteLLM groups) reject the CLI's default high when it is outside
    # their allowlist; the pin overrides that default.
    effort = (config.get("effort") or "").strip()
    if effort:
        env["VEXA_AGENT_EFFORT"] = effort
    # THE HARNESS, per subject (PRD decision 37 + 38). `VEXA_RUNNER` was a deployment-wide dial, so
    # trying a different harness meant changing it for everybody — which is exactly what a
    # rehearsal must never do. Here it is one more field of the SAME per-subject config the model
    # and the endpoint already ride, so `openai-agent` against a local Qwen can be pinned to one
    # scratch subject while every other person on the instance keeps the deployment's default.
    #
    # An unknown name is DROPPED, never an error, and the drop is logged: identical to the model
    # allowlist above, and for the identical reason — a stale pref must not brick a turn. The known
    # set is `shared.units.RUNNERS`, which `llm/tests/test_registry.py` proves equal to the runner
    # registry itself; admin-api stores the slug with no vocabulary of its own, so there is exactly
    # one list and one test holding it to the code that implements it.
    runner = (config.get("runner") or "").strip()
    if runner and runner in units.RUNNERS:
        env["VEXA_RUNNER"] = runner
    elif runner:
        logger.warning("runner %r is not a known harness (%s) — using the deployment default",
                       runner, sorted(units.RUNNERS))
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
    extra_body = (config.get("extra_body") or "").strip()
    if extra_body:
        # Passed through verbatim; the adapter parses it and fails loudly on malformed JSON rather
        # than silently dropping a setting the deployment depends on.
        env["VEXA_LLM_EXTRA_BODY"] = extra_body


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


def _start_with_nonce(start: dict, nonce: str) -> dict:
    """`start`, with the delivery nonce on its entrypoint. A COPY — the caller's dict is untouched."""
    ep = (start or {}).get("entrypoint")
    if not nonce or not isinstance(ep, dict):
        return start
    return {**start, "entrypoint": {**ep, "nonce": nonce}}


def build_unit_env(settings: Settings, invocation: dict, *, unit_id: str, token: str,
                   memberships: Optional[list[dict]] = None,
                   model_config: Optional[dict] = None,
                   room: Optional[dict] = None,
                   scaffold_workspaces: Optional[list[str]] = None,
                   entry_nonce: str = "") -> dict[str, str]:
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
    mounts = build_mount_set(settings, subject, memberships, room=room,
                             scaffold_workspaces=scaffold_workspaces)
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
        # The entrypoint carries the same nonce as its pre-delivered stream copy, so the worker can
        # skip exactly that one entry at boot and drain everything else waiting. Copied, never
        # mutated in place: `invocation` belongs to the caller.
        "VEXA_START": json.dumps(_start_with_nonce(invocation["start"], entry_nonce)),
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
    # ── the worker's AUTHENTICATED toolbelt (shared.delegation) ──────────────────────────────────
    # The dispatch is the only place that knows BOTH who this turn acts for and why it fired, so it is
    # the only place that can mint a credential saying exactly that. The worker receives a short-lived,
    # scoped, revocable delegation token — never a durable user credential — and attaches it to the
    # vexa-control MCP. REGIME comes from the trigger: `message` is a human turn (soft scope: everything
    # already theirs, because a person is watching and can correct it); anything else fired unwatched
    # and carries the HARD isolation set — the exact workspaces this dispatch was granted.
    #
    # Fails SOFT and SILENT-BY-DESIGN: no secret or no endpoint ⇒ no token, and the worker runs exactly
    # as it did before delegation existed. A mint that RAISES, though, is a dispatcher bug (a bad regime/
    # scope combination), and it is logged rather than swallowed — an unauthenticated worker that was
    # supposed to be authenticated looks, from the chat, exactly like an MCP with nothing to say.
    mcp_secret = settings.mcp_delegation_secret.get_secret_value()
    if mcp_secret and settings.mcp_url:
        regime = delegation.regime_for_trigger(invocation["trigger"])
        # Human ⇒ "*" (soft focus over the subject's own account). Autonomous ⇒ the granted workspace
        # ids, verbatim from the invocation — the dispatch's isolation set is already the right answer.
        scope_ws = "*" if regime == "human" else [
            str(w.get("id")) for w in (invocation.get("workspaces") or []) if w.get("id")
        ]
        # THE ROOM IS DELIBERATELY ABSENT FROM THIS SCOPE, and that is the security answer, not an
        # omission. The delegation scope is a CEILING ON THE ACCOUNT (`delegation.scope_allows_workspace`:
        # `"*"` "allows everything the ACCOUNT already allows … the rig still applies its own per-uid
        # ownership checks underneath"), so naming another attendee's workspace here would be asking the
        # control MCP to hand THIS uid a workspace it does not own — inert if the rig is correct, and a
        # genuine widening of the person's account reach if it ever is not. The room is a MOUNT-level read
        # grant made by the dispatcher and enforced by the container's mount table (`write: False` → a
        # `:ro` bind), which is a narrower mechanism than a credential and needs no credential change.
        # Net effect on the token: identical bytes to a room-less dispatch — same `sub`, same `regime`,
        # same `workspaces` — asserted by tests/test_meeting_room.py.
        try:
            env["VEXA_MCP_URL"] = settings.mcp_url
            env["VEXA_MCP_DELEGATION_TOKEN"] = delegation.mint_delegation(
                mcp_secret, subject=str(subject), regime=regime, workspaces=scope_ws,
                ttl_sec=settings.mcp_delegation_ttl_sec,
            )
        except ValueError:
            env.pop("VEXA_MCP_URL", None)
            logger.exception("mcp delegation mint refused for subject=%s regime=%s — worker runs "
                             "WITHOUT the vexa MCP", subject, regime)
    # Temporal awareness (PRD decision 31 §1): the worker builds its own `now / last / next` block
    # from flows-api's read-only timeline route, so it needs that route's address and a key. Passed
    # through from THIS process's environment rather than from settings, and only when it is there:
    # a deployment that has minted no timeline key gets no block, which is the correct default —
    # `VEXA_FLOWS_API_KEY` is the OPERATOR key (it submits and activates flows), and putting it in
    # a worker container to read a list of times would widen its reach by one container per turn.
    # Mint `VEXA_FLOWS_TIMELINE_KEY` instead; `flows_api._timeline_key` accepts only that route.
    for _var in ("VEXA_FLOWS_API_URL", "VEXA_FLOWS_TIMELINE_KEY"):
        if os.environ.get(_var):
            env[_var] = os.environ[_var]
    if settings.agent_model:
        env["VEXA_AGENT_MODEL"] = settings.agent_model
    if settings.meeting_model:
        env["VEXA_MEETING_MODEL"] = settings.meeting_model
    if settings.post_meeting_dev_email:
        env["VEXA_POST_MEETING_DEV_EMAIL"] = settings.post_meeting_dev_email
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


# Distinguishes "nothing to deliver" (None) from "the delivery was attempted and failed", which
# only the caller can turn into a verdict — it depends on whether the worker is warm.
_DELIVERY_FAILED = object()


class WarmDeliveryFailed(RuntimeError):
    """A chat turn could not be handed to its worker.

    Raised rather than swallowed because for a WARM unit the pre-delivery XADD is the only delivery
    there is: returning quietly loses the person's words behind a 200. The caller turns this into an
    error the client can show and the person can retry."""


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

    def dispatch(self, invocation: dict, *, room: Optional[dict] = None,
                 scaffold_workspaces: Optional[list[str]] = None) -> str:
        """Validate + spawn. Returns the workload id. Raises on a non-conformant envelope (P18).

        ``room`` is the post-meeting MEETING ROOM — ``{meeting_id, subjects[], source}`` — already
        RESOLVED AND AUTHORISED by the caller (``api._resolve_room``). It is a dispatcher argument
        rather than a field of the invocation on purpose: ``unit.v1`` is a sealed wire contract
        (``additionalProperties: false``), and more importantly the room must never be able to
        arrive from anywhere a request body can reach. ``None`` (every other trigger and every
        chat that names no meeting) leaves the dispatch byte-identical to before.

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
        # Enumeration reconciles BOTH stores (index ∪ policy/members.json) so a dead or incomplete index
        # cannot silently drop a shared workspace from the mount set — the reconciler never raises and
        # logs the degraded leg out loud. None (no index wired) still means Lane A off.
        memberships = None
        if self._membership_index is not None:
            memberships, _ = reconciled_memberships(
                self._settings.workspaces_dir, identity["subject"], self._membership_index.list)
        # Settings → Models: resolve the subject's effective model config (fail soft — a down
        # identity service must never block a turn; the deployment env defaults still dispatch).
        # NOTE: /api/chat gates message-triggers upstream (credential preflight) — this path stays
        # ungated so async triggers (scheduled/event/transcription) never lose a dispatch; their
        # credential-less failure mode is the clean rewritten done frame (llm/errors taxonomy).
        model_config = self.resolve_model_config(identity["subject"])
        # ONE NONCE, TWO COPIES. A cold spawn receives this prompt twice — as its entrypoint (in
        # VEXA_START, serialised into the env right below) and as the pre-delivered stream entry —
        # and the worker must skip exactly the one it is about to run. Computed HERE because the env
        # is built before pre-delivery: stamping it inside _predeliver mutated a dict that had
        # already been serialised, so it reached nobody and leaked across callers.
        entry_nonce = f"{uid}:{time.time_ns()}"
        env = build_unit_env(self._settings, invocation, unit_id=uid, token=token, memberships=memberships,
                             entry_nonce=entry_nonce,
                             model_config=model_config, room=room,
                             scaffold_workspaces=scaffold_workspaces)
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
        # The mount must exist before the runtime is asked to bind it (see the helper).
        _ensure_workspace_exists(self._settings, identity["subject"])
        delivery = (self._predeliver(uid, invocation, entry_nonce)
                    if invocation["trigger"] == "message" else None)
        if delivery is _DELIVERY_FAILED:
            # The XADD failed. If the worker is GONE the spawn below re-runs this prompt as its
            # entrypoint and nothing is lost; if it is ALIVE the spawn is a touch and the person's
            # words reached nobody. Refuse loudly in that case rather than answer 200 and stream the
            # turn already running — see _predeliver for what that cost.
            if self._workload_gone(uid):
                delivery = None
            else:
                raise WarmDeliveryFailed(
                    f"unit {uid} is running and its turn could not be delivered")
        acked = self._runtime.spawn(uid, self._settings.agent_profile, env)
        if delivery is not None:
            self._watch_delivery(uid, env, tail=delivery)
        logger.info(
            "dispatch SPAWN workload=%s trigger=%s subject=%s launcher=%s warm_delivery=%s room=%s "
            "scaffold_mounts=%s",
            acked, invocation["trigger"], identity["subject"], identity["launcher"], delivery is not None,
            (room or {}).get("meeting_id") or "-", scaffold_workspaces or "-",
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

    def _predeliver(self, uid: str, invocation: dict, nonce: str) -> Optional[str]:
        """XADD the message's prompt to ``unit:<uid>:in`` with a matching nonce; returns the
        out-stream TAIL id the watchdog reads the ack from.

        Returns None ONLY when there is nothing to deliver (a session-only start has no inline
        prompt). A delivery that was attempted and FAILED raises — see below.

        ⚠ WHY THIS RAISES NOW (2026-09-02, the founder's own chat). It used to swallow every failure
        with "warm delivery must never break a dispatch — relying on the spawn path". That reasoning
        holds for a COLD unit, where the spawn really does carry the prompt as its entrypoint. It is
        false for a WARM one: `spawn` on a live unit is a touch, there is no entrypoint, and this
        XADD is the ONLY delivery the message will ever get. So a swallowed failure meant the words
        were gone while `POST /api/chat` answered 200 and streamed the turn already running — the
        person watched a reply appear and reasonably believed it was to what they had just sent.

        He sent "and share it with dmitry@vexa.ai" twice into a busy session. Both returned 200,
        neither became a turn, and nothing anywhere recorded a loss. A 200 that drops the message is
        strictly worse than an error: an error he can retry, a silent drop he cannot even see."""
        prompt = ((invocation.get("start") or {}).get("entrypoint") or {}).get("inline")
        if not prompt:
            return None  # session-only starts have no inline prompt to deliver
        r = self._redis()
        if r is None:
            # NOT a delivery failure — a topology without redis (tests, the process backend) has no
            # warm path at all, and there the spawn genuinely carries the prompt as its entrypoint.
            # The distinction is the whole correction: "there is no warm path here" is a property of
            # the deployment, "the XADD failed" is an accident on a deployment that has one. Only the
            # second can lose a person's words, and only the second raises.
            return None
        try:
            entries = r.xrevrange(output_topic(uid), count=1)
            tail = entries[0][0] if entries else "0-0"
            r.xadd(input_topic(uid), {"turn": json.dumps({"type": "message", "prompt": prompt, "nonce": nonce})})
        except Exception as exc:  # noqa: BLE001
            # WHETHER THIS LOSES THE TURN DEPENDS ON THE UNIT, so the decision is the caller's.
            # Cold unit: the spawn carries this same prompt as its entrypoint and nothing is lost —
            # which is the case in every test topology, where redis is configured but unreachable.
            # Warm unit: `spawn` is a touch, there is no entrypoint, and this XADD was the only
            # delivery the message would ever get. `dispatch` knows which, and raises there.
            logger.warning("warm pre-delivery failed for unit=%s: %s", uid, exc)
            self._warm_fail()
            return _DELIVERY_FAILED
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
