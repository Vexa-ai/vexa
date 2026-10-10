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
from control_plane import delegation_refresh, delegation_revocation, global_layer
from control_plane import model_endpoint
from control_plane import model_providers
from control_plane import unit_faults
from control_plane.meeting_room import group_desk_mount, resolve_desks
from control_plane.system_mounts import GLOBAL_SLUG, SYSTEM_SLUG, global_mount, system_mount
from shared.config import Settings
from shared import delegation
from shared.ports import IdentityPort, RuntimePort
from shared import unit_input, units
from shared.runtime_fault import RuntimeFault
from shared.units import chat_session, dispatch_id, inbox_cursor_key, input_topic, output_topic

logger = logging.getLogger("agent_api.dispatch")


def _stream_id_le(a: str, b: str) -> bool:
    """``a <= b`` for redis stream ids (``<ms>-<seq>``) — compared as numbers, never as text."""
    def parts(x: str) -> tuple[int, int]:
        ms, _, seq = str(x).partition("-")
        return int(ms or 0), int(seq or 0)
    try:
        return parts(a) <= parts(b)
    except ValueError:
        return False


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

    ⚠ ENFORCED BY THE MOUNT, NOT BY A GUARD THAT FAILS THE REPORT (Vexa-ai/vexa#1606, founder ruling
    2026-09-06). The organiser's own desk is mounted ``write: False`` on EVERY room run — group or no
    group. It used to keep the write bit, with ``process_meeting``'s HEAD-before/HEAD-after check as
    the only thing standing between decision 22 and a desk write, and that check is a DETECTOR: when
    it fires the meeting loses its minutes mail and a human has to reset a repository by hand. It
    fired twice on 2026-09-06 (meetings 147 and 150) — first the entity write-back phase, then the
    desk README refresh — and each time the report existed, was grounded, and went nowhere. A
    contract enforced by a check that costs the product its output when it holds is enforced in the
    wrong place. A read-only bind cannot be talked out of.

    THE ROOM IS THE OTHER ATTENDEES' DESKS; the organiser's desk is not one of the room mounts, it is
    simply read-only too. What is NOT reverted here is the lesson of F59, and it is a different
    statement than the one this code used to make: **the cwd the runtime is handed must be
    WRITABLE**. It used to buy that by leaving the desk writable, and the spawn died without it
    (``OSError: [Errno 30] Read-only file system: '/workspaces/129/.claude'`` — every post-meeting
    turn for every non-admin subject, invisible because the instance's only admin is the founder).
    The cwd now comes from the group desk when the subject may write it, and otherwise from the
    ``_system`` tier, which is writable by contract and is not a desk (``_worker_cwd``'s last
    fallback). F59's property is asserted as itself — the cwd is writable — instead of through a
    proxy that also grants a content desk nobody wanted written.

    So in room mode: the ROOM entries are ``write: False`` (they always were, where they are appended
    below), the subject's OWN desk is ``write: False`` as well, and the subject's OTHER activated
    workspaces are demoted with it — none of them is the room, and a run scoped to one meeting has no
    business writing any of them. The GROUP DESK, when the meeting is bound to a shared workspace and
    the subject is a contributor/owner, keeps its write bit AND becomes the turn's cwd (``primary``),
    because that desk is the room's shared state and decision 22's group half says the run maintains
    it. ``_system`` is NOT a desk and stays read-write — chat continuity anchors there
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
    above, and it is a different argument on purpose so the two can never be confused.

    ── AND THE CHAT'S TARGET IS NOT HERE (Vexa-ai/vexa#1611), deliberately ─────────────────────────
    A chat's target workspace — the one its writes go to — changes the turn's CWD and nothing about
    this stack: every workspace in the focus is mounted either way, with the role and the write bit
    membership already decided. It is applied in ``_worker_cwd``, which is the one thing the cwd is
    a function of.

    It was written here first, as ``primary`` on the target's mount, and that was WRONG in a way
    that would have looked right: ``primary`` already means *"which of these is the person's own
    desk"* — ``worker/engine.desk_mounts`` reads it to decide whose README the end-of-turn refresh
    maintains, and ``_tier_label`` renders it to the model as *"your DESK — your private baseline,
    durable personal memory"*. Pointing it at a customer's shared workspace would have told the
    agent that workspace was the person's private desk and moved the desk README onto it. One field,
    one meaning."""
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

    # Tier 2 — the NORMAL active set (private baseline + activated extras). In ROOM mode EVERY desk
    # the subject owns is demoted to read-only — their own included; the meeting's group desk is the
    # one exception (see DECISION 22 in the docstring — the room's own entries are appended below
    # already read-only).
    if room:
        group = str(room.get("group_workspace_id") or "")
        # DECISION 22, IN THE MOUNT TABLE. A room run writes ONE artefact and its home is the meeting
        # row; `drop_to_attendees` is the one writer of desks. So no desk this subject owns is
        # writable on this run — not the one they are activated on, not the one they happen to be
        # cwd'd into. This used to keep the organiser's own desk writable "because F59 needs a
        # writable cwd", and two different end-of-turn writers took that as permission: the entity
        # write-back phase (decision 24) on meeting 147, and the desk README refresh (decision 26.4)
        # on meeting 150 — three commits reading `175: README.md — updated`. Both moved HEAD, both
        # tripped `process_meeting`'s detector, and both times a grounded report was thrown away.
        # The cwd is bought elsewhere now (`_worker_cwd`'s `_system` fallback), so the write bit here
        # buys nothing and costs exactly that.
        if group and not any(m.get("slug") == group for m in active):
            # The meeting names a group the subject has not activated — resolve it directly through
            # the authoritative Lane-A seam so the run can maintain the group's memory. Membership
            # and the write bit are decided THERE, from policy/members.json, not here.
            g_mount = group_desk_mount(settings.workspaces_dir, subject, group)
            if g_mount is not None:
                active.append(dict(g_mount))
        active = [
            dict(m) if (group and m.get("slug") == group)
            else {**m, "write": False, "primary": False}
            for m in active]
    # THE CWD, STATED rather than reached by elimination. The group desk takes it when the meeting
    # has one AND this subject may actually write it; otherwise nothing here is primary and
    # `_worker_cwd` lands on the writable `_system` tier.
    #
    # `_worker_cwd` picks the first `primary`, else the first writable non-system mount, else the
    # writable `_system` tier, else the baseline home. The baseline home is the fallback that killed
    # every non-admin post-meeting spawn (F59) — it is a PATH, not a mount, so it was handed over
    # read-only and the worker died on `mkdir`. `_system` sits ahead of it now precisely so a run
    # with no writable desk still has a writable cwd, which is F59's actual property.
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
# brokers creds itself (the HOST_CLAUDE_CREDENTIALS bind-mount + copying ANTHROPIC_* from
# the runtime service env), but the k8s and process backends deliver ONLY this spec env — so a helm
# worker booted with no credential at all (claude CLI: "Not logged in" → chat "Model inference
# error"). agent-api therefore stamps an EXPLICIT allowlist from its own environment into every
# dispatch, making credential delivery uniform across backends. Never blanket-forward env (P14/P15):
# each entry is a var a core/agent/llm adapter (or the claude CLI itself) actually reads.
MODEL_AUTH_ENV_ALLOWLIST = (
    "CLAUDE_CODE_OAUTH_TOKEN",  # claude CLI subscription OAuth — the env twin of the docker credentials mount
    "ANTHROPIC_API_KEY",        # claude CLI (last-resort fallback)
    "ANTHROPIC_AUTH_TOKEN",     # claude CLI gateway/OpenRouter token
    "ANTHROPIC_BASE_URL",       # claude CLI gateway endpoint
)
# The list used to carry three more — VEXA_LLM_API_KEY / _BASE_URL / _EXTRA_BODY — the completion
# provider's credential, endpoint and dialect escape hatch. PRD decision 34 removed the pipeline
# that called it, so a worker needs exactly one model credential: the agent harness's.


# ── a subject's own endpoint pins the WHOLE model route, here and nowhere else ───────────────
# A worker's model env has TWO writers. The dispatch stamps what it decided; then the runtime fills
# every key the spec left ABSENT from the deployment's own environment
# (`runtime_kernel.workload_env.WORKER_FORWARD_ENV`, `forwarded_env`), the VEXA_LLM_* keys among
# them. The openai-agent harness reads VEXA_LLM_* BEFORE ANTHROPIC_*, so a subject's endpoint
# stamped under the ANTHROPIC_* names alone lost to a forwarded deployment VEXA_LLM_BASE_URL: the
# turn ran on the deployment's model, and with no deployment VEXA_LLM_API_KEY it carried the
# SUBJECT's key to the DEPLOYMENT's endpoint (F84 from the other side).
#
# So the precedence is decided ONCE, by the dispatch: when a subject's endpoint applies, every key
# either harness reads to choose an endpoint, a credential, a model override or a request dialect
# is stamped by `subject_route_env`, the empty string included. The runtime never refills a key the
# spec carries, so nothing of the deployment's route survives into that worker, and neither harness
# has to know whose value it is reading. Two names for the subject's one endpoint is not the second
# endpoint decision 34 removed: that was a second CONSUMER, configured separately.
def subject_route_env(base_url: str, api_key: str, extra_body: str) -> dict[str, str]:
    """The model route of a worker whose subject's own endpoint applies: every key, every time.

    ``api_key`` and ``extra_body`` are the subject's own, and empty means NONE — never "use the
    deployment's". The model is not here: it is ``VEXA_AGENT_MODEL``, resolved once by
    ``overlay_model_config`` under the operator's allowlist, so the openai-agent override
    ``VEXA_LLM_MODEL`` is pinned empty rather than left for the deployment's value to fill."""
    return {
        # the claude CLI (the claude-code harness)
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CODE_OAUTH_TOKEN": "",     # the subscription token has ONE legitimate destination
        # the openai-agent harness, which reads these before the ANTHROPIC_* names
        "VEXA_LLM_BASE_URL": base_url,
        "VEXA_LLM_API_KEY": api_key,
        "VEXA_LLM_MODEL": "",
        # Qwen behind vLLM needs {"chat_template_kwargs":{"enable_thinking":false}} or it returns
        # nothing parseable; another endpoint may refuse those fields. Only the subject knows which.
        "VEXA_LLM_EXTRA_BODY": extra_body,
        # THE ROUTE, STATED TO THE WORKER: the endpoint is the person's own. A harness whose CLI keeps
        # a credential in its config directory clears it for this worker (llm/claude_code.py), so
        # the only credential on this route is the one stamped above.
        "VEXA_MODEL_ROUTE": "subject",
    }


def _allowlisted(model: str, allowlist: str) -> bool:
    """The operator's model gate (``VEXA_MODEL_ALLOWLIST``, comma-separated): empty = anything goes."""
    allowed = {m.strip() for m in allowlist.split(",") if m.strip()}
    return not allowed or model in allowed


def overlay_model_config(env: dict[str, str], config: dict, *, allowlist: str = "",
                         friction=None, subject: str = "", session: str = "") -> None:
    """Overlay the subject's effective model config (Settings → Models: user pref > platform
    setting, resolved by admin-api) onto the dispatch env — field-by-field over the deployment
    env defaults, which stay the bottom fallback for anything unset.

    ``mode: custom`` points the agent harness at the supplied gateway (e.g. LiteLLM/OpenRouter in
    front of an open-source model, or a self-hosted vLLM) once it passes the operator gate: the
    whole model route is stamped by ``subject_route_env``, under the names each harness reads, so
    the subject's endpoint, key and extra_body apply whatever the deployment's VEXA_LLM_* say.
    ``mode: subscription`` (or unset) keeps the deployment's brokered credential — the mounted
    Claude Code subscription / deployment key — and only the model names apply.

    Dispatch-stamped values WIN downstream (the runtime copies its own env only for keys absent
    here — ``runtime_kernel.workload_env.forwarded_env``). Models are gated by the operator's
    allowlist: a non-allowlisted model is DROPPED (deployment default applies), never an error —
    a stale pref must not brick a turn."""
    model = (config.get("model") or "").strip()
    if model and _allowlisted(model, allowlist):
        env["VEXA_AGENT_MODEL"] = model     # harness turns — the ONE model this product runs
    elif model:
        logger.warning("model %r not in VEXA_MODEL_ALLOWLIST — using deployment default", model)
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
    base_url = model_endpoint.custom_base_url(config)
    api_key = (config.get("api_key") or "").strip()
    if not base_url:
        # Not custom, or custom with no endpoint — inert either way; deployment credentials apply.
        return
    # THE OPERATOR GATE (F84). A subject-supplied URL is an outbound destination chosen by a
    # non-operator, so it is refused unless the deployment allow-lists its host — and on a harness
    # that signs in from its config directory when it has no key, it is refused without the
    # subject's own key. The refusal is LOUD — a log line and a friction record — because a
    # silently-ignored endpoint runs the turn on the deployment's own model and looks like it worked.
    refusal = model_endpoint.route_refusal(
        base_url, api_key, env.get("VEXA_RUNNER") or units.deployment_runner())
    if refusal:
        logger.warning("model endpoint REFUSED for subject=%s: %s", subject or "?", refusal)
        if friction is not None:
            try:
                friction(model_endpoint.refusal_friction(base_url, refusal, subject=subject,
                                                        session=session))
            except Exception:  # noqa: BLE001 — a report is never worth a dispatch
                logger.warning("model endpoint refusal could not be filed as friction")
        return
    # ALWAYS THE SUBJECT'S OWN CREDENTIAL — the empty string included (F84, SECURITY). Two writers
    # fill a key this leaves ABSENT: the backfill at the end of `build_unit_env` (from agent-api's
    # own environment) and the runtime's forward list (from the deployment's). An explicit "" is not
    # absent to either. Stamping only a non-empty key therefore paired the DEPLOYMENT's token with
    # the SUBJECT's endpoint, and stamping only the claude CLI's names paired the SUBJECT's token
    # with the DEPLOYMENT's openai-agent endpoint. The whole route is pinned in one call, so a
    # custom endpoint receives only what its owner set, and the owner's key goes nowhere else.
    env.update(subject_route_env(base_url, api_key, (config.get("extra_body") or "").strip()))


# ── a catalog route: the operator's model, through its provider's adapter ────────────────────
# With a model catalog declared (VEXA_MODEL_CATALOG), the person's Settings → Models no longer picks
# the model: their chat does, from the catalog, and `model_providers` resolves that pick into a
# `ModelRoute`. These two functions are where a route becomes a worker's environment, beside
# `subject_route_env` and for the same reason: the dispatch is the one writer of a worker's model
# route, and every key either harness reads is stamped, the empty string included, so nothing of
# the deployment's route survives into a worker whose route came from the catalog.

#: The claude CLI's model tiers. A route to a gateway pins every tier to the model the person
#: chose, so no background call asks that gateway for a model id it does not serve; a route to
#: Anthropic itself (or a harness that never reads them) pins them empty: the CLI's own defaults.
_CLAUDE_TIERS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                 "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")


