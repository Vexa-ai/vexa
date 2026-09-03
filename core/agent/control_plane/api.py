"""api.py — the agent-api HTTP front door (the unit control plane's entrypoint).

A thin FastAPI surface mirroring ``runtime_kernel/api.py``. Routes (the gateway api.v1 proxies these):
  POST /invocations          — the dispatcher sink: a unit.v1 dispatch → a runtime.v1 agent spawn
  POST /api/chat             — a chat *now*-dispatch, streamed back as an SSE VIEW of its Stream
  POST /api/chat/reset       — drop a session
  GET  /api/sessions         — list a subject's sessions
  GET  /api/routines …       — routines (compile to schedule.v1 cron jobs)
  POST /events               — the generic event ingress (event.v1 → unit.v1)
  GET  /api/workspace/…      — read the workspace tree/file
  GET  /health               — liveness

Chat is **not** run in-process (agents never run in the control plane). ``/api/chat`` builds a now
dispatch, asks the Dispatcher to spawn the isolated container, then RELAYS the dispatch's output Stream
(``unit:<id>:out``) as SSE via the injected ``StreamReader``. When no reader is wired it answers ``501``
honestly. Built lazily (PEP 562) so ``uvicorn control_plane.api:app`` wires the real adapters at startup.
"""
from __future__ import annotations

import os

import functools
import hashlib
import hmac
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from fastapi import APIRouter, Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from control_plane import meeting_room
from control_plane import meeting_steering
from control_plane import schedule_digest as schedule_digest_mod
from control_plane import routines as routines_mod
from control_plane.config_preflight import NOT_CONFIGURED, capability_state, missing_capability_keys
from shared import units
from shared import entities as entities_mod
from control_plane import workspace_routines as workspace_routines_mod
from control_plane import link_resolver as link_resolver_mod
from control_plane import workspace_ids as ids_mod
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from control_plane.workspace_attach import (
    CloneError,
    activate_workspace,
    active_workspaces,
    attach_shared_workspace,
    attached_workspaces,
    create_shared_workspace_dir,
    create_workspace,
    deactivate_workspace,
    delete_workspace,
    ensure_workspace_private,
    ensure_workspace_shareable,
    rename_workspace,
    set_archived,
    set_shared_active,
    shared_active_mounts,
    shared_attached_state,
    swap_workspace,
    workspace_dir_for,
    workspace_slot_dir,
)
from control_plane.workspace_publish import PublishError, RepoExistsError, publish_workspace, published_remote_url
from control_plane.workspace_git_sync import RemoteSyncError, pull_origin, push_origin, remote_status
from control_plane.workspace_purpose import read_purpose, write_purpose
from control_plane import workspace_membership as membership_mod
from control_plane import git_credentials as git_creds
from control_plane import dispatch as dispatch_mod
from control_plane import deploy_keys as deploy_keys_mod
from control_plane import workspace_credentials as wcreds
from control_plane import repo_ref
from shared.git_redaction import redact as redact_secrets
from shared import workspace_paths as wpaths
from control_plane import global_layer
from control_plane import version as version_mod
from control_plane import system_mounts
from control_plane import scaffolds as scaffolds_mod
from control_plane import friction as friction_store_mod
from control_plane import model_endpoint
from shared import friction as friction_mod
from control_plane import chat_intents
from control_plane.workspace_membership import MembershipError, MembershipIndex, InMemoryMembershipIndex
from control_plane.dispatch import Dispatcher
from control_plane.events import event_to_invocation
from shared.ports import SchedulerPort, StreamReader
from control_plane.workspace_reader import WorkspaceReader

# The models, pure helpers and constants every route is built out of. They live in
# `api_shared` so the routers can import them too — see that module's docstring.
from control_plane.routers import health as routers_health
from control_plane.routers import chats as routers_chats
from control_plane.routers import admin as routers_admin
from control_plane.routers import meetings as routers_meetings
from control_plane.routers import scaffolds as routers_scaffolds
from control_plane.routers import friction as routers_friction
from control_plane.routers import workspaces as routers_workspaces
from control_plane.api_shared import (logger, _PHASE_WORD, _iso, _provenance_line, _epoch_text, 
    MAX_UPLOAD_BYTES, MEETING_STREAM_TRANSCRIPT_REPLAY, _upload_filename, _truncate_title, 
    _stream_tail_id, CHAT_TURN_HEAD_TTL_SEC, _chat_turn_head_key, _record_chat_turn_head, 
    _chat_turn_head, _Sessions, LIVE_SILENCE_TTL_SEC, _LiveMeetings, ChatContextBody, ChatBody, 
    ScaffoldMintBody, ScaffoldHandBody, ResetBody, RoutineCreate, RoutineEnabledPatch, 
    WorkspaceSwapBody, WorkspacePublishBody, WorkspaceRenameBody, WorkspacePushBody, 
    GitTokenBody, WorkspacePullBody, WorkspacePurposeBody, InviteCreateBody, InviteAcceptBody, 
    RoleSetBody, SharedNewBody, SharedAttachBody, SharedActiveBody, ArchiveBody, 
    WorkspaceActivateBody, WorkspaceNewBody, WorkspaceDeactivateBody, _encode_sse_cursor, 
    _decode_sse_cursor, _sse, _has_custom_model_endpoint, _model_creds_error_message, 
    MEETING_CHAT_TRANSCRIPT_SEGMENTS, _fold_meeting_transcript, _meeting_grounding, 
    CONTEXT_SENTINEL, _AMBIENT_TAB_KINDS, _ambient_gated, _WORKSPACE_README_LINES, 
    _WORKSPACE_README_CHARS, _fold_workspace_grounding, _enriched_meeting_focus, 
    _context_grounding, _ROOM_SOURCE, _http_email_subject_lookup, _http_meeting_owner_lookup)  # noqa: F401