def route_env(route: "model_providers.ModelRoute") -> dict[str, str]:
    """A catalog route as the worker env — every key, every time.

    The credential goes to the route's own endpoint under the one name the endpoint expects (a
    bearer token, or Anthropic's API-key header), every other credential name is stamped empty, and
    the deployment's subscription token is stamped empty unless the route IS that subscription — in
    which case the endpoint is stamped empty too, so the CLI sends it to Anthropic and nowhere else.
    A ``custom`` route is the person's own endpoint and is stamped by ``subject_route_env``,
    unchanged."""
    if route.credential_source == model_providers.CRED_SUBJECT:
        env = subject_route_env(route.base_url, route.credential, route.extra_body)
    else:
        key = route.credential if route.credential_source == model_providers.CRED_SECRET else ""
        bearer = key if route.auth_header == "bearer" else ""
        env = {
            "ANTHROPIC_BASE_URL": route.base_url,
            "ANTHROPIC_AUTH_TOKEN": bearer,
            "ANTHROPIC_API_KEY": key if route.auth_header == "x-api-key" else "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "VEXA_LLM_BASE_URL": route.base_url,
            "VEXA_LLM_API_KEY": bearer,
            "VEXA_LLM_MODEL": "",
            "VEXA_LLM_EXTRA_BODY": route.extra_body,
        }
        if route.credential_source == model_providers.CRED_SUBSCRIPTION:
            # The backfill below (MODEL_AUTH_ENV_ALLOWLIST) brings the deployment's subscription
            # token, and the runtime brokers its credential file — both only ever to Anthropic,
            # because this route's endpoint is stamped empty and the CLI's default is Anthropic.
            del env["CLAUDE_CODE_OAUTH_TOKEN"]
    gateway = (route.harness == "claude-code" and bool(route.base_url)
               and model_endpoint.host_of(route.base_url) != "api.anthropic.com")
    tier = route.provider_model if gateway else ""
    env.update({k: tier for k in _CLAUDE_TIERS})
    env["VEXA_RUNNER"] = route.harness
    if route.provider_model:
        env["VEXA_AGENT_MODEL"] = route.provider_model
    env["VEXA_AGENT_STREAM"] = "1" if route.capabilities.streaming else "0"
    env["VEXA_AGENT_CONTEXT_TOKENS"] = (str(route.capabilities.context_tokens)
                                        if route.capabilities.context_tokens else "")
    # THE EFFORT, stamped every time, empty included: the claude CLI's --effort for this route (a
    # level the adapter has checked), or nothing — never a level left over from the deployment or
    # from Settings → Models riding into a model that did not offer it. On openai-agent the
    # adapter has already written the level into VEXA_LLM_EXTRA_BODY in its provider's own field.
    env["VEXA_AGENT_EFFORT"] = route.effort if route.harness == "claude-code" else ""
    # THE ENTRY'S OUTPUT CAP, when it names one. Absent, the deployment's own
    # VEXA_AGENT_MAX_OUTPUT_TOKENS (forwarded by the runtime) applies, or the harness's default.
    if route.max_output_tokens:
        env["VEXA_AGENT_MAX_OUTPUT_TOKENS"] = str(route.max_output_tokens)
    return env