def create_app(
    dispatcher: Dispatcher,
    *,
    stream_reader: Optional[StreamReader] = None,
    sessions: Optional[_Sessions] = None,
    reader: Optional[WorkspaceReader] = None,
    scheduler: Optional[SchedulerPort] = None,
    invocations_url: Optional[str] = None,
    redis_url: Optional[str] = None,
    membership_index: Optional[MembershipIndex] = None,
    meeting_owner_lookup: "Optional[object]" = None,
    schedule_source: "Optional[Callable[[str], list]]" = None,
    email_subject_lookup: "Optional[object]" = None,
) -> FastAPI:
    if sessions is not None:
        sess = sessions
    elif redis_url:
        import redis as _redis

        sess = _Sessions(_redis.from_url(redis_url, decode_responses=True))
    else:
        sess = _Sessions()
    live = _LiveMeetings()
    # THE SCAFFOLD STORE (PRD 5.5). Same redis client as the session index and the same in-memory
    # fallback, for the same reasons — see control_plane/scaffolds.py for why it is redis and not
    # the workspace volume (it must outlive a wipe of the recipient's desk and exist before the
    # recipient does).
    if redis_url:
        import redis as _redis_for_scaffolds

        scaffolds = scaffolds_mod.ScaffoldStore(_redis_for_scaffolds.from_url(redis_url, decode_responses=True))
    else:
        scaffolds = scaffolds_mod.ScaffoldStore()
    # THE FRICTION STORE (PRD decision 33). Same redis client, same in-memory fallback, and for
    # the same reasons as the scaffold store — plus one of its own: `shared/friction.py` states why
    # this record does NOT live in the flows `friction` table the rig has been writing (the people
    # half posts HERE, and this service cannot reach the flows lane).
    if redis_url:
        import redis as _redis_for_friction

        friction = friction_store_mod.FrictionStore(
            _redis_for_friction.from_url(redis_url, decode_responses=True))
    else:
        friction = friction_store_mod.FrictionStore()
    # A REFUSED model endpoint is friction, not a log line (F84). The dispatcher is built before the
    # store, so the sink is attached here; a fake dispatcher in a test simply has no such method.
    if hasattr(dispatcher, "attach_friction"):
        dispatcher.attach_friction(friction.file)
    wsr = reader or WorkspaceReader("/workspaces")
    mindex: MembershipIndex = membership_index if membership_index is not None else InMemoryMembershipIndex()
    # THE WORKSPACE REGISTRY (PRD decision 26.1) — id → where that workspace is NOW. Same redis
    # client and the same in-memory fallback as the scaffold store, for the same reason: the unit
    # tests need no redis and the deployment needs no second store. It is a DERIVED index — every
    # field but the display name is recomputable by walking the volume — so a redis loss costs the
    # names and nothing else.
    if redis_url:
        import redis as _redis_for_ids

        _ws_redis = _redis_for_ids.from_url(redis_url, decode_responses=True)
    else:
        _ws_redis = None
    workspace_registry = ids_mod.WorkspaceRegistry(_ws_redis)
    # THE USAGE SIGNAL (founder refinement, 2026-09-02: the desk README is "mostly links to the
    # other cards in different workspaces"). A list of links is only useful if the ones this person
    # uses are at the top, and the only place that knows which those are is the panel that opens
    # them. Same redis, same in-memory fallback.
    workspace_touches = ids_mod.TouchLog(_ws_redis)
    # THE MIGRATION, at startup and idempotent. Every workspace already on the volume gets an id
    # written into it and a row in the registry; parked trees get the id file so it survives the
    # swap that brings them back. It runs HERE rather than in a script because a workspace with no
    # id is one nothing can link to, and a link that silently does nothing is the defect being
    # fixed — a migration nobody remembered to run would reproduce it exactly.
    try:
        _migrated = ids_mod.migrate(wsr.root, workspace_registry)
        if _migrated["minted"] or _migrated["parked_minted"]:
            logger.info("workspace ids: minted %d live, %d parked; %d indexed",
                        len(_migrated["minted"]), len(_migrated["parked_minted"]),
                        len(_migrated["indexed"]))
    except Exception as exc:  # noqa: BLE001 — a volume that cannot be walked must not stop the boot
        logger.warning("workspace-id migration could not run: %s: %s", type(exc).__name__, exc)
    app = FastAPI(title="vexa-agent-api", version="0.12.0")

    # ── THE COMPANY-LAYER GATE, ENFORCED PER REQUEST ────────────────────────────────────────────
    # Founder ruling, 2026-09-02: a Vexa with no company layer serves nobody. That was first built
    # as a check at SIGN-IN — and a session minted before the gate existed walked straight past it,
    # observed live on 2026-09-02: an old cookie got the whole terminal, a chat, and an agent turn
    # on an instance that could not say which company it worked for. A door check is not a gate; it
    # is a greeting. The gate belongs where the WORK happens, on every request, because the client
    # is presentation and the client can be stale, cached, forged or simply already open.
    #
    # WHO GETS THROUGH while the layer is missing: the instance admin, and nobody else. Two
    # deliberate holes, both narrow:
    #   * `/api/global/*` — the state the wizard polls and the verb that lifts the gate. A gate
    #     that blocks the only way to open it is a deadlock.
    #   * requests with NO subject header — the internal tier (`/api/admin/*` and friends), which
    #     is gated on X-Internal-Secret instead and has no user to judge.
    # And when NO admin exists yet the gate does not refuse at all: on a virgin instance the next
    # sign-in is the claim, so refusing here would make a fresh install unclaimable.
    #
    # This is the FAIL-CLOSED half of the pair. The terminal deliberately fails OPEN on an
    # unreachable probe so a transient fault cannot brick sign-in on a working instance; it can
    # afford to precisely because this middleware holds, so a browser that renders anyway can still
    # do nothing.
    # `/api/version` joins them (decision 39): the swap script and an open browser tab both poll
    # it to find out what is serving, and neither has a subject. Gating it would answer 403 to
    # the one question whose whole purpose is answerable from outside, before anyone signs in.
    _GATE_OPEN_PREFIXES = ("/api/global/", "/api/version")

    @app.middleware("http")
    async def _company_layer_gate(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith(_GATE_OPEN_PREFIXES):
            subject = request.headers.get("x-user-id") or (
                settings.agent_default_subject if settings is not None else "")
            if subject:
                gate = global_layer.instance_state(settings)
                # A DEGRADED read is "unknown", never "missing". `instance_state` answers missing
                # when it cannot reach admin-api — right for anything that SENDS, wrong here, where
                # the consequence is locking every user out of a working instance because one probe
                # timed out (and, in a deployment with no admin-api configured at all, locking them
                # out permanently). Refuse only on a POSITIVE read. The closed half of the pair
                # lives where the damage is: the flows engine parks rather than mails, and the
                # operator verbs refuse, both fail-closed.
                if (not gate.get("degraded")
                        and gate.get("global_setup") != global_layer.COMPLETED
                        and gate.get("admin_exists")
                        and not global_layer.is_admin(settings, str(subject))):
                    return JSONResponse(status_code=403, content={
                        "detail": global_layer.GATE_SENTENCE,
                        "global_setup": global_layer.MISSING,
                        "why": ("This instance has not been set up yet. Only its administrator can "
                                "use it until the company layer is written."),
                    })
        return await call_next(request)

    app.state.dispatcher = dispatcher
    app.state.sessions = sess
    # Reachable for the operator seams that rename a workspace and for the tests that prove a
    # rename moves nothing else.
    app.state.workspace_registry = workspace_registry
    app.state.live_meetings = live
    app.state.scheduler = scheduler
    settings = dispatcher.settings if dispatcher is not None else None
    # The SSE ownership gate's owner-lookup (P0): default = HTTP to meeting-api; injectable for L2 tests.
    _meeting_owner_lookup = meeting_owner_lookup or _http_meeting_owner_lookup(
        settings.meeting_api_url if settings is not None else "")
    # The ambient schedule digest's rows source (context bundle): TTL-cached meeting-api fetch;
    # injectable for L2 tests, same seam style as meeting_owner_lookup.
    # The post-meeting room's participant ADDRESS → subject resolver; injectable for L2 tests, same
    # seam style as meeting_owner_lookup. See _http_email_subject_lookup for why this door is awkward.
    _email_subject_lookup = email_subject_lookup or _http_email_subject_lookup(
        (settings.admin_api_url if settings is not None else "") or "",
        settings.internal_api_secret.get_secret_value() if settings is not None else "",
        settings.admin_api_token.get_secret_value() if settings is not None else "",
    )
    _schedule_source = schedule_source or schedule_digest_mod.digest_source(
        settings.meeting_api_url if settings is not None else "", mindex.list)

    # TOPOLOGY BOUNDARY (Lane M vector 3): agent-api trusts X-User-Id / X-User-Email as ground truth.
    # That trust is only SOUND when the gateway is the SOLE ingress — the gateway strips any client-sent
    # x-user-id/x-user-email and re-injects the values it resolved from the verified api-key. In the
    # current dev/direct topology the terminal and host-local clients reach agent-api WITHOUT the gateway
    # hop (compose loopback + VEXA_AGENT_DEFAULT_SUBJECT fallback), so those headers are spoofable and
    # restricted-mode invites MUST NOT be relied on as a security boundary here. A hardened deploy sets
    # VEXA_REQUIRE_GATEWAY_IDENTITY=1: agent-api then rejects any request lacking the gateway's signed
    # identity marker (X-Gateway-Verified), so identity headers are only honored when the gateway put
    # them there. OFF by default so the dev/direct topology keeps working. Full fix = route the terminal
    # through the gateway (Stage 4) and make the gateway the only thing that can reach agent-api.
    _require_gateway_identity = os.environ.get("VEXA_REQUIRE_GATEWAY_IDENTITY", "").strip().lower() in ("1", "true", "yes")

    def subject_of(request: Request) -> str:
        """The authenticated subject (P20). The gateway resolves the api-key → user_id and injects
        ``X-User-Id``; agent-api derives the workspace/chat/quota partition from THAT, never from the
        client body/query. Fail-closed (401) when the header is absent, unless a single-user fallback
        (``VEXA_AGENT_DEFAULT_SUBJECT``) is configured for a direct/self-host deploy with no gateway in front.

        When ``VEXA_REQUIRE_GATEWAY_IDENTITY`` is set, the request must additionally carry the gateway's
        signed identity marker (``X-Gateway-Verified``) — a hardened deploy enforces that identity headers
        were injected by the gateway, not forged by a direct/host-local caller (see the TOPOLOGY BOUNDARY
        note above). This does NOT change the default dev/direct topology."""
        if _require_gateway_identity and not request.headers.get("x-gateway-verified"):
            raise HTTPException(status_code=401,
                                detail="gateway-signed identity required (VEXA_REQUIRE_GATEWAY_IDENTITY)")
        uid = request.headers.get("x-user-id")
        if uid:
            return uid
        fallback = settings.agent_default_subject if settings is not None else ""
        if fallback:
            return fallback
        raise HTTPException(status_code=401, detail="missing X-User-Id (agent-api is fronted by the gateway)")

    def _resolve_room(request: Request, subject: str, meeting_id: str,
                      participants: "Optional[list[str]]" = None,
                      names: "Optional[dict]" = None,
                      speakers: "Optional[list[str]]" = None,
                      read_max: Optional[int] = None) -> dict:
        """Turn a caller-named MEETING into the room this turn may read.

        The room is the post-meeting mount widening (founder ruling: a person's `personal`/desk
        workspace is company knowledge, not private, and the post-meeting agent reads the attendees'
        desks to write ONE shared write-up; only `_system` stays private).

        GATES, all fail-CLOSED, in this order:

        0. CALLER TIER — the internal-tier shared secret (`X-Internal-Secret` == `VEXA_INTERNAL_API_SECRET`,
           the same edge `/api/admin/overview` and the admin-api mirror already use). The room is a
           FLOWS/OPERATOR capability, not an end-user one: the post-meeting run is dispatched by
           `core/flows` talking to agent-api directly, while browser clients reach `/api/chat` through
           the gateway and hold no internal secret. An UNCONFIGURED secret means nobody gets a room.
           **Under the participant model this gate is also the trust boundary on WHO is in the room** —
           see the residual below.
        1. ENTITLEMENT — `_meeting_owner_lookup`, the EXISTING meeting access check (meeting-api
           `GET /meetings/{id}`, which evaluates its own access union in SQL and 404s a row the
           caller may not read). No second authorisation rule is invented for the room.
        2. OWNERSHIP — `meeting_room.assert_owner`: the row must be the caller's OWN meeting. A
           transcript-share recipient passes gate 1 and is refused here.
        3. GROUP DESK — `meeting_room.group_workspace_id` reads the meeting's BOUND shared workspace
           (`data.workspace_id`). Under decision 22 that is the ONE desk a room run may write; every
           other desk in the stack, the dispatch subject's own included, is demoted to read-only by
           `dispatch.build_mount_set`. The id is not a grant — the dispatcher re-reads the subject's
           role from that workspace's own policy/members.json.
        4. MEMBERSHIP + ORDER — `meeting_room.order_participants`: membership is the INVITE's
           participant ADDRESSES (`room_participants`); speaking only ORDERS them, via the ICS `CN=`
           map. Each address is resolved to a subject by `_email_subject_lookup` at mount time, and
           only a participant who already HAS a subject and a desk is mounted. A name never admits
           anybody, so a bad CN match costs ordering and nothing else.

        THE RESIDUAL, stated because it is a real one: membership now comes from the CALLER's list,
        so a trusted internal caller could name addresses that were not in the meeting. Gate 0 IS the
        trust boundary on that. It is a deliberate trade — the server-held alternative
        (`data.transcript_viewers`) is empty at post-meeting time, because nobody has clicked their
        share link yet, which made the whole feature inert on its normal path.

        Returns the room the dispatcher applies. `lookup` rides in it because address→subject
        resolution has to happen where the mount set is built (it needs the store root and the paths
        the subject's own stack already holds); it is an in-process callable on a dispatcher
        argument and never crosses a wire.
        """
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        if not secret or not hmac.compare_digest(provided, secret):
            logger.warning("room REFUSED subject=%s meeting=%s reason=not-internal-caller",
                           subject, meeting_id)
            raise HTTPException(status_code=403,
                                detail="the meeting room is an internal-tier capability")
        owned = _meeting_owner_lookup(subject, meeting_id)
        try:
            meeting_room.assert_owner(owned, requester=subject)
        except meeting_room.RoomRefused as e:
            logger.warning("room REFUSED subject=%s meeting=%s reason=%s", subject, meeting_id, e.reason)
            raise HTTPException(status_code=403, detail=e.reason)
        # DECISION 22: the ONE writable desk of a room run is the meeting's GROUP desk, when the
        # meeting is bound to a shared workspace. Server-derived — meeting-api owns the binding
        # (`POST /meetings/{platform}/{native}/workspace`, owner-scoped), so a caller cannot name a
        # group. Returning the id grants nothing: the dispatcher still asks that workspace's own
        # policy/members.json whether THIS subject may write it.
        group = meeting_room.group_workspace_id(owned)
        ordered = meeting_room.order_participants(participants, names=names, speakers=speakers)
        return {"meeting_id": str(meeting_id), "ordered": ordered, "source": _ROOM_SOURCE,
                "group_workspace_id": group, "read_max": read_max,
                "lookup": _email_subject_lookup}












    # ── routines (MVP2) — a scheduled routine compiles to a schedule.v1 cron job whose body is a
    #    unit.v1 dispatch POSTed back to /invocations when due (the runtime owns the durable cron) ──




    # ── events (MVP3) — the GENERIC event-source ingress: any event.v1 Event → a unit.v1 dispatch →
    #    the one Dispatcher. agent-api knows no tool/domain; the unit reaches email/calendar via its
    #    toolbelt. Email-triage, post-meeting, news all POST here (one front door, P6) ──

    def _read_target(request: Request, slug: Optional[str], *, write: bool = False) -> Path:
        """Resolve which workspace dir a read or write targets, returning its ABSOLUTE PATH. Default (no
        slug) = the caller's primary baseline. A `slug` addresses ANOTHER mount in the caller's active set —
        their own non-primary private workspaces (which live under .attached, NOT <root>/<slug>) OR a SHARED
        workspace they're a member of. Authorization is by construction: the set is built for THIS subject
        (own actives + shared_active_mounts over their memberships), so a slug not in it → 403. This is what
        lets the KNOWLEDGE panel render one section per active mount without leaking arbitrary workspaces.

        ``write=True`` STOPS THERE, and that asymmetry is the founder's ruling of 2026-09-02: a desk is
        **readable by any signed-in member of this instance and writable by its owner**. So a read may fall
        through to another person's desk (below) and a write may never. `write=True` is therefore exactly
        today's behaviour, unchanged, and the widening is confined to the read path — which is the only way
        to add it without weakening a write seam by accident.

        The read fall-through is not a convenience: without it the link resolver would answer `readable` for
        a colleague's desk and the panel would then 403 on the click. A chip that says you may open
        something and an endpoint that refuses it is worse than either answer alone."""
        subject = subject_of(request)
        target = (slug or "").strip()
        # _system — the caller's OWN private-system workspace (RW, surfaced hidden-by-default in the files
        # panel). It's a per-subject dispatch mount, not in the active set, so authorize it directly here:
        # it can only ever resolve to THIS subject's own .system store — never another user's.
        if target == system_mounts.SYSTEM_SLUG:
            return system_mounts.system_store_path(wsr.root, subject)
        # The _global org tier is readable by EVERY subject — it is mounted ro into every worker,
        # so the read API mirrors that; writes still go only through the admin's worker mount.
        if target == system_mounts.GLOBAL_SLUG:
            g = wsr.root / system_mounts.GLOBAL_SLUG
            if g.exists():
                return g
            raise HTTPException(status_code=404, detail="the organisation tier is not configured")
        mounts = active_workspaces(wsr.root, subject)  # own actives (real .attached paths); may raise ValueError
        try:
            mounts = mounts + shared_active_mounts(wsr.root, subject, mindex.list(subject))
        except Exception:  # noqa: BLE001 — a shared-mount hiccup must not break a plain own-workspace read
            pass
        if not target or target == subject:
            primary = next((m for m in mounts if m.primary), None)
            return Path(primary.path) if primary else (wsr.root / subject)
        for m in mounts:
            if m.slug == target:
                # A MOUNTED MATCH IS NOT THE SAME AS A WRITABLE ONE. `shared_active_mounts` mounts
                # a viewer's workspace too (`write=False`) — read this far unguarded, `write=True`
                # returned it anyway, so any viewer of any currently-active shared workspace could
                # write to it via this path, role checked nowhere. Fall through instead of
                # returning: the ownership check below re-derives the real role authoritatively
                # and gives the correct 403 for exactly this subject.
                if write and not m.write:
                    break
                return Path(m.path)
        # ANOTHER PERSON'S DESK — readable, never writable (the ruling above). The registry is asked
        # rather than the directory layout, so this can only ever resolve something that IS a desk:
        # a group the caller does not belong to still 403s here, and `_system` has no registry row
        # at all, by construction, precisely so nothing can reach it this way.
        if not write and subject:
            rec = workspace_registry.by_slug(target)
            if rec and rec.get("kind") == "desk":
                d = Path(str(rec.get("dir") or ""))
                if d.is_dir():
                    return d
        # OWNERSHIP, NOT MOUNT STATE (F196/F198/F200). The active set built above is a per-session
        # DISPLAY toggle — `shared_active_mounts` drops a workspace the subject switched off
        # (`hidden_shared_set`) even though their membership is unchanged — so a write to a shared
        # workspace the caller genuinely owns or contributes to 403'd here whenever it happened not
        # to be "on" for THIS mount set, the identical answer a stranger gets. The docstring above
        # already promises "authorized... by construction: own actives + shared_active_mounts over
        # their memberships" — which only holds if membership and mount state can never diverge, and
        # `hidden_shared_set` is exactly a way they do.
        #
        # The authoritative answer is one call away and already the pattern this module uses
        # correctly elsewhere: `_require_shared_write` (this file, the parallel MANAGEMENT-write
        # gate) calls the same `require_role`, which reads `policy/members.json` directly — the
        # same authority `shared_active_mounts` itself defers to via `is_member`, just not gated on
        # whether the workspace happens to be mounted right now.
        if write and target and subject:
            try:
                membership_mod.require_role(wsr.root, target, subject, "contributor")
            except MembershipError:
                pass  # not a member, or below contributor — the 403 below is the real answer
            else:
                d = membership_mod._ws_dir(wsr.root, target)
                if d.is_dir():
                    return d
        raise HTTPException(status_code=403, detail="not authorized for this workspace")

    def _manage_dir(subject: str, slug: Optional[str]) -> Path:
        """Resolve a workspace dir for a MANAGEMENT op (git sync, purpose) — unlike ``_read_target`` this
        also reaches the caller's PARKED slots (a workspace need not be mounted to manage it). Own slots
        first (active or parked); a slug that isn't one of them but IS a shared workspace the caller belongs
        to resolves to the shared dir. Neither path can ever reach another user's private workspace."""
        try:
            return workspace_dir_for(wsr.root, subject, slug)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            pass
        target = (slug or "").strip()
        if target and membership_mod.is_member(wsr.root, target, subject) is not None:
            return membership_mod._ws_dir(wsr.root, target)
        raise HTTPException(status_code=404, detail="workspace not found")

    def _repo(raw: "Optional[str]") -> "Optional[str]":
        """The value a PERSON typed into "Repository", normalized — or a 422 they can act on.

        It runs before a subprocess exists, which is the whole point: on 2026-09-02 a PAT pasted into
        that field was handed straight to ``git clone``, and git's "repository '<the token>' does not
        exist" put the secret in the error card, the response body and the browser console. A
        validator that runs after the clone has not protected anything.

        422 rather than 400 because the field is well-formed JSON and semantically wrong — and the
        detail is the sentence itself, which the terminal's presenter shows verbatim."""
        try:
            return repo_ref.normalize(raw)
        except repo_ref.RepoRefError as exc:
            # LOG THE KIND, NEVER THE VALUE. "someone pasted a token" is the operational signal; the
            # token is the thing we are refusing to have anywhere at all.
            logger.warning("repository field refused (kind=%s)", exc.kind)
            raise HTTPException(status_code=422, detail=exc.sentence)

    def _require_shared_write(subject: str, slug: Optional[str]) -> None:
        """A no-op for the caller's OWN workspaces; for a SHARED one, refuse anyone below contributor.
        (``_manage_dir`` resolves a workspace a viewer may READ — that is the right gate for status and
        purpose, and the wrong one for anything that rewrites the tree.)"""
        target = (slug or "").strip()
        if not target or target == subject:
            return
        try:
            workspace_dir_for(wsr.root, subject, target)
            return                                   # one of their own slots — their own business
        except (ValueError, KeyError):
            pass
        try:
            membership_mod.require_role(wsr.root, target, subject, "contributor")
        except MembershipError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))

    def _clone_fn(cred: "wcreds.Credential"):
        """The clone callable for one credential: the default clone, pre-bound to the deploy key's
        ``GIT_SSH_COMMAND`` when there is one. Uses ``workspace_attach``'s existing injection seam, so
        no signature anywhere else has to learn about ssh."""
        from control_plane.workspace_attach import _git_clone as _default_clone
        if cred.ssh_env:
            return functools.partial(_default_clone, ssh_env=cred.ssh_env)
        return _default_clone

    # ── THE SCAFFOLD (PRD 5.5) ──────────────────────────────────────────────────────────────────
    #
    # One record per moment a person arrives at, minted by the flow that creates the touch, read by
    # BOTH renderers: the terminal draws its header, tabs and focus from it, and the agent's first
    # turn is its opening and its refs. Before it existed, each renderer composed its own half out
    # of whatever it could find, and the two disagreed in every way the alpha ledger records.

    def _global_root() -> Path:
        """Where `_global` actually is, from agent-api's own filesystem.

        The volume slot FIRST and the configured source second — that order is the 2026-09-02
        single-store fix (audit N1): `_global` was two disjoint stores, agent-api read one and the
        admin's setup chat wrote the other, and his README went into a directory nothing reads."""
        vol = wsr.root / system_mounts.GLOBAL_SLUG
        if vol.is_dir():
            return vol
        return Path((settings.global_system_workspace_path if settings is not None else "") or "/nonexistent")

    def _internal_caller(request: Request) -> bool:
        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        return bool(secret) and hmac.compare_digest(provided, secret)

    def _meeting_row_for_scaffold(rec: dict, subject: str) -> "dict | None":
        """The meeting ROW behind a scaffold, read as whoever can actually see it.

        The recipient FIRST — for their own meeting that is the honest reader. Then the minter
        (`provenance.minted_by`, the organiser's uid), because an attendee who has not yet redeemed
        their share cannot read the row at all, and a phase resolved from nobody is how an emailed
        link ends up telling a person their finished meeting is upcoming (ledger F4). The row is
        the meeting's own truth either way; only the reader changes."""
        mid = str(rec.get("meeting") or "")
        if not mid.isdigit():
            return None
        for reader_uid in (str(subject or ""), str((rec.get("provenance") or {}).get("minted_by") or "")):
            if not reader_uid:
                continue
            row = _meeting_owner_lookup(reader_uid, mid)
            if isinstance(row, dict):
                return row
        return None

    def _scaffold_state(rec: dict, subject: str, row: "dict | None") -> dict:
        """`refs.state`, RE-CHECKED at open. Computed at mint against what the mail was written for
        and again here against what is true when they click — days apart, and for a stranger who
        signed in meanwhile it is a different answer. A record that only carried the mint-time
        state would tell the agent to introduce itself to somebody it has been talking to."""
        desk = scaffolds_mod.desk_state(wsr.root, subject) if subject else "new"
        return {"desk": desk, "group": scaffolds_mod.group_state(wsr.root, scaffolds_mod.group_workspace_of(row))}

    def _scaffold_view(rec: dict, subject: str) -> dict:
        """The record as its reader gets it: the stored fields, the phase resolved from the meeting
        ROW, the state re-checked, the header derived, and the opening ALREADY SUBSTITUTED.

        The substitution happens here and not in the client because a client that composes text is
        a second author of the first thing the agent is told — which is the defect this whole record
        exists to remove. The terminal reads `opening_text` and writes nothing of its own.

        THE WIRE SHAPE IS THE INTERFACE, and it is pinned on the client side in exactly one function
        (`clients/terminal/src/minutes/scaffold.ts` `parseScaffold`). Field names here follow that
        function deliberately — flat `opening_preset` / `opening_text`, `refs.when` as RENDERED TEXT,
        timestamps as ISO strings — because two halves of one contract built the same afternoon is
        precisely how the `room_read` / `room_participants` mismatch 422'd every dispatch. Where the
        two must differ, BOTH shapes ship rather than one silently losing:
          · `provenance` is the record's OBJECT (flow · reaction · run · minted_by — a string cannot
            carry it); `provenance_line` is the same thing rendered for a panel to show.
          · `refs.when` is the rendered line and `refs.when_epoch` is the number the record stores.
        """
        row = _meeting_row_for_scaffold(rec, subject)
        phase = scaffolds_mod.phase_of(row)
        state = _scaffold_state(rec, subject, row)
        refs = dict(rec.get("refs") or {})
        refs["state"] = state
        row_data = (row or {}).get("data") if isinstance((row or {}).get("data"), dict) else {}
        title = refs.get("title") or (row_data or {}).get("title") or ""
        # `when` on the record is an EPOCH (the record's own shape). A caller that already rendered
        # it in the recipient's own zone (flows does, `_their_clock`) passes `when_text` and that
        # wins — the person's clock beats ours. The wire carries the TEXT under `when`, because that
        # is what a panel and a preset both need; the number stays available beside it.
        when_epoch = refs.get("when")
        when_text = refs.get("when_text") or _epoch_text(when_epoch)
        refs["when"] = when_text
        if when_epoch is not None:
            refs["when_epoch"] = when_epoch
        refs.pop("when_text", None)
        # THE NATIVE ID. `meeting:note` resolves to `kg/entities/meeting/<native>.md` while the
        # canvas binds to the ROW id — two different identifiers, and the client can only hold one
        # of them from the link. It comes off the row, never off the record: a native id remembered
        # at mint would be a second copy of a fact the meetings domain owns.
        native = str((row or {}).get("native_meeting_id") or "") or None
        fm, body = scaffolds_mod.read_preset(_global_root(), str(rec.get("opening") or ""))
        mounts = list(rec.get("workspaces") or [])
        prompt = scaffolds_mod.substitute(body, {
            "meeting": rec.get("meeting") or "the meeting in view",
            "title": title or "the meeting in view",
            "when": when_text,
            "state": scaffolds_mod.state_token(state["desk"], state["group"]),
            "ws": next((m for m in mounts if m != system_mounts.GLOBAL_SLUG), ""),
            "workspace": scaffolds_mod.WORKSPACE_WORD,
            "today": time.strftime("%Y-%m-%d"),
        })
        prov = rec.get("provenance") or {}
        return {
            "id": rec.get("id"),
            "kind": rec.get("kind"),
            "who": rec.get("who"),
            "meeting": rec.get("meeting"),
            "native": native,
            # RESOLVED, never stored. `null` means the row could not be read — an honest "we do not
            # know", which the renderer must treat as "keep the meeting's own layout", never as post.
            "phase": phase,
            "workspaces": mounts,
            "refs": refs,
            "opening_preset": rec.get("opening"),
            "opening_label": fm.get("label") or str(rec.get("opening") or "").replace("-", " "),
            # The text the agent is given, machinery-marked. The terminal renders none of it:
            # "the human sees turns, the agent sees instructions".
            "opening_text": prompt + scaffolds_mod.MACHINERY_NOTE,
            "tabs": list(rec.get("tabs") or []),
            "focus": rec.get("focus") or "",
            # DERIVED, not stored (the record says so): the phase word comes off the row we just
            # read, so a link clicked three days late cannot announce "upcoming" about a meeting
            # that has happened. No phase means the word is simply absent — never a guess.
            "header": {"title": title or (fm.get("label") or ""),
                       "flavor": ("meeting · " + _PHASE_WORD[phase]) if phase
                                 else ("meeting" if rec.get("meeting") else "chat"),
                       "when": when_text},
            "provenance": prov,
            "provenance_line": _provenance_line(prov, rec.get("minted_at")),
            "minted_at": _iso(rec.get("minted_at")),
            "redeemed_at": _iso(rec.get("redeemed_at")),
            "redeemed_by": rec.get("redeemed_by"),
            # WHETHER the transcript share was handed over, never WHAT it is (R-A08). This route is
            # what a panel polls; a capability that rode every read would be a capability in every
            # log that traffic touches — the shape the row exists to remove, one surface along.
            # `has_share` is what the client branches on to decide whether to ask for it at all.
            "has_share": bool(rec.get("share_token")),
            "share_handed_at": _iso(rec.get("share_handed_at")),
        }

    def _scaffold_is_for(rec: dict, request: Request, subject: str) -> bool:
        """May THIS caller read this scaffold? The recipient, the instance admin, or the service key.

        The recipient is matched on the gateway-injected address first (the cheap, exact answer) and
        on the resolved subject second, because a scaffold minted for a stranger names an ADDRESS
        and only becomes a subject when they sign in."""
        who = str(rec.get("who") or "").strip().lower()
        email = (request.headers.get("x-user-email") or "").strip().lower()
        if email and who and email == who:
            return True
        if rec.get("redeemed_by") and str(rec["redeemed_by"]) == str(subject):
            return True
        resolved = _email_subject_lookup(who) if who else None
        if resolved and str(resolved) == str(subject):
            return True
        return bool(global_layer.is_admin(settings, str(subject)))

    def _scaffold_recipient_is(rec: dict, request: Request, subject: str) -> bool:
        """Is this caller THE RECIPIENT — the three identity tests `_scaffold_is_for` runs, minus its
        admin clause and with no service-key door.

        Two predicates on purpose, because the two questions are different. Reading a scaffold is a
        support surface and an instance admin has a reason to be on it. The transcript SHARE is a
        bearer grant on somebody else's meeting: *may debug this record* is not *may watch this
        meeting*, and collapsing them would hand every admin a capability the record was minted to
        give one person (R-A08)."""
        who = str(rec.get("who") or "").strip().lower()
        email = (request.headers.get("x-user-email") or "").strip().lower()
        if email and who and email == who:
            return True
        if rec.get("redeemed_by") and str(rec["redeemed_by"]) == str(subject):
            return True
        resolved = _email_subject_lookup(who) if who else None
        return bool(resolved and str(resolved) == str(subject))

    def _compose_and_mint(*, who: str, subject: str, kind: str, opening: str, fm: dict,
                          mid: str, mounts: list, group, refs_in, tabs, focus, provenance,
                          share_token=None) -> dict:
        """Build the record and mint it. ONE composition, both mint routes.

        Factored when the hand-link route landed: two routes composing the same record from the same
        helpers is how the halves of one contract drift, and the fields that would drift here are
        the ones that decide what an agent is told about a person."""
        refs = dict(refs_in or {})
        # THE RECIPIENT'S OWN ADDRESS IS A FACT OF THE TURN, and the derived domain is the only
        # anchor a first setup conversation has. Both are computed HERE, from `who`, rather than
        # asked of the caller: the mint already knows the address (it is the record's identity), and
        # a caller that had to pass them could pass a different pair than the one the record is
        # bound to. `domain` is "" for a placeholder like `.test` — see scaffolds.company_domain —
        # which the preset reads as "no signal", so it asks cold instead of naming a fake company.
        refs.setdefault("who", who)
        domain = scaffolds_mod.company_domain(who)
        if domain:
            refs.setdefault("domain", domain)
        refs["state"] = {"desk": scaffolds_mod.desk_state(wsr.root, subject) if subject else "new",
                         "group": scaffolds_mod.group_state(wsr.root, group)}
        return scaffolds.mint({
            "who": who,
            "kind": kind,
            "meeting": mid or None,
            "workspaces": mounts,
            "refs": refs,
            "opening": opening,
            "tabs": tabs if tabs is not None else scaffolds_mod.frontmatter_list(fm, "tabs"),
            "focus": focus if focus is not None else (fm.get("focus") or ""),
            "provenance": dict(provenance or {}),
            # THE SHARE LIVES ON THE RECORD, NOT IN THE LINK (R-A08). The recipient redeems it
            # against the scaffold id over an authenticated request; nothing puts it in a URL.
            "share_token": str(share_token) if share_token else None,
        })






    # ── ROUGH EDGES (PRD decision 33) ───────────────────────────────────────────────────────────
    def _friction_subject(request: Request) -> str:
        """Who filed it, BEST-EFFORT — never a refusal.

        Every other read on this service fails closed without `X-User-Id`, and this one must not.
        The rig's `report_friction` has always been documented NO ACCOUNT NEEDED, the worker that
        auto-files may have no principal, and the single most valuable report available is the one
        from a session so broken it has no identity left. **A friction report we cannot attribute is
        worth more than one that was never filed**, and refusing it would silence exactly the class
        of failure this loop exists to catch (ledger F70).

        The exposure is a write with no caller. It is bounded by the record itself: every field is
        length-capped in `shared/friction.py`, and the dedup key folds a flood of identical reports
        into ONE row with a counter."""
        return (request.headers.get("x-user-id") or "").strip()








    def _entity_mounts(subject: str) -> list:
        """`[{slug, path}]` for every workspace this subject has mounted — their own actives plus
        the shared ones their membership grants. Read for ONE purpose: to know which OTHER
        workspace already holds a page for a name, so the link into it can be written by id.

        Fails soft to an empty list, which is the single-workspace behaviour: an entity write must
        never fail because the mount table could not be read."""
        out: list = []
        try:
            mounts = active_workspaces(wsr.root, subject)
        except Exception:  # noqa: BLE001
            return out
        try:
            mounts = mounts + shared_active_mounts(wsr.root, subject, mindex.list(subject))
        except Exception:  # noqa: BLE001
            pass
        for m in mounts:
            if m.path:
                out.append({"slug": m.slug, "path": m.path})
        return out




    # ── workspace lifecycle (SCAFFOLD / TODO(phase-6)) — init from a validated template, swap which
    # validated workspace/template the next dispatch mounts. The seams exist downstream (seeding.seed_workspace
    # for init; VEXA_WORKSPACE_REPO/REF in dispatch/spawn for swap, bridge resolves per-meeting) — Phase 6
    # surfaces them here and wires the slim-client init_workspace()/use_workspace().

    # ── workspace identity + link resolution (PRD decision 26) ──────────────────────────────────
    #
    # "Hash ID to every workspace? workspaces interconnected together. If a workspace is not
    # available, it's okay — by design." (founder, 2026-09-02). Three reads, and the third one is
    # the product: a reader hands the server the refs in front of them and gets back, per ref, what
    # it points at NOW and whether they may open it.

    def _ws_is_member(root, slug, subject):
        """The membership check the access rule injects — the authoritative git roster, never the index."""
        return membership_mod.is_member(root, slug, subject)

    def _ws_sync(slug: str, **kw):
        """Re-point the registry at a workspace that just moved. Best-effort by design: a failure
        here costs a stale row that the next startup migration repairs, and it must never fail the
        act that moved the workspace."""
        try:
            return ids_mod.sync_workspace(wsr.root, slug, registry=workspace_registry, **kw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace-id sync failed for %s: %s: %s", slug, type(exc).__name__, exc)
            return None

    def _ws_here(request: Request, slug: Optional[str]):
        """The reader's CURRENT workspace record — the one an in-workspace `[[Title]]` resolves in.

        A slug the caller may not read resolves to None rather than to that workspace: the ref
        would otherwise be answered out of somebody else's tree because the READER named it."""
        subject = subject_of(request)
        rec = workspace_registry.by_slug(slug) if slug else workspace_registry.by_slug(str(subject))
        if rec is None and slug:
            rec = _ws_sync(slug)
        if rec is None and not slug:
            rec = _ws_sync(str(subject), kind="desk", owner=str(subject))
        if rec is None:
            return None
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        return rec if access == ids_mod.ACCESS_READABLE else None








    # ── the additive mount set (WP-A2.1): ACTIVE-SET membership over swap's park/restore machinery ──────















    # ── workspace membership + invites + roles (Lane M) ───────────────────────────────────────────
    # The access layer for SHARED workspaces. Authoritative store = policy/members.json + policy/
    # invites.json in the workspace's OWN git repo (PLATFORM-WRITE-ONLY, committed via
    # membership_mod.policy_commit); mirror = users.data.memberships[] over the injected index.
    # is_member(workspace_id, subject) -> role|None is the seam Lane A calls for mount/subscribe authz.
    def _pc(ws, message):
        return membership_mod.policy_commit(ws, message)

    def _member_error(exc: MembershipError):
        return HTTPException(status_code=exc.status, detail=str(exc))

    # ── LOADING AN EXISTING REPO (the "we already have a workspace on GitHub" path) ────────────────
    #
    # Two lanes, one mechanic. A person's own desk swaps through ``POST /api/workspace/swap``; a GROUP
    # workspace swaps through here. The difference that needs a route of its own is authorization: a
    # desk belongs to its subject, a group belongs to a member list, and replacing a group's tree is a
    # WRITE — so a viewer is refused, and the member list itself is carried across the swap by
    # ``attach_shared_workspace`` (it lives inside the tree being replaced).

    def _workspace_key(subject: str, slug: Optional[str]) -> str:
        """The deploy-key name for a target: a shared workspace keys by its id (the key belongs to the
        WORKSPACE, so every member's pull uses the same one), a person's desk by subject."""
        target = (slug or "").strip()
        if target and target != subject and membership_mod.is_member(wsr.root, target, subject) is not None:
            return deploy_keys_mod.workspace_key(workspace_id=target)
        return deploy_keys_mod.workspace_key(subject=subject)

    def _credential_refusal(detail: str, subject: str, slug: Optional[str], repo_url: str):
        """Turn git's "I do not know you" into the ONE action that fixes it. No box asking for a secret:
        the person adds OUR public key to THEIR repo, which is the whole point of the deploy-key model."""
        if not wcreds.is_auth_failure(detail):
            return HTTPException(status_code=502, detail=detail)
        try:
            prompt = wcreds.deploy_key_prompt(wsr.root, key=_workspace_key(subject, slug), repo_url=repo_url)
        except Exception:  # noqa: BLE001 — no ssh-keygen on this host; say the plain failure instead
            return HTTPException(status_code=502, detail=detail)
        return HTTPException(status_code=502, detail=f"{detail}\n\n{wcreds.prompt_sentence(prompt)}")









    # ── the COMPANY LAYER gate (PRD §9 decision 17; founder 2026-09-02) ──────────────────────────
    # A fresh instance serves nobody until an admin has written the thin company layer into
    # `_global`. agent-api is where the verification belongs because agent-api is the only service
    # that can SEE the store; admin-api holds the resulting value, and every service reads it from
    # there. Two verbs: look, and accept.

    def _global_store() -> Path:
        """The WRITABLE `_global` on this host. Two candidates because the deployment mounts the
        same bytes twice — the workspaces-dir copy (read-write in dev) and the host-path mirror
        (read-only) — and a writer that picks the wrong one fails at commit time with a permissions
        error that reads like a bug in git."""
        candidates = [Path(settings.workspaces_dir) / system_mounts.GLOBAL_SLUG,
                      Path(settings.global_system_workspace_path or "/nonexistent")]
        target = next((c for c in candidates if c.is_dir() and os.access(c, os.W_OK)), None)
        if target is None:
            target = next((c for c in candidates if c.is_dir()), None)
        if target is None:
            raise HTTPException(status_code=404, detail="the organisation tier is not present here")
        return target
















    # ── Settings → Models "Test" buttons (on-demand credential tests, fail-loud surface) ────────
    # Both test the caller's EFFECTIVE config — the same user > global > env resolution the
    # dispatch overlay / bot_spawn apply — so what's tested is what a turn/bot actually gets.



    # ── THE ROUTES, BY OWNER ─────────────────────────────────────────────────────────────────
    #
    # `create_app` was 2,868 lines and 78 routes, and every lane that touched agent-api touched
    # this one file — which is also where the identity hole lived (seam backlog B3). The routes
    # now live in `control_plane/routers/`, one module per owner, and this function is what it
    # says it is: build the app, build what the routes are built out of, include them.
    #
    # WHAT `build(**_deps)` IS FOR. Each handler closed over `create_app`'s locals — `wsr`,
    # `settings`, `subject_of`, `_read_target` and twenty more. Passing them in a bag and rebinding
    # each to THE NAME IT ALREADY HAD is what let every body move byte for byte: not one
    # identifier inside a handler changed, so `git diff -M` reads as a move and a reviewer is
    # reading the same code they reviewed before. Each router declares in its own `build()` which
    # of these it takes, so "what does this router depend on" is answerable by reading one line.
    #
    # ORDER IS NOT LOAD-BEARING HERE, and that is checked rather than assumed: no two of these 78
    # routes can match the same concrete URL under the same method (FastAPI resolves
    # first-match-wins, so a pair that could would make the include order a behaviour). The check
    # is `tests/test_route_table.py::test_no_two_routes_can_match_the_same_url`.
    _deps = dict(
        _clone_fn=_clone_fn, _compose_and_mint=_compose_and_mint,
        _credential_refusal=_credential_refusal, _email_subject_lookup=_email_subject_lookup,
        _entity_mounts=_entity_mounts, _friction_subject=_friction_subject,
        _global_root=_global_root, _global_store=_global_store,
        _internal_caller=_internal_caller, _manage_dir=_manage_dir,
        _meeting_owner_lookup=_meeting_owner_lookup, _member_error=_member_error, _pc=_pc,
        _read_target=_read_target, _repo=_repo, _require_shared_write=_require_shared_write,
        _resolve_room=_resolve_room, _scaffold_is_for=_scaffold_is_for,
        _scaffold_recipient_is=_scaffold_recipient_is, _scaffold_view=_scaffold_view,
        _schedule_source=_schedule_source, _workspace_key=_workspace_key, _ws_here=_ws_here,
        _ws_is_member=_ws_is_member, _ws_sync=_ws_sync, dispatcher=dispatcher,
        friction=friction, invocations_url=invocations_url, live=live, mindex=mindex,
        redis_url=redis_url, scaffolds=scaffolds, scheduler=scheduler, sess=sess,
        settings=settings, stream_reader=stream_reader, subject_of=subject_of,
        workspace_registry=workspace_registry, workspace_touches=workspace_touches, wsr=wsr)
    for _r in (routers_health, routers_chats, routers_admin, routers_meetings, routers_scaffolds, routers_friction, routers_workspaces):
        app.include_router(_r.build(**_deps))

    return app


# ── ASGI entrypoint (PEP 562) — `uvicorn control_plane.api:app` resolves this lazily ──────────────────
def _build_production_app() -> FastAPI:
    from shared.adapters import AdminApiMembershipIndex, AdminApiModelConfig, LocalIdentityMinter, RedisStreamReader, RuntimeHttpClient, SchedulerHttpClient
    from shared.config import load_settings
    from control_plane.config_preflight import preflight
    from control_plane.workspace_routines import start_workspace_routine_reconciler

    # ONE NAME for the internal tier (F95). The canonical key is INTERNAL_API_SECRET — the same
    # name compose, helm, admin-api, gateway and meeting-api use. VEXA_INTERNAL_API_SECRET still
    # resolves through the settings alias so an operator mid-upgrade is WARNED rather than silently
    # dropped into an unauthenticated internal tier; it is removed next release.
    if not (os.environ.get("INTERNAL_API_SECRET") or "").strip() and \
            (os.environ.get("VEXA_INTERNAL_API_SECRET") or "").strip():
        logger.warning(
            "VEXA_INTERNAL_API_SECRET is DEPRECATED — rename it to INTERNAL_API_SECRET, the one "
            "name the whole internal tier uses (compose/helm secret key, admin-api, gateway, "
            "meeting-api). The prefixed spelling is honoured this release and removed in the next."
        )

    # config.v1 boot preflight (ADR-0026): INTERNAL_API_SECRET is required-explicit and must not
    # hold a published placeholder, so an internal tier that was never configured refuses to boot
    # rather than believing a secret printed in this repository (F95). The run also logs the
    # capability tri-states (bot_gateway · model_inference) — a deploy that cannot add bots from URL
    # or whose workers will have NO model credentials says so in the boot log and on /health,
    # instead of failing at first chat with 'Model inference failed: Not logged in'.
    preflight()

    settings = load_settings()
    runtime = RuntimeHttpClient(settings.runtime_api_url)
    scheduler = SchedulerHttpClient(settings.runtime_api_url)
    identity = LocalIdentityMinter(settings.dispatch_signing_key.get_secret_value())
    invocations_url = settings.agent_api_self_url.rstrip("/") + "/invocations"
    # Lane M: the membership index mirror (users.data.memberships[]) over the admin-api internal edge.
    # Empty admin_api_url → the in-memory index (git files stay authoritative; only "shared with me"
    # listing is degraded, per Q6). create_app defaults to InMemoryMembershipIndex when None is passed.
    membership_index = None
    model_config = None
    if settings.admin_api_url:
        membership_index = AdminApiMembershipIndex(
            settings.admin_api_url, settings.internal_api_secret.get_secret_value(),
        )
        # Settings → Models: per-subject effective model config (user pref > platform setting)
        # over the same internal edge; None (no admin-api) → deployment env defaults only.
        model_config = AdminApiModelConfig(
            settings.admin_api_url, settings.internal_api_secret.get_secret_value(),
        )
    # Lane A: the Dispatcher takes the SAME index so shared workspaces the subject is a member of enter
    # the dispatch mount set (read-only for Slice 1), not just the /active listing.
    dispatcher = Dispatcher(settings, runtime, identity, membership_index=membership_index,
                            model_config=model_config)
    app = create_app(
        dispatcher,
        stream_reader=RedisStreamReader(settings.redis_url),
        reader=WorkspaceReader(settings.workspaces_dir),
        scheduler=scheduler,
        invocations_url=invocations_url,
        redis_url=settings.redis_url,
        membership_index=membership_index,
    )
    app.state.workspace_routine_reconciler = start_workspace_routine_reconciler(
        scheduler=scheduler,
        invocations_url=invocations_url,
        workspaces_dir=settings.workspaces_dir,
        interval_sec=settings.routine_reconcile_interval_sec,
    )

    @app.on_event("shutdown")
    def _stop_workspace_routine_reconciler() -> None:
        handle = getattr(app.state, "workspace_routine_reconciler", None)
        if handle is not None:
            handle.stop()

    # The in-process meetings Integration (replaces the standalone bridge container): a daemon thread
    # tails transcription_segments → registers the live meeting on activity.
    # NOTE: no `subject=` → the watcher uses its PRE-M2 `u_live` placeholder; live-meeting dispatch (M2)
    # must pass the real meeting owner here (see transcription_watcher.start).
    from control_plane import transcription_watcher
    transcription_watcher.start(settings.redis_url, dispatcher, app.state.live_meetings)

    # `_global` gets its history BEFORE its first writer, never after. It shipped as a bare
    # directory that was mounted into every worker and read on every turn, with nothing recording
    # who changed it or what it said yesterday — and one admin edit changes how every agent in the
    # deployment behaves. Best-effort: a store that is read-only here (the host-path mirror) is a
    # legitimate deployment shape, and it must not stop the service from booting.
    try:
        global_layer.ensure_repo(Path(settings.workspaces_dir) / system_mounts.GLOBAL_SLUG)
    except Exception as exc:  # noqa: BLE001
        logger.info("the organisation tier is not a git repo here and could not be made one: %s", exc)
    return app


def __getattr__(name: str):
    if name == "app":
        return _build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