def route_context(model_config: Optional[dict], *, allowlist: str = "",
                  deployment_runner: str = "", deployment_model: str = "",
                  env: Optional[dict] = None) -> "model_providers.RouteContext":
    """What a provider adapter may consult for one person: their effective Settings → Models
    config, agent-api's environment for ``secret_ref`` values (read now, never cached), the operator
    gates, and the deployment's own harness and model. One constructor, so the dispatch, the
    catalog route and the Test button resolve a model against the same facts."""
    return model_providers.RouteContext(
        subject_config=dict(model_config or {}),
        secret=model_providers.secret_from_env(os.environ if env is None else env),
        endpoint_refusal=model_endpoint.route_refusal,
        model_allowed=lambda m: _allowlisted(m, allowlist),
        deployment_runner=deployment_runner or units.deployment_runner(),
        deployment_model=deployment_model)


def apply_model_route(env: dict[str, str], model_config: Optional[dict], *,
                      catalog: Optional["model_providers.Catalog"] = None, choice: str = "",
                      admin=False, allowlist: str = "", friction=None, subject: str = "",
                      session: str = "", effort: str = "") -> Optional["model_providers.ModelRoute"]:
    """THE ONE PLACE A WORKER'S MODEL ROUTE IS DECIDED, with or without a catalog.

    No catalog: the person's Settings → Models overlays the deployment env exactly as it always has
    (``overlay_model_config``), and nothing is returned.

    A catalog: ``choice`` (the chat's own pick, ``""`` for none) resolves through the provider port —
    the chat's pick, else the person's default, else the catalog's — and the route is stamped whole
    (``route_env``). A pick that cannot run raises ``ModelChoiceFault`` BEFORE anything is spawned:
    the caller refuses the turn out loud rather than run it on a model the person did not choose. A
    refused own endpoint also files the friction record a refused endpoint has always filed."""
    cfg = model_config or {}
    if catalog is None or catalog.empty:
        if cfg:
            overlay_model_config(env, cfg, allowlist=allowlist, friction=friction,
                                 subject=subject, session=session)
        return None
    ctx = route_context(cfg, allowlist=allowlist, deployment_runner=env.get("VEXA_RUNNER", ""),
                        deployment_model=env.get("VEXA_AGENT_MODEL", ""))
    try:
        route = catalog.route(choice, ctx, admin=admin, effort=effort)
    except model_providers.ModelChoiceFault as fault:
        logger.warning(json.dumps({"event": "model_choice_refused", "source": fault.source,
                                   "kind": fault.kind, "model": fault.model,
                                   "provider": fault.provider, "subject": subject}))
        if fault.kind == model_providers.ENDPOINT_REFUSED and friction is not None:
            base_url = model_endpoint.custom_base_url(cfg)
            try:
                friction(model_endpoint.refusal_friction(
                    base_url, model_endpoint.refuse_reason(base_url) or fault.detail,
                    subject=subject, session=session))
            except Exception:  # noqa: BLE001 — a report is never worth a dispatch
                logger.warning("model endpoint refusal could not be filed as friction")
        raise
    env.update(route_env(route))
    logger.info(json.dumps({"event": "model_route", "model": route.model_id,
                            "provider": route.provider, "adapter": route.adapter,
                            "harness": route.harness, "credential": route.credential_source,
                            "effort": route.effort, "subject": subject}))
    return route


def _worker_cwd(root: str, subject: str, mounts: list[dict], target: str = "") -> str:
    """The worker's CWD — the workspace it 'lives in', whose ``CLAUDE.md`` auto-loads as project memory.

    ⚠ THE CHAT'S TARGET COMES FIRST (Vexa-ai/vexa#1611). It is where this conversation writes, so it
    is where a plain ``Write`` with no absolute path has to land — the founder's *"it creates files
    in the wrong workspace, we need so that the thing knew the workspace of writing, if it's
    specified"*. Only when it is actually a WRITABLE mount: naming a slug is not a grant, and a
    read-only cwd is F59 all over again. Never on a room run — ``build_unit_env`` passes no target
    there, because decision 22 already decides that run's cwd and a chat's target must not be able
    to talk a room run into writing a desk.

    ⚠ AND FOR THE ADMIN, ``_global`` IS ONE OF THOSE WORKSPACES (Vexa-ai/vexa#1616). Founder,
    2026-09-06 15:20Z: *"as admin i should just have global as option to choose here as workspace to
    write to"*. A workspace you may CHOOSE to write to is one an unqualified ``Write`` lands in.
    #1611's rule refused ``global`` here, so a chat aimed at the company layer moved the prompt's
    sentence and the tools' default while the file still appeared on the desk — the same split
    #1611 existed to close, one tier up, and the harder half of it: a turn TOLD it is writing to the
    organisation has no reason left to qualify its paths. So the target clause refuses ``system``
    and no longer refuses ``global``, and the ``write`` bit carries the rest.

    That is NOT a weaker test than the route's, and this is why the bit is enough on its own:
    ``build_mount_set`` sets the global mount read-write from ``global_layer.is_admin`` — the very
    call ``POST /api/chat/target`` makes before it will store ``_global`` as a target at all. One
    question, asked once, arriving here as a mount-table fact instead of as a second lookup that
    could answer differently. A non-admin's global mount is read-only, so it cannot become a cwd
    even if a target reached here by some other road: two locks on one door, and this is the inner.

    ``_system`` stays refused for EVERYBODY, the admin included. It is chats, sessions, settings and
    identity, it is writable BY CONTRACT rather than by anyone's decision, and nobody aims a
    conversation at it. THE FALLBACKS BELOW STILL EXCLUDE ``global`` AS WELL — a target is a choice
    somebody made, a fallback is not, and drifting a turn into the organisation tier because no desk
    happened to be writable is precisely the accident this thin tier must not have.

    It is applied HERE rather than as ``primary`` on the mount, and that distinction is load-bearing:
    ``primary`` means *"the person's own desk"* to ``engine.desk_mounts`` and to ``_tier_label``,
    which renders it as *"your DESK — your private baseline"*. See ``build_mount_set``'s last
    section for what stamping it would have done.

    Otherwise the private baseline (the primary mount). But the baseline can be switched OFF, in which case
    it is absent from the mount set — the cwd must then FOLLOW the active set (the first NORMAL writable
    workspace, never a system tier), not stay pinned to the disconnected baseline home (which would make the
    agent describe/read a workspace the user turned off).

    ⚠ THE LAST TWO STEPS ARE ORDERED THE WAY THEY ARE BECAUSE OF F59. The final fallback used to be
    the baseline HOME — a composed path, not a mount — so a dispatch in which no desk is writable was
    handed a cwd it could not write, and the worker died on ``<cwd>/.claude`` before the model ran.
    A room run is exactly that dispatch now (Vexa-ai/vexa#1606: every desk the subject owns is
    read-only), so ``_system`` — always mounted, read-write by contract, and NOT a desk — is tried
    first. F59's property is "the cwd the runtime is handed is writable"; this is where that is
    bought. The baseline home stays as the degenerate last resort (a dispatch with no mounts at all),
    where it is the only answer there is."""
    want = str(target or "").strip()
    if want:
        # `system` ONLY — see the docstring. `global` is admissible for whoever holds it READ-WRITE,
        # which is the instance admin and nobody else (`build_mount_set`, via `global_layer.is_admin`,
        # the same call the target route makes). The `write` bit IS the admin test here.
        aimed = next((m for m in mounts
                      if m.get("slug") == want and m.get("write")
                      and m.get("role") != "system" and m.get("path")), None)
        if aimed is not None:
            return aimed["path"]
        logger.info("dispatch TARGET %s is not a writable mount for subject=%s — the cwd stays "
                    "where it was", want, subject)
    primary = next((m for m in mounts if m.get("primary") and m.get("path")), None)
    if primary:
        return primary["path"]
    normal = next((m for m in mounts
                   if m.get("write") and m.get("role") not in ("global", "system") and m.get("path")), None)
    if normal:
        return normal["path"]
    system = next((m for m in mounts
                   if m.get("role") == "system" and m.get("write") and m.get("path")), None)
    return system["path"] if system else f"{root}/{subject}"


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
                   target: str = "",
                   entry_nonce: str = "", friction=None,
                   catalog: Optional["model_providers.Catalog"] = None,
                   model_choice: str = "", admin=False, effort_choice: str = "") -> dict[str, str]:
    """Map a ``unit.v1`` dispatch to the worker's ``runtime.v1`` env (12-factor, P7). The minted token +
    the workspace LIST + the per-dispatch Stream topics travel here; the runtime injects them opaquely."""
    identity = invocation["identity"]
    subject = identity["subject"]
    # The dispatch's personal (rw) workspace folder is mounted at <root>/<subject>; the Runtime binds the
    # backing store at <root> from its OWN configuration (it drops any VEXA_WORKSPACE_MOUNT_* key a spec
    # carries), and the worker works in the subject subdir.
    root = settings.workspaces_dir
    # The ORDERED mount set (WP-A1.1 + WP-A2.1): the private baseline first, then every activated extra.
    # The whole store root is already bound by the runtime, so this is a WORKER-FACING contract (the paths
    # + roles the turn respects), not a per-mount bind — it generalizes uniformly across all three backends.
    mounts = build_mount_set(settings, subject, memberships, room=room,
                             scaffold_workspaces=scaffold_workspaces)
    # THE CHAT'S TARGET, AND NEVER ON A ROOM RUN (Vexa-ai/vexa#1611). Decision 22 already decides a
    # room run's cwd — the group desk when the subject may write it, the `_system` tier otherwise —
    # and a chat's target must never be able to talk that run into writing a desk. Dropped HERE, in
    # one place, rather than inside `_worker_cwd`, which is a function of its arguments.
    cwd_target = "" if room else str(target or "").strip()
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
        "VEXA_WORKSPACE_PATH": _worker_cwd(root, subject, mounts, cwd_target),  # the worker's cwd — the chat's target, else the primary baseline, or (if it's switched off) the first active normal workspace
        "VEXA_MOUNTS": json.dumps(mounts),                       # the ordered active mount set [{slug,path,role,write,primary}]
        "VEXA_WORKSPACE_STORE_URL": settings.workspace_store_url,
        "REDIS_URL": settings.redis_url,
    }
    # THE ROOM, STATED TO THE WORKER. Present exactly when this dispatch is a post-meeting room
    # run, absent on every other dispatch — a POSITIVE signal we always emit, never an inference
    # from the mount shape. The worker cannot derive it: a room whose other attendees have no desks
    # yet resolves to zero `role: "room"` mounts, which is precisely the small-team case, so
    # "are there room mounts?" answers `no` on a run that IS one.
    #
    # It is read for two things the room run must not do, both of them decision 22 ("the run reads
    # desks and writes ONE shared artefact whose home is the meeting row"):
    #   * no end-of-turn bookkeeping runs on the organiser's desk — not the entity write-back phase
    #     (F103) and not the README refresh (Vexa-ai/vexa#1606). The MOUNT is what makes that true
    #     (`build_mount_set` demotes every desk the subject owns); this stamp is what lets the worker
    #     say so in its own words instead of inferring it from a mount shape that cannot answer;
    #   * the bot verbs leave the toolbelt (a turn about a meeting that is over cannot usefully
    #     stop a bot, and it filed four `bot_stop` calls trying — F104).
    if room and room.get("meeting_id"):
        env["VEXA_ROOM_MEETING"] = str(room["meeting_id"])
    # THE TARGET WORKSPACE, STATED TO THE WORKER (Vexa-ai/vexa#1611). Present exactly when this
    # chat has one and absent when it writes to the person's own desk — a positive signal, never an
    # inference from which mount happens to be primary, because "primary" also answers for a room
    # run's group desk and for a subject whose baseline is simply first.
    if str(target or "").strip():
        env["VEXA_TARGET_WORKSPACE"] = str(target).strip()
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
    if not (mcp_secret and settings.mcp_url):
        logger.warning("worker toolbelt not configured (worker_toolbelt: %s) — subject=%s's worker "
                       "runs without vexa tools", ", ".join(n for n, v in (
                           ("no endpoint, VEXA_MCP_URL unset", settings.mcp_url),
                           ("no signing key, VEXA_MCP_DELEGATION_SECRET unset", mcp_secret)) if not v),
                       subject)
    if mcp_secret and settings.mcp_url:
        regime = delegation.regime_for_trigger(invocation["trigger"])
        # Human ⇒ "*" (soft focus over the subject's own account). Autonomous ⇒ the granted workspace
        # ids, verbatim from the invocation — the dispatch's isolation set is already the right answer.
        scope_ws = "*" if regime == "human" else [
            str(w.get("id")) for w in (invocation.get("workspaces") or []) if w.get("id")
        ]
        # THE ROOM IS DELIBERATELY ABSENT FROM THIS SCOPE, and that is the security answer, not an
        # omission. The delegation scope is a CEILING ON THE ACCOUNT, never a grant: `"*"` allows what
        # the account already allows, and every workspace route behind the MCP checks the subject's own
        # access to the workspace it names (membership) as well as the ceiling
        # (`api_shared.require_in_ceiling`). Naming another attendee's workspace here would therefore be
        # asking for a workspace this uid does not own — refused by that access check, and a genuine
        # widening of the person's account reach if it ever failed. The room is a MOUNT-level read
        # grant made by the dispatcher and enforced by the container's mount table (`write: False` → a
        # `:ro` bind), which is a narrower mechanism than a credential and needs no credential change.
        # Net effect on the token: identical bytes to a room-less dispatch — same `sub`, same `regime`,
        # same `workspaces` — asserted by tests/test_meeting_room.py.
        try:
            env["VEXA_MCP_URL"] = settings.mcp_url
            # THE TARGET RIDES THE TOKEN (Vexa-ai/vexa#1611) — which is how `entity_upsert` and
            # `workspace_write` with no `slug` land in the workspace this chat is working in rather
            # than on the person's desk. It is a DEFAULT, not a grant: the token's `scope` is still
            # the ceiling, and each route authorizes the resolved workspace against the subject's own
            # access exactly as it would one the caller had named (`api_shared.write_slug`). On the
            # token rather than in a tool argument because the model must not have to remember it —
            # the founder's answer to *"how to softly reinforce that?"* was context, not a rule
            # somebody repeats.
            env["VEXA_MCP_DELEGATION_TOKEN"] = delegation.mint_delegation(
                mcp_secret, subject=str(subject), regime=regime, workspaces=scope_ws,
                ttl_sec=settings.delegation_ttl_sec(), target=str(target or "").strip(),
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
    # The workspace template this deployment seeds from. The worker resolves its platform skills (and
    # any workspace it seeds itself) from it, against its own seeds root (shared.seeding).
    if (settings.default_template or "").strip():
        env["VEXA_DEFAULT_TEMPLATE"] = settings.default_template.strip()
    if settings.agent_model:
        env["VEXA_AGENT_MODEL"] = settings.agent_model
    # The chat-continuation dials the openai-agent harness reads (shared/config.py). Always stamped,
    # so a worker runs on the deployment's declared value rather than on a default of its own.
    env["VEXA_AGENT_AUTO_CONTINUE_CHAT"] = "1" if settings.agent_auto_continue_chat else "0"
    env["VEXA_AGENT_MAX_CHAT_CONTINUATIONS"] = str(settings.agent_max_chat_continuations)
    # The optional operator model gate.
    if settings.model_allowlist:
        env["VEXA_MODEL_ALLOWLIST"] = settings.model_allowlist
    # THE MODEL ROUTE, decided once (`apply_model_route`): the chat's pick from the operator's
    # catalog when one is declared, else Settings → Models (per-user/platform config from admin-api)
    # over the deployment env defaults stamped above, field-by-field. A pick that cannot run raises
    # here, before anything is spawned.
    # #1510: the friction carrier requires a session on every report; `chat_session` already
    # defaults to "main" for a non-message trigger, so this is never empty.
    apply_model_route(env, model_config, catalog=catalog, choice=model_choice, admin=admin,
                      allowlist=settings.model_allowlist, friction=friction, subject=subject,
                      session=chat_session(invocation), effort=effort_choice)
    # The chat conversation thread (default "main") — the worker namespaces its continuity session file
    # by this so multiple threads coexist in the one user workspace. Meeting/digest paths ignore it.
    if invocation["trigger"] == "message":
        env["VEXA_CHAT_SESSION"] = chat_session(invocation)
        # The warm-serve window: how long the worker keeps serving unit:<id>:in after its last turn.
        # The engine's own default is a tight 120s; chat stamps the (longer) configured window so a
        # follow-up message lands on the WARM worker (no container/CLI cold start).
        env["VEXA_IDLE_TIMEOUT_SEC"] = str(settings.chat_idle_timeout_sec)
    # Chat GROUNDED in a live meeting (cookbook #1): the meeting-scoped tool needs the native id +
    # platform to target meetings' published /transcripts.
    #
    # A `meeting` context carrying a `meeting_id` used to take a FIRST branch here and build a whole
    # second dispatch shape: VEXA_TRANSCRIPT_STREAM + the meeting facts, for a worker that tailed the
    # transcript and ran completion beats over it. PRD decision 34 removed that worker, and
    # transcription_watcher no longer mints the dispatch, so the branch had no producer and no
    # consumer left.
    ctx = invocation.get("context") or {}
    meeting = ctx.get("meeting") if ctx.get("kind") == "meeting" else None
    if meeting and meeting.get("native_id"):
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

# The same rule one level up: agent-api routing hints that ride on ``context`` and are NOT part of
# the sealed Context (additionalProperties: false). ``session`` is the chat thread; ``mount_gen`` is
# the chat's focus generation (Vexa-ai/vexa#1603) — both are read off the in-memory dispatch by
# ``dispatch_id`` / ``build_unit_env`` and both are stripped before the contract check.
_INTERNAL_CTX_HINTS = frozenset({"session", "mount_gen"})


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
        bool(_INTERNAL_CTX_HINTS & ctx_dict.keys())
        or (isinstance(meeting, dict) and bool(_INTERNAL_MEETING_HINTS & meeting.keys()))
    ))
    if not needs_clean:
        return invocation
    clean = dict(invocation)
    if has_principal:
        clean["identity"] = {k: v for k, v in identity.items() if k != "principal"}
    if ctx_dict is not None:
        clean_ctx = {k: v for k, v in ctx_dict.items() if k not in _INTERNAL_CTX_HINTS}
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
                 membership_index=None, model_config=None, warm_stream=None,
                 workload_redis=None, delegation_store=None, catalog=None) -> None:
        self._settings = settings
        self._runtime = runtime
        self._identity = identity
        # Where a minted delegation token is recorded against its unit, so it is revoked when the
        # unit ends (control_plane.delegation_revocation). Production wires the service Redis; None
        # (tests, the in-process harness) records nothing.
        self._delegation_store = delegation_store
        # The Redis connection that defines each worker's own Redis user (control_plane.
        # workload_redis). Production wires it unless the deployment chose REDIS_WORKLOAD_ACL=shared;
        # None hands the worker the service URL (the in-process harness, and that explicit choice).
        self._workload_redis = workload_redis
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
        # The operator's model catalog (`model_providers`, VEXA_MODEL_CATALOG), parsed and refused
        # whole at boot. None or empty → no catalog: Settings → Models routes every turn as before.
        self._catalog = catalog if catalog is not None else model_providers.Catalog(None)
        # PRD decision 33: where a REFUSED model endpoint is filed (F84). A callable taking the raw
        # friction record — `create_app` attaches the store's `file` once it has built it. None (a
        # test, a dispatcher built without an app) logs the refusal and files nothing; the refusal
        # itself never depends on the sink.
        self._friction = None
        self.dispatched: list[dict] = []  # observability — the dispatches that fired

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def catalog(self) -> "model_providers.Catalog":
        return self._catalog

    def is_admin(self, subject: str) -> bool:
        """The instance-admin check an admins-only catalog entry needs — the same one the global
        layer uses, asked only when an entry is actually restricted."""
        return bool(global_layer.is_admin(self._settings, str(subject)))

    def attach_friction(self, file_record) -> None:
        """Wire the friction sink after construction — ``create_app`` builds it (#1510:
        ``control_plane.publish.publish_friction``, no longer a store's ``.file``) and already
        holds the dispatcher."""
        self._friction = file_record

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
                 scaffold_workspaces: Optional[list[str]] = None,
                 target: str = "", inbox: Optional[dict] = None, model: str = "",
                 effort: str = "") -> str:
        """Validate + spawn. Returns the workload id. Raises on a non-conformant envelope (P18).

        ``inbox`` — WHAT A QUEUED ROW SHOWS (Vexa-ai/vexa#1610). The pre-delivered stream entry IS
        the chat's inbox record, so the few facts a person needs to read while it waits (their own
        words, the act's kind and target, when they submitted it, the id their client gave it) ride
        on the entry rather than in a second store that could disagree with it. The worker reads
        `type`, `prompt` and `nonce` and ignores this, so an older worker is unaffected.

        ``room`` is the post-meeting MEETING ROOM — ``{meeting_id, subjects[], source}`` — already
        RESOLVED AND AUTHORISED by the caller (``api._resolve_room``). It is a dispatcher argument
        rather than a field of the invocation on purpose: ``unit.v1`` is a sealed wire contract
        (``additionalProperties: false``), and more importantly the room must never be able to
        arrive from anywhere a request body can reach. ``None`` (every other trigger and every
        chat that names no meeting) leaves the dispatch byte-identical to before.

        ``target`` is the chat's TARGET WORKSPACE (Vexa-ai/vexa#1611) — where this conversation's
        writes go. A dispatcher argument for the same two reasons ``room`` is: unit.v1 is sealed,
        and where an agent writes must never be assertable from a request body. ``""`` (every chat
        that writes to the person's own desk, and every non-chat trigger) leaves the dispatch
        byte-identical to before.

        ``model`` is the chat's own pick from the operator's model catalog — a catalog id, never a
        free-form model name, and a dispatcher argument for the reasons ``target`` is. ``""`` (a
        chat nobody picked for, and every non-chat trigger) runs on the person's default. A pick
        that cannot run raises ``model_providers.ModelChoiceFault`` before anything is spawned.

        ``context.session`` (the chat conversation thread) is an agent-api routing hint, not part of the
        published unit.v1 wire contract — it is stripped before the schema check so the envelope stays
        conformant, while ``dispatch_id`` / ``build_unit_env`` still read it off the in-memory dispatch."""
        contracts.validate_unit_invocation(_without_chat_session(invocation))  # fail loud at the seam
        self.dispatched.append(invocation)
        identity = invocation["identity"]
        uid = dispatch_id(invocation)
        self._revoke_ended_incarnation(uid)
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
                             scaffold_workspaces=scaffold_workspaces, target=target,
                             friction=self._friction, catalog=self._catalog, model_choice=model,
                             effort_choice=effort,
                             admin=lambda: self.is_admin(identity["subject"]))
        self._record_delegation(uid, env)
        if self._workload_redis is not None:
            # The worker connects as its unit's own Redis user, never with the service connection.
            # A user that cannot be defined refuses the dispatch: the fallback would be the service
            # credential, which reaches every unit's streams.
            from control_plane import workload_redis

            env["REDIS_URL"] = workload_redis.grant(
                self._workload_redis, secret=self._settings.internal_api_secret.get_secret_value(),
                unit_id=uid, service_url=self._settings.redis_url)
        # The unit's input-stream key: the worker runs only the stream entries signed with it, and
        # agent-api is the one writer that holds it (shared/unit_input.py).
        in_key = self._unit_input_key(uid)
        if in_key:
            env[unit_input.KEY_ENV] = in_key
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
        delivery = (self._predeliver(uid, invocation, entry_nonce, inbox=inbox)
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
        try:
            acked = self._runtime.spawn(uid, self._settings.agent_profile, env)
        except RuntimeFault as fault:
            # FAIL LOUD AND ATTRIBUTABLE (P18). The runtime said no, or could not be reached; the
            # typed fault goes back to the caller (a 502/503 the chat can render), and the words this
            # dispatch pre-delivered are taken back unless a live worker already has them.
            if not self._spawn_failed(uid, fault, entry_nonce if delivery is not None else ""):
                raise
            acked = uid      # a live worker took the turn: the refusal stopped nothing
        else:
            # The unit is up: whatever stopped its queue before has stopped stopping it. Only where
            # the warm path just answered — a topology without a reachable redis has no record.
            if delivery is not None:
                unit_faults.clear(self._redis(), uid)
        if delivery is not None:
            self._watch_delivery(uid, env, tail=delivery)
        logger.info(
            "dispatch SPAWN workload=%s trigger=%s subject=%s launcher=%s warm_delivery=%s room=%s "
            "scaffold_mounts=%s",
            acked, invocation["trigger"], identity["subject"], identity["launcher"], delivery is not None,
            (room or {}).get("meeting_id") or "-", scaffold_workspaces or "-",
        )
        return acked

    # ── the worker's delegation token ends with its unit (delegation_revocation) ─────

    def _record_delegation(self, uid: str, env: dict[str, str]) -> None:
        """Record the token this dispatch minted against its unit, so the reaper revokes it when the
        unit ends. A token that cannot be recorded could never be revoked, so it is WITHHELD: with no
        token the worker attaches no vexa MCP (the endpoint alone attaches nothing) and says so, the
        same as a deployment with no toolbelt."""
        token = env.get("VEXA_MCP_DELEGATION_TOKEN")
        if not token or self._delegation_store is None:
            return
        try:
            claims = delegation.verify_delegation(
                self._settings.mcp_delegation_secret.get_secret_value(), token)
            delegation_revocation.record(self._delegation_store, unit_id=uid,
                                         jti=str(claims.get("jti") or ""), exp=int(claims["exp"]))
        except Exception:  # noqa: BLE001 — an unrecorded token is never handed out
            env.pop("VEXA_MCP_DELEGATION_TOKEN", None)
            logger.exception("delegation token for unit=%s could not be recorded for revocation — "
                             "the worker runs WITHOUT the vexa MCP", uid)
            return
        # Published as the unit's CURRENT token too, which the reaper re-mints from before it
        # expires and the worker reads before each turn (control_plane.delegation_refresh). A
        # publish that fails costs only the refresh: the worker still boots with this token.
        # REDIS_WORKLOAD_ACL=shared gives every worker the service connection, so a token in Redis
        # would be readable by every other unit: there it is never published, and the unit keeps
        # this token (and its tools) until its exp, unrefreshed.
        if self._settings.redis_workload_acl == "shared":
            return
        # A unit id can be reached by a second dispatch for ANOTHER person (a meeting's unit is keyed
        # on the meeting alone), and the worker on that id reads the published token before each
        # turn. Whose worker that is depends on a race the dispatcher cannot see — a warm touch keeps
        # the first person's worker, an ended one is respawned for this dispatch — so neither
        # person's token is left where the other's worker could read it: the published token is
        # withdrawn, and this dispatch's is published only when the runtime reports the previous
        # incarnation ended (the worker about to start is then this dispatch's own).
        try:
            if not delegation_refresh.same_authority(
                    self._delegation_store, self._settings.mcp_delegation_secret.get_secret_value(),
                    unit_id=uid, claims=claims):
                delegation_refresh.withdraw(self._delegation_store, unit_id=uid)
                if not self._incarnation_ended(uid):
                    logger.warning("unit=%s held another person's delegation token — withdrawn, and "
                                   "this dispatch's is not published to it", uid)
                    return
        except Exception:  # noqa: BLE001 — a worker that could read the other token gets none
            env.pop("VEXA_MCP_DELEGATION_TOKEN", None)
            logger.exception("unit=%s held another person's delegation token, which could not be "
                             "withdrawn — the worker runs WITHOUT the vexa MCP", uid)
            return
        try:
            delegation_refresh.publish(self._delegation_store, unit_id=uid, token=token,
                                       exp=int(claims["exp"]))
        except Exception:  # noqa: BLE001 — the token stands; only its replacement is lost
            logger.warning("delegation token for unit=%s could not be published for refresh — the "
                           "unit keeps its tools until this token expires", uid, exc_info=True)

    def _incarnation_ended(self, uid: str) -> bool:
        """Does the runtime report ``uid``'s previous container ended? Anything else — running,
        starting, unknown, unreadable — is False."""
        try:
            return self._runtime.await_done(uid, timeout_sec=0.0) in delegation_revocation.ENDED_STATES
        except Exception:  # noqa: BLE001 — not knowing is not "ended"
            return False

    def _revoke_ended_incarnation(self, uid: str) -> None:
        """A unit id is reused: a chat thread dispatches every turn to the same id, and a new
        container takes it once the last one idled out. When this unit still holds a token older
        than the grace and the runtime says its container ended, that token belongs to the ended
        incarnation: revoke it now, before the next incarnation makes the id live again and the
        reaper's sweep can no longer tell the two apart. Best-effort: anything it cannot learn is
        left to the reaper."""
        store = self._delegation_store
        if store is None:
            return
        try:
            if not delegation_revocation.has_revocable(store, uid):
                return
            state = self._runtime.await_done(uid, timeout_sec=0.0)
            if state in delegation_revocation.ENDED_STATES:
                revoked = delegation_revocation.revoke_unit(store, uid)
                if revoked:
                    logger.info("revoked %d delegation token(s) of unit=%s's ended incarnation",
                                revoked, uid)
        except Exception:  # noqa: BLE001 — the reaper is the backstop
            logger.warning("could not check unit=%s's previous incarnation for revocation", uid,
                           exc_info=True)

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

    def _unit_input_key(self, uid: str) -> str:
        secret = self._settings.internal_api_secret
        return unit_input.unit_key(secret.get_secret_value() if secret else "", uid)

    def _warm_fail(self) -> None:
        """An op on the warm client failed — drop it and back off (the spawn path still dispatched)."""
        self._warm_stream = None
        self._warm_retry_at = time.monotonic() + 60.0

    def _predeliver(self, uid: str, invocation: dict, nonce: str,
                    *, inbox: Optional[dict] = None) -> Optional[str]:
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
            entry = {"type": "message", "prompt": prompt, "nonce": nonce}
            if inbox:
                entry["inbox"] = inbox
            r.xadd(input_topic(uid), unit_input.signed_entry(self._unit_input_key(uid), entry))
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

    def _spawn_failed(self, uid: str, fault: RuntimeFault, nonce: str) -> bool:
        """A spawn ended in ``fault``. Returns True only when the turn is running anyway.

        THE WORDS ARE TAKEN BACK. ``_predeliver`` put this turn on the inbox before the spawn was
        asked for, so a refused spawn used to leave it there under "queued behind the current turn"
        for an hour, waiting for a worker nobody started — and a retry then ran it twice. The entry
        is withdrawn instead, and the caller's typed refusal is the one record of it.

        …UNLESS A LIVE WORKER ALREADY TOOK IT. A warm worker reads its inbox whatever the runtime
        is doing, so a runtime that was unreachable for one call may have stopped nothing: when the
        worker's cursor is already past this entry the turn is running, and refusing it would ask
        the person to send again something that is being answered. That is logged, and only that.

        Otherwise the fault is recorded against the unit (`unit_faults`) so the chat's queue and any
        view attached to it can say which dependency stopped it."""
        taken = self._retract(uid, nonce) if nonce else False
        log_fields = {"event": "runtime_fault", "source": fault.source, "kind": fault.kind,
                      "op": fault.op, "status": fault.status, "unit": uid,
                      "turn_running": taken, "upstream": fault.upstream[:300]}
        if taken:
            logger.warning(json.dumps(log_fields))
            return True
        logger.error(json.dumps(log_fields))
        unit_faults.record(self._redis(), uid, fault.as_dict())
        return False

    def _retract(self, uid: str, nonce: str) -> bool:
        """Withdraw the inbox entry carrying ``nonce``. True when a worker had already taken it.

        agent-api is the in-topic's one writer (P23), so withdrawing its own entry is its call.
        Best-effort: no redis, or an entry no longer there, withdraws nothing and takes nothing."""
        r = self._redis()
        if r is None:
            return False
        key = self._unit_input_key(uid)
        try:
            cursor = r.get(inbox_cursor_key(uid)) or ""
            for entry_id, fields in r.xrevrange(input_topic(uid), count=50) or []:
                msg = unit_input.verified_turn(key, fields or {})
                if not msg or msg.get("nonce") != nonce:
                    continue
                if cursor and _stream_id_le(entry_id, cursor):
                    return True
                r.xdel(input_topic(uid), entry_id)
                return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not withdraw the refused turn for unit=%s: %s", uid, exc)
        return False

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
                    except RuntimeFault as fault:
                        # Nobody is waiting on this call to answer, so the fault is RECORDED: the
                        # message is still on the inbox and the chat's queue must say what is
                        # holding it, not "queued behind the current turn" for an hour (P18).
                        logger.error(json.dumps({
                            "event": "runtime_fault", "source": fault.source, "kind": fault.kind,
                            "op": fault.op, "status": fault.status, "unit": uid,
                            "watchdog": True, "upstream": fault.upstream[:300]}))
                        unit_faults.record(self._redis(), uid, fault.as_dict())
                        return
                    except Exception:  # noqa: BLE001
                        logger.exception("delivery-watchdog respawn failed for unit=%s", uid)
                        return
                    respawned = True
            if not respawned:
                logger.warning("no turn-accepted within %.0fs for unit=%s — turn queued behind a long "
                               "turn, or lost to a concurrent boot", self._ACK_DEADLINE_SEC, uid)

        threading.Thread(target=watch, daemon=True, name=f"warm-watch-{uid}").start()
