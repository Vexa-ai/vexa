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

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
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
from shared.agent_config import default_meeting_model, load_meeting_config
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
from control_plane import global_layer
from control_plane import system_mounts
from control_plane import scaffolds as scaffolds_mod
from control_plane.workspace_membership import MembershipError, MembershipIndex, InMemoryMembershipIndex
from control_plane.dispatch import Dispatcher
from control_plane.events import event_to_invocation
from shared.ports import SchedulerPort, StreamReader
from control_plane.workspace_reader import WorkspaceReader

logger = logging.getLogger("agent_api.api")

# The phase, in the meeting's own vocabulary — the same three words the terminal's header uses
# (`minutes/MinutesShell.tsx` PHASE_WORD). One vocabulary, two renderers.
_PHASE_WORD = {"prep": "upcoming", "live": "live", "post": "held"}


def _iso(epoch) -> "str | None":
    """An epoch as an ISO-8601 UTC string, or None. The wire carries times as STRINGS because the
    client half pins them that way; the store keeps the float, which is what arithmetic wants."""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError):
        return None


def _provenance_line(prov: dict, minted_at) -> str:
    """The record's provenance as ONE readable line — what produced this touch, and when.

    The OBJECT stays on the wire beside it: flow, reaction id, run id and minted_by are four facts,
    and a string that concatenates four facts is not a record of them. This line exists so a panel
    can show something without parsing, not so the object can be dropped."""
    if not isinstance(prov, dict):
        return ""
    bits = [str(prov[k]) for k in ("flow", "reaction_id", "run_id") if prov.get(k)]
    if prov.get("minted_by"):
        bits.append(f"minted by {prov['minted_by']}")
    stamp = _iso(minted_at)
    if stamp:
        bits.append(stamp)
    return " · ".join(bits)


def _epoch_text(when) -> str:
    """An epoch as a readable UTC line, or "" — the last-resort rendering of a meeting's time when
    the caller did not render one in the recipient's own zone. UTC and SAID to be UTC: a bare
    "14:00" in a zone nobody named is the kind of half-fact that reads as a bug in an inbox."""
    try:
        return time.strftime("%a %d %b %H:%M UTC", time.gmtime(float(when)))
    except (TypeError, ValueError):
        return ""

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MEETING_STREAM_TRANSCRIPT_REPLAY = 80
MEETING_STREAM_OUTPUT_REPLAY = 160
# How long the SSE keeps draining after session_end when the copilot HAS written notes but its
# view_end marker hasn't arrived (the final beat is ~10s of LLM; a dead worker never marks) —
# the bounded cap that replaces the old one-empty-poll guess (ADR 0027).
MEETING_STREAM_ENDING_CAP_SEC = 45.0


def _upload_filename(name: str | None) -> str:
    base = (name or "upload").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")
    return base[:160] or "upload"


def _truncate_title(text: str, *, limit: int = 60) -> str:
    """A session's default title — the first prompt, single-lined + truncated."""
    title = " ".join((text or "").split())
    return title[: limit - 1] + "…" if len(title) > limit else title


def _stream_tail_id(redis_url: str | None, stream: str) -> str | None:
    if not redis_url:
        return None
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrevrange(stream, count=1)
        return str(rows[0][0]) if rows else "0-0"
    except Exception as exc:
        logger.warning("could not resolve transcript stream tail for %s: %s", stream, exc)
        return None


# How long a turn's start-cursor record lives — covers the client's whole resume window (its hard
# timeout is minutes); after this a stale nonce simply falls back to the fresh-dispatch path.
CHAT_TURN_HEAD_TTL_SEC = 900


def _chat_turn_head_key(unit_id: str) -> str:
    return f"unit:{unit_id}:turnhead"


def _record_chat_turn_head(redis_url: str | None, unit_id: str, turn_id: str, start: str) -> None:
    """Remember, per warm chat unit, the CURRENT turn's nonce + the out-Stream id it started after.
    Best-effort (redis-less unit tests skip it): losing the record only degrades a no-cursor retry
    back to today's behavior, it never breaks the turn."""
    if not redis_url:
        return
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        r.set(_chat_turn_head_key(unit_id), json.dumps({"turn_id": turn_id, "start": start}),
              ex=CHAT_TURN_HEAD_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record chat turn head for %s: %s", unit_id, exc)


def _chat_turn_head(redis_url: str | None, unit_id: str, turn_id: str) -> str | None:
    """The recorded start cursor of the turn ``turn_id`` — or None when it isn't the current turn."""
    if not redis_url or not turn_id:
        return None
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        raw = r.get(_chat_turn_head_key(unit_id))
        head = json.loads(raw) if raw else None
        return head["start"] if head and head.get("turn_id") == turn_id else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read chat turn head for %s: %s", unit_id, exc)
        return None


class _Sessions:
    """Durable, per-subject chat-session index. Each session carries a created + last-active stamp and an
    optional title (default the first prompt, truncated). ``list`` returns them most-recent first.

    Backed by redis when a client is wired (one hash per session under ``agent:sessions:<subject>`` +
    the per-subject id set), with an in-memory fallback so the unit tests need no redis. Multiple
    conversation threads live in the ONE user workspace — this indexes the threads, not workspaces."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._mem: dict[str, dict[str, dict]] = {}  # subject → {session → {created,last_active,title}}

    # ── redis key helpers ──
    @staticmethod
    def _ids_key(subject: str) -> str:
        return f"agent:sessions:{subject}"

    @staticmethod
    def _meta_key(subject: str, session: str) -> str:
        return f"agent:session:{subject}:{session}"

    def _now(self) -> float:
        import time

        return time.time()

    def upsert(self, subject: str, session: str, *, title: str | None = None) -> None:
        """Record the session on use: create it (stamping ``created`` + a default ``title``) or touch its
        ``last_active``. An explicit ``title`` overrides; otherwise the first prompt seeds it once."""
        now = self._now()
        if self._redis is not None:
            mkey = self._meta_key(subject, session)
            existing = self._redis.hgetall(mkey) or {}
            fields = {"last_active": str(now)}
            if not existing:
                fields["created"] = str(now)
                fields["title"] = title or session
            elif title is not None:
                fields["title"] = title
            self._redis.hset(mkey, mapping=fields)
            self._redis.sadd(self._ids_key(subject), session)
            return
        rec = self._mem.setdefault(subject, {}).get(session)
        if rec is None:
            self._mem[subject][session] = {"created": now, "last_active": now, "title": title or session}
        else:
            rec["last_active"] = now
            if title is not None:
                rec["title"] = title

    def list(self, subject: str) -> list[dict]:
        """The subject's sessions, most-recently-active first."""
        rows: list[dict] = []
        if self._redis is not None:
            for session in self._redis.smembers(self._ids_key(subject)) or set():
                meta = self._redis.hgetall(self._meta_key(subject, session)) or {}
                rows.append({
                    "session": session,
                    "title": meta.get("title") or session,
                    "created": float(meta.get("created", 0) or 0),
                    "last_active": float(meta.get("last_active", 0) or 0),
                })
        else:
            for session, meta in self._mem.get(subject, {}).items():
                rows.append({
                    "session": session, "title": meta.get("title") or session,
                    "created": meta.get("created", 0.0), "last_active": meta.get("last_active", 0.0),
                })
        rows.sort(key=lambda r: r["last_active"], reverse=True)
        return rows

    def drop(self, subject: str, session: str) -> None:
        if self._redis is not None:
            self._redis.srem(self._ids_key(subject), session)
            self._redis.delete(self._meta_key(subject, session))
            return
        self._mem.get(subject, {}).pop(session, None)


# P21 (ADR 0027 family — the panel's stale-live finding): a registry entry is "live" only while
# segments actually FLOW. The watcher re-adds on every batch (~2s apart), so this much silence
# means the meeting is over even when the session_end frame was lost (e.g. a hot-reload racing the
# wire leaves it pending-unacked) — the server-side stale-"live" the terminal's durableTerminal
# guard was papering over.
LIVE_SILENCE_TTL_SEC = 60.0

# The processing desired-state flag's set-time backstop TTL (the watcher refreshes a rolling TTL per
# armed batch while segments flow — transcription_watcher.PROC_FLAG_ROLLING_TTL_SEC). Bounds a flag
# whose meeting never produces a segment; generous because the toggle is only offered on live rows.
PROC_FLAG_BACKSTOP_TTL_SEC = 4 * 3600


class _LiveMeetings:
    """In-memory registry of meeting copilots — the terminal's 'meetings' feed. Keyed by session_uid (the
    native Meet code). A stopped/ended meeting is KEPT (``status='stopped'``) so the terminal can offer to
    send the bot back; ``add`` (re)marks it live. Liveness is EVIDENCE, not a latch: ``add`` stamps
    ``last_seen`` and ``list`` demotes an entry silent past LIVE_SILENCE_TTL_SEC to stopped (P21 —
    absence of the expected signal is itself a reportable state). The dev-tier foundation."""

    def __init__(self) -> None:
        self._by_uid: dict[str, dict] = {}

    def add(self, meeting: dict) -> None:
        m = dict(meeting)
        m["status"] = "live"
        m["last_seen"] = time.monotonic()
        self._by_uid[meeting["session_uid"]] = m

    def stop(self, session_uid: str) -> None:
        m = self._by_uid.get(session_uid)
        if m:
            m["status"] = "stopped"

    def drop(self, session_uid: str) -> None:
        # the meeting ended — keep the row (stopped) so 'send the bot back' stays available
        self.stop(session_uid)

    def list(self) -> list[dict]:
        now = time.monotonic()
        for m in self._by_uid.values():
            if m.get("status") == "live" and now - m.get("last_seen", now) > LIVE_SILENCE_TTL_SEC:
                m["status"] = "stopped"  # earned liveness expired — the segment flow went silent
        return list(self._by_uid.values())


class ChatContextBody(BaseModel):
    """The terminal-state CONTEXT BUNDLE (slice 1). ``extra="ignore"`` on purpose — forward-
    tolerant: a newer terminal adding bundle fields must never 422 against this server."""
    model_config = {"extra": "ignore"}
    tz: Optional[str] = None            # IANA tz for digest rendering (invalid → UTC)
    surface: Optional[dict] = None      # {list?: str, tab?: {kind: str}} — the ambient gate signal
    focus: Optional[dict] = None        # the focused thing (meeting/file/workspace/today); None = cleared
    include: Optional[dict] = None      # {schedule?: bool} — explicit user toggle beats the gate


class ChatBody(BaseModel):
    model_config = {"extra": "forbid"}
    prompt: str
    # subject is DERIVED server-side from X-User-Id (P20) — kept here only so a client that still sends it
    # doesn't 422 (extra=forbid); the value is IGNORED. Dropped from the client in Stage 4.
    subject: Optional[str] = None
    session: Optional[str] = None
    # LEGACY single-focus grounding ({kind, ref}) — still honored when ``context`` is absent, so
    # old clients keep byte-identical behavior. The terminal now sends ``context`` (below) too.
    active: Optional[dict] = None
    # the terminal-state context bundle; when present it is AUTHORITATIVE (including
    # ``focus: null`` = the user cleared the focus chip — legacy ``active`` is then ignored).
    context: Optional[ChatContextBody] = None
    # Client-minted TURN NONCE (one per user turn, constant across that turn's reconnect attempts).
    # Lets the server tell a no-cursor RETRY (the stream dropped before the client ever saw an ``id:``,
    # so it can't send Last-Event-ID) from a genuinely new turn with identical text ("yes" twice):
    # a matching nonce re-attaches from the turn's recorded start — no second dispatch, no lost events.
    turn_id: Optional[str] = None
    # THE MEETING ROOM (post-meeting run). The caller may name ONLY THE MEETING — a meetings-domain
    # ROW id — and the server resolves who was in it (control_plane/meeting_room.py). There is
    # deliberately NO field here that names a workspace or a subject: a caller able to say "mount
    # u_bob" could read any user's desk by naming it, which is the exact hole this shape exists to
    # close. Accepting it additionally requires the internal-tier secret (see ``_resolve_room``), so
    # the general end-user chat surface cannot open a room at all.
    room_meeting_id: Optional[str] = None
    # MEMBERSHIP = the INVITE's participant list, as ADDRESSES. The trusted caller holds the parsed
    # invite, so it sends the addresses; agent-api resolves each one to a subject through admin-api
    # and mounts only participants who ALREADY have a subject and a desk. Addresses, never subject
    # ids and never workspace names: the resolution — and therefore the blast radius — stays here.
    room_participants: Optional[list[str]] = None
    # The invite's ICS ``CN=`` map (address → display name). Used ONLY to match transcript speaker
    # labels back to addresses that are ALREADY in the list above, i.e. only to ORDER the room. A
    # name never admits anybody, so a bad match costs position and nothing else.
    room_participant_names: Optional[dict[str, str]] = None
    # The transcript's speaker labels, already ordered by speaking time DESCENDING (the caller holds
    # the transcript, so it does that arithmetic). Unmatched labels are simply ignored.
    room_speakers: Optional[list[str]] = None
    room_read_max: Optional[int] = None
    # THE SCAFFOLD (PRD 5.5). The terminal sends this on the FIRST turn of a chat a link composed.
    # It is an ID, never a mount list and never prompt text: the record it names says which
    # workspaces this turn mounts and what the opening ask is, and the server reads BOTH from the
    # store. A scaffold that is not this subject's is ignored (never an error) — a stale or
    # forwarded id must not be able to widen anybody's mounts, and must not break their turn either.
    scaffold_id: Optional[str] = None


class ScaffoldMintBody(BaseModel):
    """`POST /internal/scaffolds` — what a FLOW says at the moment it creates a touch.

    ``extra="forbid"``: a mint is the thing a step checks before it sends, so a caller that spells a
    field wrong has to hear about it here rather than mail a link built from a field nobody read.

    There is deliberately NO field carrying prompt text. ``opening`` is a NAME in `_global/asks/`
    and the server refuses anything that is not one — the URL never carried text (PRD 6) and neither
    does the record behind it, for the same reason: anyone who can mint a touch would otherwise be
    able to drive the recipient's agent."""
    model_config = {"extra": "forbid"}
    who: str                                   # the RECIPIENT ADDRESS. Not a subject: they may not exist yet.
    kind: str                                  # one of scaffolds.KINDS
    opening: str                               # a preset NAME in _global/asks/, never text
    meeting: Optional[str] = None              # the meetings-domain ROW id, or None. PHASE IS NOT STORED.
    workspaces: Optional[list[str]] = None     # slugs to mount; None = derived (see the route)
    refs: Optional[dict] = None                # the facts for the agent: title, when, organizer, participants…
    tabs: Optional[list[str]] = None           # UI half; None = the preset's own `tabs:`
    focus: Optional[str] = None                # UI half; None = the preset's own `focus:`
    # The RESTRICTED transcript share, minted by the caller against the meeting ROW
    # (`flows_steps/meeting.mint_transcript_share`) when the meeting is not the recipient's own.
    # Minted THERE and not here because the mint needs the meeting OWNER's gateway key, which flows
    # holds and agent-api deliberately does not — see the route's docstring.
    share_token: Optional[str] = None
    provenance: Optional[dict] = None          # flow, reaction/run id, minted_by (the fact's admitted_by)


class ResetBody(BaseModel):
    """Body for POST /api/chat/reset — the docs (api/agent.mdx) say it's just ``{session?}``. reset only
    needs the session; ``prompt``/``subject``/``active`` are accepted-and-ignored so a client reusing the
    chat-body shape doesn't 422 (reset must NOT require a prompt the way the chat turn does)."""
    model_config = {"extra": "forbid"}
    session: Optional[str] = None
    subject: Optional[str] = None
    prompt: Optional[str] = None
    active: Optional[dict] = None
    context: Optional[ChatContextBody] = None  # accepted-and-ignored, same rationale
    room_meeting_id: Optional[str] = None      # accepted-and-ignored (reset mounts nothing)


class RoutineCreate(BaseModel):
    """The Routines surface / ``/routine`` create form — compiles to a routine.v1 + a schedule.v1 job."""
    model_config = {"extra": "forbid"}
    subject: Optional[str] = None  # DERIVED from X-User-Id (P20); ignored if sent. Dropped client-side in Stage 4.
    name: str
    cron: str
    prompt: str
    run_now: bool = True  # fire one immediate run so the author sees a result without waiting for cron


class RoutineEnabledPatch(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool


class WorkspaceSwapBody(BaseModel):
    """Attach a custom external git repo as the subject's workspace. Omit ``repo`` to swap back to seed."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL to clone (None → swap back to the seeded default)
    ref: Optional[str] = None    # branch/tag/sha to check out (defaults to main)
    slug: Optional[str] = None   # target a parked slot DIRECTLY (e.g. a no-repo backup) — restores, no re-clone
    fresh: bool = False          # swap-to-seed only: rebuild the default from template (start fresh) vs restore the park
    token: Optional[str] = None  # access token for a PRIVATE repo — used for the clone only, never stored (P15)


class WorkspacePublishBody(BaseModel):
    """Publish the subject's vexa-born workspace to GitHub — create the repo (unless ``remote_url``
    targets a pre-created one) and push the current branch's full history. ``token`` is the caller's
    PAT, used server-side for this call only, NEVER stored (P15)."""
    model_config = {"extra": "forbid"}
    repo_name: Optional[str] = None    # name of the repo to create (required unless remote_url is given)
    private: bool = True               # create the repo private (default) or public
    token: Optional[str] = None        # GitHub PAT (repo-creation + push); OPTIONAL — falls back to the caller's SAVED token
    org: Optional[str] = None          # create under this org instead of the user's account
    remote_url: Optional[str] = None   # skip creation and push to this (pre-created/empty) repo
    slug: Optional[str] = None         # target workspace (own slot or shared membership); omitted = the seed-slot workspace


class WorkspaceRenameBody(BaseModel):
    """Set a workspace slot's DISPLAY name (label only — the slug/parked dir are unchanged). Empty clears it."""
    model_config = {"extra": "forbid"}
    slug: str
    name: Optional[str] = None


class WorkspacePushBody(BaseModel):
    """Push a workspace's current branch to its GitHub home (origin / vexa-publish), fast-forward only.
    ``slug`` targets one of the caller's workspaces (default = the primary); ``token`` is the caller's PAT.
    OPTIONAL — when omitted, the caller's SAVED reusable GitHub token (git_credentials) is used. Whichever
    token applies is used for this push only and NEVER stored on the workspace remote (P15)."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class GitTokenBody(BaseModel):
    """Save (or, with an empty/omitted ``token``, CLEAR) the caller's reusable GitHub token — stored ONCE,
    server-side, and reused as the fallback credential for every git op across all their repos."""
    model_config = {"extra": "forbid"}
    token: Optional[str] = None


class WorkspacePullBody(BaseModel):
    """Fetch + fast-forward a workspace from its GitHub home. ``slug`` targets one of the caller's
    workspaces (default = primary); ``token`` (optional — public repos need none) is used for the fetch
    only and NEVER stored (P15). A divergence is refused, not merged/rebased/forced."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    token: Optional[str] = None


class WorkspacePurposeBody(BaseModel):
    """Set a workspace's PURPOSE — a one-line statement of what it's for, stored IN the workspace so it
    travels when shared and is read into the agent's mount preamble. ``slug`` targets one of the caller's
    workspaces (default = primary); an empty ``purpose`` clears it."""
    model_config = {"extra": "forbid"}
    slug: Optional[str] = None
    purpose: str = ""


class InviteCreateBody(BaseModel):
    """Mint a scoped invite for a shared workspace (owner/contributor only). Returns the token ONCE."""
    model_config = {"extra": "forbid"}
    workspace_id: str
    role: str = "viewer"                 # viewer | contributor (never owner)
    expires_in_sec: int = 604800         # 7 days
    max_uses: int = 1
    mode: str = "open"                   # open (anyone-with-link) | restricted (allowed_emails only)
    allowed_emails: Optional[list[str]] = None  # restricted mode: the verified emails permitted to redeem


class InviteAcceptBody(BaseModel):
    """Redeem an invite token (any logged-in user). Idempotent per user."""
    model_config = {"extra": "forbid"}
    token: str


class RoleSetBody(BaseModel):
    """Flip a member's role (owner only) — the "change read/write permissions" DoD item."""
    model_config = {"extra": "forbid"}
    role: str                            # viewer | contributor | owner


class SharedNewBody(BaseModel):
    """CREATE a new shared workspace (top-level, caller becomes owner) — the bootstrap that makes a
    workspace shareable so invites can be minted against it. ``name`` → display + workspace-id base."""
    model_config = {"extra": "forbid"}
    name: str = "Shared workspace"


class SharedAttachBody(BaseModel):
    """LOAD AN EXISTING REPO into a SHARED (group) workspace — the group counterpart of
    ``POST /api/workspace/swap``. The group's current tree is PARKED (kept, swappable-back), the repo is
    cloned in, and the member list is carried across so nobody loses access.

    ``token`` is the terminal's optional per-call PAT for an https repo; the MCP path never sends one —
    an ssh repo authenticates with the workspace's deploy key, resolved server-side."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL (ssh → deploy key; https → PAT). None + ``slug`` = swap back
    ref: Optional[str] = None    # branch/tag/sha (defaults to main)
    slug: Optional[str] = None   # a parked slot to restore DIRECTLY (no re-clone), e.g. "seed"
    token: Optional[str] = None  # https only, used for the clone and never stored (P15)


class SharedActiveBody(BaseModel):
    """Switch a shared workspace ON (mount) or OFF (hide) in the caller's active set — per-user, membership
    is unchanged."""
    model_config = {"extra": "forbid"}
    active: bool


class ArchiveBody(BaseModel):
    """Archive (collapse, keep) or un-archive one of the caller's own workspaces."""
    model_config = {"extra": "forbid"}
    archived: bool = True


class WorkspaceActivateBody(BaseModel):
    """ADD a workspace to the subject's active set (the additive mount set — WP-A2.1). Pass ``repo`` to
    clone/restore a git repo, or ``slug`` to activate an already-parked slot. Unlike swap it does NOT park
    the others — the private baseline and any other active workspaces stay mounted."""
    model_config = {"extra": "forbid"}
    repo: Optional[str] = None   # git URL to clone (first time) / restore (thereafter)
    ref: Optional[str] = None    # branch/tag/sha (defaults to main)
    slug: Optional[str] = None   # activate an already-parked slot directly (no repo needed)
    token: Optional[str] = None  # access token for a PRIVATE repo — clone only, never stored (P15)


class WorkspaceNewBody(BaseModel):
    """CREATE a brand-new BLANK workspace (seeded from the template) at a fresh slug and ADD it to the
    active set — the additive-model "new workspace" action. NOT a swap: nothing is parked/rebuilt/backed
    up. ``name`` (optional) → the new workspace's display label (default a unique "New workspace")."""
    model_config = {"extra": "forbid"}
    name: Optional[str] = None


class WorkspaceDeactivateBody(BaseModel):
    """REMOVE a workspace from the active set (park it — never destroyed). The private baseline cannot be
    deactivated (it is the subject's durable memory root)."""
    model_config = {"extra": "forbid"}
    slug: str


class MeetingStart(BaseModel):
    """Launch a live-meeting copilot for a REAL meeting. The vexa-cloud bridge POSTs this once it has a
    bot in the meeting; the dispatch then tails ``tc:meeting:{native_id}`` (the stream the bridge feeds)."""
    model_config = {"extra": "forbid"}
    platform: str               # google_meet | teams | zoom
    native_id: str              # the platform meeting id (e.g. a Google Meet code abc-defg-hij)
    subject: Optional[str] = None  # DERIVED from X-User-Id (P20); ignored if sent.
    title: Optional[str] = None


class MeetingProcess(BaseModel):
    """Toggle copilot PROCESSING for a meeting. on=false → no processing (raw transcript only);
    on=true → process the meeting (full-history backfill the first time, else resume live)."""
    model_config = {"extra": "forbid"}
    native_id: str
    platform: str = "google_meet"
    on: bool
    # P0 (cross-tenant leak fix): the meetings-domain ROW id (unique per meeting run). When the terminal
    # knows it (POST /bots returns it), the copilot's opt-in flag + cursor + processed stream key on it —
    # so a re-sent bot on the same native link, or a DIFFERENT tenant on the same link, can never
    # arm/clobber/read another meeting's processing. Falls back to native only when absent (legacy).
    meeting_id: Optional[str] = None
    subject: Optional[str] = None  # DERIVED from X-User-Id (P20); ignored if sent.


# The meeting copilot's start brief. The in-container worker drives per-beat extraction with its own
# CARD_PROMPT; this is the envelope's entrypoint (continuity = the session file in the workspace).
_MEETING_BRIEF = (
    "You are the live meeting copilot. Watch the meeting transcript as it streams in and surface the "
    "people, companies, products, and projects worth tagging."
)


def _encode_sse_cursor(last: dict, tkey: str, okey: str, pkey: str | None = None) -> str:
    """Pack the per-stream redis cursors into ONE SSE event id (the browser echoes it as
    Last-Event-ID on reconnect → we resume EXACTLY from here, gapless). '-' = not-yet-read.
    Three parts since ADR 0027 (transcript|output|processed); the third is the proc-stream cursor."""
    parts = [last.get(tkey, "-"), last.get(okey, "-")]
    if pkey is not None:
        parts.append(last.get(pkey, "-"))
    return "|".join(str(p) for p in parts)


def _decode_sse_cursor(raw: str | None) -> "tuple[str | None, str | None, str | None]":
    """Last-Event-ID → (transcript_id, output_id, processed_id). None when absent/malformed (fresh
    connect). PAD-tolerant: a pre-ADR-0027 two-part id decodes with processed_id None — the caller
    replays the proc stream from the start (notes upsert by id client-side, so replay is idempotent
    and never drops the reconnect gap)."""
    if not raw or "|" not in raw:
        return (None, None, None)
    parts = (raw.split("|") + [None, None, None])[:3]
    return tuple(p if p and p != "-" else None for p in parts)  # type: ignore[return-value]


def _sse(events) -> Iterator[str]:
    for item in events:
        # ``None`` is the reader's idle tick → an SSE comment keepalive. Proxies (and the client's own
        # idle-stall detector) cut a byte-silent stream in ~18-30s; a long agent think is byte-silent for
        # minutes. The comment is invisible to EventSource parsing but keeps bytes flowing.
        if item is None:
            yield ": keepalive\n\n"
            continue
        # Each item is either a bare event dict, or (event, sse_id) — the id makes reconnects resumable.
        ev, sid = item if isinstance(item, tuple) else (item, None)
        prefix = f"id: {sid}\n" if sid else ""
        yield f"{prefix}data: {json.dumps(ev)}\n\n"


def _has_custom_model_endpoint(cfg: dict) -> bool:
    """True iff a per-user Settings → Models config actually delivers a credential to the worker.
    Mirrors overlay_model_config's inertness rule (dispatch.py): only ``mode=custom`` WITH a
    ``base_url`` stamps auth env; ``api_key`` is optional (a keyless local gateway is legitimate)."""
    return (cfg.get("mode") or "").strip() == "custom" and bool((cfg.get("base_url") or "").strip())


def _model_creds_error_message() -> str:
    keys = ", ".join(missing_capability_keys("model_inference"))
    return (
        "No model credentials are configured, so the agent cannot run. "
        f"Set one of {keys} in the deployment environment "
        "(deploy/compose/.env for the compose stack, then `make all`), "
        "or add a custom endpoint under Settings → Models."
    )


MEETING_CHAT_TRANSCRIPT_SEGMENTS = 400  # bound the live transcript folded into a meeting-chat prompt


def _fold_meeting_transcript(redis_url: "str | None", stream_key: str, *, limit: int) -> str:
    """Fold the live transcript Stream ``tc:meeting:{stream_key}`` — the SAME stream the meeting copilot
    tails (worker/meeting.py) and the terminal renders — into ordered ``speaker: text`` lines for chat
    grounding. ``stream_key`` is the meetings-domain ROW id (P0 cross-tenant leak fix: the carrier keys
    on the row id, never the native id which collides across tenants/re-sends). Refining live drafts are
    upserted by ``segment_id`` (latest text wins, no duplicate), arrival order preserved, bounded to the
    last ``limit`` segments. Best-effort: returns "" when redis is unwired or the stream is empty."""
    if not redis_url:
        return ""
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrange(f"tc:meeting:{stream_key}")
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort; never fail the chat turn
        logger.warning("could not read transcript for %s: %s", stream_key, exc)
        return ""
    order: list[str] = []
    seg_by_id: dict[str, dict] = {}
    for entry_id, fields in rows:
        payload = json.loads(fields.get("payload", "{}"))
        if payload.get("type") == "session_end":
            continue
        for i, seg in enumerate(payload.get("segments", [])):
            sid = str(seg.get("segment_id") or f"{entry_id}:{i}")
            if sid not in seg_by_id:
                order.append(sid)
            seg_by_id[sid] = seg
    lines: list[str] = []
    for sid in order[-limit:]:
        seg = seg_by_id[sid]
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = (seg.get("speaker") or "Speaker").strip()
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _fold_meeting_processed(redis_url: "str | None", stream_key: str, *, limit: int) -> str:
    """Fold the PROCESSED-notes Stream ``proc:meeting:{stream_key}`` (processed-notes.v1 — the copilot's
    cleaned transcript; single writer worker/meeting.py) into ordered ``speaker: text`` lines for
    post-meeting chat grounding. Notes upsert by id (a refining pass upgrades in place), the ``view_end``
    terminal marker is skipped, order preserved, bounded to the last ``limit`` notes. Best-effort:
    returns "" when redis is unwired, the stream is empty, or entries are malformed."""
    if not redis_url:
        return ""
    try:
        import redis

        r = redis.from_url(redis_url, decode_responses=True)
        rows = r.xrange(f"proc:meeting:{stream_key}")
    except Exception as exc:  # noqa: BLE001 — grounding is best-effort; never fail the chat turn
        logger.warning("could not read processed notes for %s: %s", stream_key, exc)
        return ""
    order: list[str] = []
    note_by_id: dict[str, dict] = {}
    for entry_id, fields in rows:
        if fields.get("type") == "view_end":
            continue
        raw = fields.get("note")
        if not raw:
            continue
        try:
            note = json.loads(raw)
        except (TypeError, ValueError):
            continue
        nid = str(note.get("id") or entry_id)
        if nid not in note_by_id:
            order.append(nid)
        note_by_id[nid] = note
    lines: list[str] = []
    for nid in order[-limit:]:
        note = note_by_id[nid]
        text = (note.get("text") or "").strip()
        if not text:
            continue
        speaker = (note.get("speaker") or "Speaker").strip()
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _meeting_grounding(
    active: "dict | None", session: str, prompt: str, redis_url: "str | None"
) -> "tuple[dict, list[str], str]":
    """Cookbook #1 — chat grounding in the terminal's ACTIVE meeting, branched by the meeting's
    LIFECYCLE PHASE (design-spec meeting-lifecycle-v2, W4; steering templates + the _global override
    live in control_plane/meeting_steering.py):

      prep (idle/scheduled)          — no transcript fold (none exists); steer toward preparation,
                                       naming the bound prep workspace when the client sent one.
      live (default; absent status)  — fold ``tc:meeting:{row}`` fresh on every turn — a legacy
                                       client that sends no status keeps exactly this behavior.
      post (completed/failed/stopped)— fold the PROCESSED notes ``proc:meeting:{row}``; fall back to
                                       the raw transcript; if neither exists say so plainly (fail loud,
                                       never fabricate).

    The transcript reaches the agent the SAME way the live copilot gets it: the meeting's redis Stream
    (the meetings⊥agent seam) — NOT a file, NOT a cross-domain HTTP call, NO token. Returns the plain
    (none-context, no tools, prompt) when the active tab isn't a meeting."""
    a = active or {}
    if a.get("kind") != "meeting":
        return ({"kind": "none", "session": session}, [], prompt)
    m = a.get("meeting") or a  # tolerate {kind, meeting:{…}} or a flat {kind, platform, native_id}
    native = m.get("native_id") or m.get("ref")
    if not native:
        return ({"kind": "none", "session": session}, [], prompt)
    platform = m.get("platform") or "google_meet"
    # A chat turn (trigger "message"), not a live-meeting serve — the transcript travels in the prompt,
    # so the dispatch context stays plain (no meeting env / serve path is engaged for a chat).
    ctx = {"kind": "none", "session": session}
    # P0 (cross-tenant leak fix): read the streams by the meetings-domain ROW id (``meeting_id``),
    # which the terminal passes on the active meeting — the carriers key on it, never the native id
    # (which would fold a DIFFERENT tenant's / an older row's transcript into this user's chat).
    # Fall back to native only when the client didn't send a row id (legacy), documented as best-effort.
    stream_key = str(m.get("meeting_id") or native)
    status = str(m.get("status") or "").strip().lower()
    phase = meeting_steering.phase_for(status)
    fields = {
        "title": str(m.get("title") or "").strip() or str(native),
        "platform": platform,
        "native": str(native),
        "meeting_id": str(m.get("meeting_id") or native),   # the ROW id — the meeting's stable ?meeting= link
    }

    if phase == "prep":
        when = str(m.get("scheduled_at") or "").strip()
        fields["when"] = f", scheduled for {when}" if when else " (no time set yet)"
        workspace = str(m.get("workspace_id") or "").strip()
        fields["workspace"] = (
            f"The prep workspace bound to this meeting is \"{workspace}\" — ground your research and "
            f"write the brief there (its kg/ entities cover the attendees and companies). "
            if workspace
            else (
                "No shared prep workspace is bound to this meeting — the brief lives (or will live) "
                "as this meeting's note under kg/entities/meeting/ in the user's OWN workspace, "
                "reused across the meeting's series. "
            )
        )
        return (ctx, [], meeting_steering.render("prep", fields) + prompt)

    if phase == "post":
        fields["failed"] = " — the bot FAILED during this meeting" if status == "failed" else ""
        folded = _fold_meeting_processed(redis_url, stream_key, limit=MEETING_CHAT_TRANSCRIPT_SEGMENTS)
        if folded:
            fields["source"] = "processed notes (cleaned transcript)"
        else:
            folded = _fold_meeting_transcript(redis_url, stream_key, limit=MEETING_CHAT_TRANSCRIPT_SEGMENTS)
            fields["source"] = "raw transcript"
        if not folded:
            return (ctx, [], meeting_steering.NO_RECORD_POST.format(**fields) + prompt)
        fields["transcript"] = folded
        return (ctx, [], meeting_steering.render("post", fields) + prompt)

    transcript = _fold_meeting_transcript(redis_url, stream_key, limit=MEETING_CHAT_TRANSCRIPT_SEGMENTS)
    if transcript:
        fields["transcript"] = transcript
        preamble = meeting_steering.render("live", fields)
    else:
        preamble = meeting_steering.NO_TRANSCRIPT_LIVE.format(platform=platform, native=native)
    return (ctx, [], preamble + prompt)


# The grounding/user-message boundary marker. Every server-folded context block (kg-links + mounts,
# added by the WORKER, land BEFORE the user prompt too; schedule digest; meeting/workspace grounding)
# sits BEFORE this sentinel; the user's actual words are AFTER it. The terminal strips everything up to
# and including it in ONE cut — robust to preamble wording drift, unlike per-block regexes. An HTML
# comment so the model treats it as inert. Old chats (no sentinel) fall back to the client's regexes.
CONTEXT_SENTINEL = "<!--vexa:user-input-below-->"


# ── terminal-state context bundle (slice 1) — the grounding orchestrator ────────────────────────────
# A chat turn's prompt is assembled [ambient <schedule> digest] + [focus fold] + user prompt.
#   ambient — the schedule digest, SURFACE-GATED (Meetings list / Today tab / meeting-ish tab focused)
#             with the user's explicit include.schedule toggle beating the gate either way.
#   focus   — meeting/prep (delegates to _meeting_grounding, ENRICHED with the server row so a cold
#             client store can't ground a planned meeting as live), workspace (purpose + README head),
#             today (the full-day digest REPLACES ambient), file/none (unchanged).
# Everything stays inside the trusted control plane and rides the prompt (P15); ctx stays "none".

_AMBIENT_TAB_KINDS = {"today", "meeting", "meetingPrep"}


def _ambient_gated(context: "ChatContextBody | None") -> bool:
    """Digest on/off: explicit ``include.schedule`` wins; absent → on iff the user is on a
    meetings-relevant surface. No context (legacy client) → off (old behavior)."""
    if context is None:
        return False
    include = context.include or {}
    if isinstance(include.get("schedule"), bool):
        return include["schedule"]
    surface = context.surface or {}
    if surface.get("list") == "meetings":
        return True
    tab = surface.get("tab") or {}
    if tab.get("kind") in _AMBIENT_TAB_KINDS:
        return True
    focus = context.focus or {}
    return focus.get("kind") == "today"


_WORKSPACE_README_LINES = 60
_WORKSPACE_README_CHARS = 3000


def _fold_workspace_grounding(mounts: "list", slug: str) -> str:
    """The workspace-focus preamble: purpose + README head for the mount matching ``slug``.
    FAIL-CLOSED: a slug outside the caller's active/shared mounts folds nothing — the mount set
    IS the authorization; we never read a workspace the turn couldn't see."""
    mount = next((m for m in mounts if getattr(m, "slug", None) == slug
                  or getattr(m, "workspace_id", None) == slug), None)
    if mount is None:
        return ""
    name = str(getattr(mount, "name", "") or slug)
    try:
        purpose = read_purpose(mount.path) or ""
    except Exception:  # noqa: BLE001
        purpose = ""
    purpose_part = f" Its purpose: {purpose.strip()}." if purpose.strip() else ""
    readme = ""
    try:
        text = (Path(mount.path) / "README.md").read_text(encoding="utf-8")
        readme = "\n".join(text.splitlines()[:_WORKSPACE_README_LINES])[:_WORKSPACE_README_CHARS]
    except OSError:
        readme = ""
    fields = {"name": name, "slug": slug, "purpose": purpose_part, "readme": readme}
    if not readme.strip():
        return meeting_steering.NO_README_WORKSPACE_FOCUS.format(**fields)
    return meeting_steering.render("workspace_focus", fields)


def _enriched_meeting_focus(focus: dict, rows: "list[dict]") -> dict:
    """Overlay the SERVER row's truth onto the client-sent meeting focus — status/title/
    scheduled_at/workspace_id come from the meetings domain when the row is found; the client's
    values remain only as the fallback (legacy clients / row not fetched)."""
    nid = focus.get("native_id") or focus.get("ref")
    row = schedule_digest_mod.find_row(
        rows, meeting_id=focus.get("meeting_id"), platform=focus.get("platform"), native_id=nid)
    if row is None and nid is not None:
        # The terminal's tab param is the ROW id for planned meetings without a link (native is
        # NULL there) — it rides in native_id, so retry it as the row id before giving up.
        row = schedule_digest_mod.find_row(rows, meeting_id=nid)
    if row is None:
        return focus
    data = row.get("data") or {}
    merged = dict(focus)
    merged["meeting_id"] = row.get("id", focus.get("meeting_id"))
    merged["status"] = row.get("status") or focus.get("status")
    if row.get("platform") and row.get("platform") != "unknown":
        merged["platform"] = row["platform"]
    if row.get("native_meeting_id"):
        merged["native_id"] = row["native_meeting_id"]
    for src_key, dst_key in (("title", "title"), ("scheduled_at", "scheduled_at"), ("workspace_id", "workspace_id")):
        if data.get(src_key):
            merged[dst_key] = data[src_key]
    return merged


def _context_grounding(
    body: "ChatBody", session: str, redis_url: "str | None", *,
    schedule_rows: "Callable[[], list[dict]]",
    workspace_mounts: "Callable[[], list]",
) -> "tuple[dict, list[str], str]":
    """Assemble the turn's grounding from the context bundle (or the legacy ``active``).
    ``schedule_rows`` / ``workspace_mounts`` are LAZY — fetched only for the branches that
    need them, and both degrade to empty on failure (a bundle must never fail the turn)."""
    prompt = body.prompt
    context = body.context
    focus = context.focus if context is not None else body.active
    ctx = {"kind": "none", "session": session}

    ambient = _ambient_gated(context)
    kind = (focus or {}).get("kind")
    need_rows = ambient or kind in ("meeting", "today")
    rows: "list[dict]" = []
    if need_rows:
        try:
            rows = schedule_rows() or []
        except Exception:  # noqa: BLE001 — best-effort by contract
            rows = []

    tz = context.tz if context is not None else None
    preamble = ""
    if kind == "today":
        digest = schedule_digest_mod.build_schedule_digest(rows, tz=tz, full_day=True)
        if digest:
            preamble = digest + meeting_steering.render("schedule", {})
        return (ctx, [], preamble + prompt)

    if ambient:
        digest = schedule_digest_mod.build_schedule_digest(rows, tz=tz)
        if digest:
            preamble = digest + meeting_steering.render("schedule", {})

    if kind == "meeting":
        enriched = _enriched_meeting_focus(dict(focus), rows) if rows else dict(focus)
        _c, _t, folded_prompt = _meeting_grounding(enriched, session, prompt, redis_url)
        return (_c, _t, preamble + folded_prompt if preamble else folded_prompt)

    if kind == "workspace" and (focus or {}).get("slug"):
        try:
            mounts = workspace_mounts() or []
        except Exception:  # noqa: BLE001
            mounts = []
        preamble += _fold_workspace_grounding(mounts, str(focus["slug"]))
        return (ctx, [], preamble + prompt)

    # file focus stays client-side-preambled; none/unknown kinds fold nothing extra
    return (ctx, [], preamble + prompt)


# ── SSE ownership gate (P0 cross-tenant leak fix — the SSE sibling of the by-id REST check) ──────────
# The live SSE feed `GET /api/meeting/stream` is keyed on a CALLER-SUPPLIED row id (`meeting_id`) and a
# `session_uid`. Row ids are sequential ints, so without an ownership check any authenticated user B could
# `EventSource(...?meeting_id=<A_row>&session_uid=<A_native>)` and stream tenant A's live transcript +
# copilot cards — an ACTIVE, enumerable cross-tenant read. We mirror the WS `/ws` pattern (gateway
# `authorize_subscribe` → `Meeting.user_id == user_id`) and the by-id REST path (`get_transcript_by_id`
# owner-scopes in SQL): verify the caller OWNS the row BEFORE opening the redis stream. Fail CLOSED.
#
# agent-api has no meetings DB; it asks meeting-api `GET /meetings/{meeting_id}` forwarding the
# gateway-injected `X-User-Id` (meeting-api's `_resolve_user_id` trusts it exactly as its by-id path does)
# — a row owned by another user (or absent) returns 404 there → we treat it as NOT-OWNED. The returned
# record's `native_meeting_id` also lets us confirm the requested `session_uid` belongs to the SAME owned
# meeting, so B can't pair its own row with A's native to sniff A's copilot out-stream. Returns the owned
# meeting record (dict) on success, else None. Injectable so the L2 suite drives it over a fake.
# How room membership was derived, stamped on every room dispatch + log line so an audit can tell
# WHERE the room came from. Under the participant model it is the invite's addresses, resolved to
# subjects here — the ORDER comes from the transcript, the MEMBERSHIP never does.
_ROOM_SOURCE = "invite:participants→admin-api"


def _http_email_subject_lookup(admin_api_url: str, internal_secret: str, admin_token: str):
    """Build the participant ADDRESS → subject resolver: ``(address) -> str | None``.

    THE DOOR PROBLEM, stated because it constrains the whole feature. agent-api already holds an
    internal-tier seam to admin-api (``X-Internal-Secret``, used for the membership index + model
    config), but that tier exposes NO email lookup. The one route that answers this question,
    ``GET /admin/users/email/{email}``, is gated by ``verify_admin_token`` — a DIFFERENT and much
    broader credential (it can also create and patch users). So:

      * the narrow door is tried FIRST — ``GET /internal/users/by-email/{email}`` with the internal
        secret. It does not exist on admin-api today; this is the route that SHOULD be added
        (returning only ``{"id": ...}``), and when it is, the room works with no new credential and
        ``VEXA_ADMIN_API_TOKEN`` can be dropped. A 404 here marks it absent for the process lifetime
        so we probe once, not once per participant;
      * the wide door is used only when an operator has explicitly set ``VEXA_ADMIN_API_TOKEN``;
      * with neither, every lookup returns None — the room resolves to ZERO desks and says so. It
        never falls back to matching a person by name, which is the failure this design exists to
        avoid.

    Fail-CLOSED and quiet-per-call: any error is None (that participant is skipped), never an
    exception that could take down the turn."""
    import urllib.error
    import urllib.parse
    import urllib.request

    base = (admin_api_url or "").rstrip("/")
    state = {"internal_route": True}   # flipped off the first time the narrow door 404s

    def _get(url: str, headers: dict) -> "dict | None":
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:   # noqa: S310 — internal service URL
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode() or "null")

    def _lookup(address: str) -> "str | None":
        if not base or not address:
            return None
        quoted = urllib.parse.quote(str(address).strip().lower(), safe="")
        if state["internal_route"] and internal_secret:
            try:
                row = _get(f"{base}/internal/users/by-email/{quoted}",
                           {"X-Internal-Secret": internal_secret})
                if isinstance(row, dict) and row.get("id") is not None:
                    return str(row["id"])
                return None
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Ambiguous by design: 404 is both "no such user" and "no such route". Probe the
                    # ROUTE once — if the wide door is configured we can tell the difference by
                    # asking it; if it is not, treat the narrow door as present and this address as
                    # unknown (the safe reading — it skips a person rather than inventing one).
                    if not admin_token:
                        return None
                    state["internal_route"] = False
                else:
                    return None
            except Exception:  # noqa: BLE001 — unreachable admin-api → skip this participant
                return None
        if not admin_token:
            logger.warning("room: no email→subject resolver is configured (admin-api has no "
                           "internal by-email route and VEXA_ADMIN_API_TOKEN is unset) — the room "
                           "will mount ZERO desks")
            return None
        try:
            row = _get(f"{base}/admin/users/email/{quoted}", {"X-Admin-API-Key": admin_token})
        except Exception:  # noqa: BLE001 — 404 (no such user) and transport errors alike → skip
            return None
        return str(row["id"]) if isinstance(row, dict) and row.get("id") is not None else None

    return _lookup


def _http_meeting_owner_lookup(meeting_api_url: str):
    """Build the default owner-lookup: GET {meeting_api_url}/meetings/{id} with the caller's X-User-Id.
    Returns a callable ``(user_id: str, meeting_id: str) -> dict | None`` (the owned meeting record, or
    None when the row is absent / owned by someone else / meeting-api is unreachable — fail-closed)."""
    import urllib.error
    import urllib.request

    base = (meeting_api_url or "").rstrip("/")

    def _lookup(user_id: str, meeting_id: str) -> "dict | None":
        if not base or not user_id or not str(meeting_id).isdigit():
            return None  # non-numeric row id can't be an owned meeting row → fail closed
        try:
            req = urllib.request.Request(
                f"{base}/meetings/{int(meeting_id)}", headers={"X-User-Id": str(user_id)})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError:
            return None   # 404 (not owned / absent) or any other status → refuse
        except Exception:  # noqa: BLE001 — meeting-api unreachable → fail CLOSED, never open the stream
            return None

    return _lookup


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
    _GATE_OPEN_PREFIXES = ("/api/global/",)

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

    @app.get("/health")
    def health():
        ok = dispatcher is not None
        # ADDITIVE config.v1 rows (ADR-0026): the agent plane's capability tri-states (bot_gateway ·
        # model_inference). They never affect `status`/`checks` or the status code — an unconfigured
        # capability degrades a FEATURE (e.g. 'add bot from URL', worker model credentials), not the
        # process; the runtime's /health carries the credentials-file probe for the mount mechanics.
        from control_plane.config_preflight import capability_health

        return JSONResponse(
            {"status": "ok" if ok else "degraded", "service": "agent-api", "checks": {"dispatcher": ok},
             "capabilities": capability_health()},
            status_code=200 if ok else 503,
        )

    @app.get("/api/models")
    def models(request: Request):
        subject = subject_of(request)
        streaming_model = settings.meeting_model or default_meeting_model() or "default"
        try:
            # A workspace-pinned model (free string) wins; an unpinned workspace ("" — deployment
            # default) must NOT blank the label out.
            workspace_model = load_meeting_config(wsr.workspace_dir(subject)).model
            if workspace_model:
                streaming_model = workspace_model
        except ValueError:
            pass
        chat_model = settings.agent_model or "default"
        return {
            "chat_model": chat_model,
            "agent_model": chat_model,
            "streaming_model": streaming_model,
            "meeting_model": streaming_model,
        }

    @app.post("/invocations", status_code=202)
    def invocations(invocation: dict = Body(...)):
        """The dispatcher sink — any trigger source POSTs a unit.v1 dispatch here."""
        try:
            workload_id = dispatcher.dispatch(invocation)
        except ValidationError as e:  # non-conformant unit.v1 envelope — fail loud (P18)
            raise HTTPException(status_code=400, detail=f"invalid unit.v1 dispatch: {e.message}")
        return {"workload_id": workload_id}

    @app.post("/api/meeting/start", status_code=202)
    def meeting_start(body: MeetingStart, request: Request):
        """Launch (or touch) a live-meeting copilot for a real meeting — built through the ONE
        ``make_dispatch`` like every other trigger. ``meeting_id == session_uid == native_id`` so the
        transcript wire (``tc:meeting:{id}``), the dispatch (``agent-meet-{id}``), and the terminal all
        key on the same id. The bridge feeds ``tc:meeting:{native_id}``; the worker tails it."""
        meeting_ctx = {
            "meeting_id": body.native_id, "session_uid": body.native_id, "platform": body.platform,
        }
        transcript_start_id = _stream_tail_id(redis_url, f"tc:meeting:{body.native_id}")
        if transcript_start_id:
            meeting_ctx["transcript_start_id"] = transcript_start_id
        inv = units.make_dispatch(
            subject=subject_of(request), trigger="transcription",
            start=units.entrypoint(inline=_MEETING_BRIEF),
            context={"kind": "meeting", "meeting": meeting_ctx},
        )
        unit_id = dispatcher.dispatch(inv)
        meeting = {
            "meeting_id": body.native_id, "session_uid": body.native_id, "native_id": body.native_id,
            "platform": body.platform, "title": body.title or f"{body.platform} · {body.native_id}",
            "unit_id": unit_id,
        }
        live.add(meeting)
        return meeting

    @app.get("/api/meeting/relay-health")
    def meeting_relay_health(request: Request):
        """P18 (ADR 0010) — the transcript relay's observable health: is the numeric→native resolve OK,
        and are segments arriving? A stale `VEXA_BOT_API_KEY` (401 on `/meetings`) shows here as a typed
        `native_resolve: {ok:false, kind:'unauthorized', detail:…}` instead of silent dead air."""
        from control_plane import transcription_watcher as _txw
        return _txw.relay_health()

    @app.get("/api/admin/overview")
    def admin_overview(request: Request):
        """Read-only infra + pipeline introspection for the terminal's hidden admin panel: every
        runtime.v1 workload (agent workers + meeting bots, classified) plus the per-meeting redis
        pipeline carriers (proc/tc streams, opt-in flag, cursor, active_meetings membership).

        INTERNAL-TIER ONLY (fail-closed): the caller must present ``X-Internal-Secret`` matching
        ``VEXA_INTERNAL_API_SECRET`` — the terminal's Next server holds it and fronts this with its
        own email-allowlist gate; an unconfigured secret means NOBODY gets in (403), and the check
        holds regardless of ingress (direct or via the gateway's /agent/* proxy)."""
        from control_plane import admin_panel

        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        if not secret or not hmac.compare_digest(provided, secret):
            raise HTTPException(status_code=403, detail="internal secret required")

        overview: dict = {"workloads": [], "meetings": []}
        try:
            overview["workloads"] = admin_panel.fetch_workloads(settings.runtime_api_url)
        except Exception as e:  # noqa: BLE001 — typed partial failure (P18): the panel shows the section error
            overview["workloads_error"] = f"{type(e).__name__}: {e}"
        if redis_url:
            import redis as _redis

            try:
                r = _redis.from_url(redis_url, decode_responses=True)
                overview["meetings"] = admin_panel.pipeline_snapshot(r, live.list())
            except Exception as e:  # noqa: BLE001
                overview["meetings_error"] = f"{type(e).__name__}: {e}"
        else:
            overview["meetings_error"] = "no redis_url configured"
        return overview

    @app.post("/api/admin/probe")
    def admin_probe(request: Request):
        """Run the transcription-pipeline golden smoke probe (gateway → meeting-api → runtime →
        redis carriers → transcript relay). Same internal-tier gate as the overview; POST because
        it actively exercises the path (a redis write/read round-trip on scratch keys)."""
        from control_plane import admin_panel
        from control_plane import transcription_watcher as _txw

        secret = settings.internal_api_secret.get_secret_value() if settings is not None else ""
        provided = request.headers.get("x-internal-secret", "")
        if not secret or not hmac.compare_digest(provided, secret):
            raise HTTPException(status_code=403, detail="internal secret required")

        r = None
        if redis_url:
            import redis as _redis

            try:
                r = _redis.from_url(redis_url, decode_responses=True)
            except Exception:  # noqa: BLE001 — the probe's redis stage reports the fault
                r = None
        # Workloads cross-check the in-memory live registry (a stale "live" entry must not turn
        # relay quiet into a false FAIL). Unknown (kernel unreachable) → None = trust the registry.
        try:
            workloads = admin_panel.fetch_workloads(settings.runtime_api_url)
        except Exception:  # noqa: BLE001
            workloads = None
        return admin_panel.run_probe(settings, r, live.list(), relay_health=_txw.relay_health(),
                                     workloads=workloads)

    @app.post("/api/meeting/process", status_code=202)
    def meeting_process(body: MeetingProcess, request: Request):
        """User-controlled copilot PROCESSING for a meeting — DESIRED STATE ONLY (ADR 0027). This
        endpoint writes the opt-in flag; it never dispatches. The transcription watcher is the ONE
        dispatch arbiter: it arms (and keeps alive) the copilot while ``proc:meeting:{row}:on`` is
        set, always resuming from the per-meeting CURSOR (``proc:meeting:{row}:cursor`` = the last
        raw transcript stream-id already cleaned; absent ⇒ ``'0-0'`` = full history). Two writers
        used to dispatch here (this handler from the cursor, the watcher from the stream tail) and
        race — whichever landed second was a touch, so a tail-armed win silently skipped the
        backfill. OFF just clears the flag — the cursor is FROZEN at the last processed entry so a
        later re-enable gap-fills from exactly where we left off."""
        import redis as _redis

        r = _redis.from_url(redis_url, decode_responses=True)
        # P0 (cross-tenant leak fix): the copilot's opt-in flag / cursor / processed stream ALL key on
        # the meetings-domain ROW id — the native id is NOT unique (it collides across tenants + a user's
        # re-sends), so keying processing state by it armed / clobbered / resumed the wrong meeting. Prefer
        # the row id the terminal passes (POST /bots returns it); else resolve it off the live registry
        # (the watcher learns it from the segments' numeric meeting_id and stamps native_id on the entry).
        # Fall back to native only when neither is available (legacy client + not-yet-live) — documented as
        # a bootstrap-only path that arms once the row id is known.
        live_entry = next(
            (m for m in live.list()
             if m.get("native_id") == body.native_id or m.get("session_uid") == body.native_id),
            None,
        )
        row_id = (
            body.meeting_id
            or (str(live_entry["numeric_meeting_id"])
                if live_entry and live_entry.get("numeric_meeting_id") else None)
        )
        key = row_id or body.native_id
        # The opt-in flag has its OWN key suffix — it must NOT collide with the processed-notes STREAM
        # ``proc:meeting:{key}`` the worker XADDs (worker.py), else a GET on the flag hits a stream →
        # WRONGTYPE (crashes the watcher's arm loop). ``:cursor`` is likewise a distinct sibling key.
        flag = f"proc:meeting:{key}:on"
        cursor_key = f"proc:meeting:{key}:cursor"
        if not body.on:
            try:
                r.delete(flag)  # cursor is intentionally LEFT in place (frozen) for the next re-enable
            except Exception:  # noqa: BLE001 — best-effort; the watcher reaps the copilot on TTL anyway
                pass
            return {"native_id": body.native_id, "meeting_id": row_id, "processing": False}
        subject_of(request)  # identity gate (P20) — kept even though nothing dispatches from here
        cursor: str | None = None
        try:
            # TTL'd desired state (P21/P22 — verified on the eyeball: NO session_end frame ever
            # crosses the wire on the stop path, so the watcher's reap there is belt-only and the
            # flag used to persist forever). This backstop bounds a flag that never sees a segment;
            # the watcher REFRESHES a rolling TTL while segments actually flow, so the flag outlives
            # any real meeting and self-cleans within ~an hour of the flow stopping.
            r.set(flag, "1", ex=PROC_FLAG_BACKSTOP_TTL_SEC)
            cursor = r.get(cursor_key)
        except Exception:  # noqa: BLE001
            cursor = None
        # `resumed_from` reports where the watcher's arm WILL resume (the frozen cursor, else the
        # start of the transcript) — informational for the client; the dispatch itself happens on
        # the watcher's next segment (≤ one batch), keyed and started from the same cursor.
        start_id = cursor or "0-0"
        return {"native_id": body.native_id, "meeting_id": row_id, "processing": True, "resumed_from": start_id}

    @app.post("/api/chat")
    def chat(body: ChatBody, request: Request):
        """A chat *now*-dispatch: spawn the isolated container, stream its Stream back as SSE.

        RESUMABLE (mirrors /api/meeting/stream): every SSE event carries an ``id:`` = the unit output
        Stream cursor. A dropped view (per-dispatch worker cold-start races the SSE, a transient proxy
        drop) reconnects with ``Last-Event-ID`` — we then RE-ATTACH to the SAME warm unit and resume the
        read from that cursor (gapless) WITHOUT dispatching a second turn. The turn was never lost (the
        worker completes + commits regardless); resume just re-shows the output the client missed."""
        if stream_reader is None:
            raise HTTPException(status_code=501, detail="stream relay not wired")
        subject = subject_of(request)  # server-derived (P20); body.subject is ignored
        session = body.session or units.DEFAULT_CHAT_SESSION
        # THE MEETING ROOM (post-meeting run). Resolved and AUTHORISED here, before anything else
        # happens on this request — a refusal must cost the caller a 403, never a partially-run turn.
        # ``room`` never comes from the body: the body names a MEETING (and may PROPOSE a narrowing);
        # every subject in the returned room came from meeting-api. See ``_resolve_room``.
        room = None
        if body.room_meeting_id:
            room = _resolve_room(request, subject, body.room_meeting_id,
                                 participants=body.room_participants,
                                 names=body.room_participant_names,
                                 speakers=body.room_speakers,
                                 read_max=body.room_read_max)
        # THE SCAFFOLD (PRD 5.5). When the turn names one and it is THIS subject's, the record
        # — not the client — decides two things: which workspaces this chat mounts, and what
        # the opening ask is. Both used to be composed client-side, and that is precisely why
        # the panel and the agent disagreed about the same click. A scaffold that is not
        # this subject's is IGNORED rather than refused: a forwarded or stale id must not be
        # able to widen anybody's mounts, and must not break their turn either.
        scaffold_view = None
        if body.scaffold_id:
            rec = scaffolds.get(body.scaffold_id)
            if rec is not None and _scaffold_is_for(rec, request, subject):
                rec = scaffolds.redeem(body.scaffold_id, subject) or rec
                try:
                    scaffold_view = _scaffold_view(rec, subject)
                except scaffolds_mod.ScaffoldError:
                    logger.warning("scaffold %s cannot be rendered (its preset is gone) — the "
                                   "turn runs without it", body.scaffold_id)
            else:
                logger.warning("scaffold %s ignored on a turn by subject=%s (unknown or not "
                               "theirs)", body.scaffold_id, subject)
        if scaffold_view is not None:
            # THE OPENING IS THE RECORD'S, SUBSTITUTED HERE. The terminal composes no text —
            # it sends the id. The facts ride in front of the ask so the agent's first turn
            # can name the meeting, the time, the room and the person's state without
            # fetching anything, and the whole block is machinery-marked so the human never
            # sees it as their own message (ledger F7).
            body = body.model_copy(update={"prompt": scaffolds_mod.turn_prompt(scaffold_view)})
        # A reconnect carries Last-Event-ID (the last Stream cursor the client rendered). On resume we
        # DON'T re-dispatch — we re-attach to the existing warm unit and read from the cursor onward.
        resume = request.headers.get("last-event-id") or None
        # Ground the chat in the terminal's ACTIVE meeting (if any): agent-api folds the live transcript
        # from the meeting's redis Stream (tc:meeting:{native} — the SAME stream the copilot tails) into
        # the prompt, fresh on every turn. The transcript stays inside the trusted control plane and
        # rides the prompt to the worker — no file, no cross-domain HTTP, no user key in the worker (P15).
        ctx, tools, prompt = _context_grounding(
            body, session, redis_url,
            schedule_rows=lambda: _schedule_source(subject),
            workspace_mounts=lambda: (active_workspaces(wsr.root, subject)
                                      + shared_active_mounts(wsr.root, subject, mindex.list(subject))),
        )
        # Mark the grounding→user boundary so the terminal strips ALL folded context in one cut. Every
        # branch returns `<grounding> + body.prompt`, so the user's words are the exact suffix; insert the
        # sentinel right before them (no-op when nothing was folded). The kg/mounts preambles the worker
        # prepends land before `prompt`, hence before the sentinel too — so they're stripped as well.
        if body.prompt and prompt.endswith(body.prompt) and len(prompt) > len(body.prompt):
            prompt = prompt[: len(prompt) - len(body.prompt)] + CONTEXT_SENTINEL + body.prompt
        # Attribute this turn's commits to the human editor by EMAIL (gateway-injected, trusted) rather
        # than the bare subject id — the git author NAME becomes the email; the synthetic author email
        # (<subject>@vexa.local) stays for the you/member classification (workspace_reader.git_state_at).
        _email = (request.headers.get("x-user-email") or "").strip()
        inv = units.make_dispatch(
            subject=subject, trigger="message",
            start=units.entrypoint(inline=prompt), context=ctx, tools=tools,
            principal={"name": _email} if _email else None,
        )
        if resume:
            # Re-attach only — the warm unit id is deterministic from (subject, session); resume reads
            # its durable output Stream from the cursor. No new turn, no session re-title.
            unit_id = units.dispatch_id(inv)
        else:
            unit_id = units.dispatch_id(inv)
            retry_from = _chat_turn_head(redis_url, unit_id, body.turn_id) if body.turn_id else None
            if retry_from is not None:
                # No-cursor RETRY of the current turn (the stream dropped before the client saw any
                # ``id:``): re-attach from the turn's recorded start — the whole turn replays, including
                # a terminal event the worker wrote while the client was gone. NO second dispatch.
                resume = retry_from
            else:
                # Fresh turn — credential preflight FIRST (config.v1 ``model_inference``, the
                # request-path oracle): with no deployment credential AND no per-user custom
                # endpoint, the worker's claude CLI can only fail with its own "Not logged in ·
                # Please run /login" — an adapter internal that means nothing to an API consumer.
                # Refuse HERE with an actionable frame instead: no worker spawn, no ghost session
                # entry. A FAILED config lookup (None) fails OPEN — a down identity service must
                # never block a turn; the worker-side auth taxonomy still catches it cleanly.
                if capability_state("model_inference") == NOT_CONFIGURED:
                    cfg = dispatcher.resolve_model_config(subject)
                    if cfg is not None and not _has_custom_model_endpoint(cfg):
                        return StreamingResponse(
                            _sse([{"type": "error", "message": _model_creds_error_message()},
                                  {"type": "turn-complete"}]),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
                        )
                # Snapshot the out-Stream tail BEFORE dispatching and attach the reader from
                # it — attaching at ``$`` raced the worker (events written between dispatch and attach,
                # or a whole turn that finished in the gap, were invisible: the 'Reconnecting' hang).
                # The thread's Stream holds PRIOR turns too, so the snapshot (not stream start) is the
                # earliest safe attach point — the client appends and stops on any ``turn-complete``.
                start = _stream_tail_id(redis_url, units.output_topic(unit_id)) or None
                # Upsert the durable index on first use of a thread: a new thread is titled by its first
                # prompt; an existing one just bumps last_active (title preserved).
                is_new = not any(r["session"] == session for r in sess.list(subject))
                # A SCAFFOLDED chat is titled by its record, never by its opening: the opening
                # is machinery, and titling a rail row with the first 60 characters of an
                # instruction block is the same defect as painting it as the person's message.
                _title = ((scaffold_view["header"]["title"] or scaffold_view["opening_label"])
                          if scaffold_view is not None else _truncate_title(body.prompt))
                sess.upsert(subject, session, title=_title if is_new else None)
                # ``room`` applies AT SPAWN: the mount table is fixed when the container is created,
                # so a WARM unit (this thread already has a live worker) keeps the stack it booted
                # with and a room named on a later turn of the same thread does not retro-mount. The
                # post-meeting run uses its own per-meeting session, so it spawns cold and gets the
                # room; a turn that needs a different room needs a different session.
                try:
                    unit_id = dispatcher.dispatch(  # spawn-or-touch the thread's warm chat unit
                        inv, room=room,
                        scaffold_workspaces=(scaffold_view or {}).get("workspaces") or None)
                except dispatch_mod.WarmDeliveryFailed as exc:
                    # THE TURN IS REFUSED, NOT DROPPED. For a warm unit the pre-delivery is the only
                    # delivery, so a failure here means the person's words reached nobody. Answering
                    # 200 and streaming the turn already in flight is what made this invisible: they
                    # watch a reply appear and reasonably believe it is to what they just sent.
                    # 503 is deliberate — this is transient by nature (redis blip, a worker in the
                    # idle-exit race) and the honest instruction is "send it again".
                    logger.warning("chat turn refused for subject=%s session=%s: %s",
                                   subject, session, exc)
                    raise HTTPException(
                        status_code=503,
                        detail="That message did not reach your agent — nothing was lost on your "
                               "side, please send it again.") from exc
                if body.turn_id and start is not None:
                    _record_chat_turn_head(redis_url, unit_id, body.turn_id, start)
                resume = start
        return StreamingResponse(
            _sse(stream_reader.read(unit_id, resume=resume)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
        )

    @app.post("/api/chat/reset")
    def chat_reset(body: ResetBody, request: Request):
        """Drop a conversation thread: remove it from the index AND delete its continuity file so a
        future turn on the same name starts a fresh conversation (not a resume of the old one)."""
        subject = subject_of(request)
        session = body.session or units.DEFAULT_CHAT_SESSION
        sess.drop(subject, session)
        try:
            wsr.drop_session(subject, session)
        except Exception:  # noqa: BLE001 — index drop is the contract; the file delete is best-effort
            logger.exception("dropping continuity file failed subject=%s session=%s", subject, session)
        return {"ok": True}

    @app.get("/api/sessions")
    def list_sessions(request: Request):
        return {"sessions": sess.list(subject_of(request))}

    @app.get("/api/sessions/{session}/history")
    def session_history(session: str, request: Request):
        """The session's prior conversation, as simplified turns the terminal can render (so clicking a
        saved chat re-opens its history). Tolerant: a missing/empty transcript returns ``{turns: []}``;
        an invalid subject/session never 500s."""
        subject = subject_of(request)
        # The turn's cwd FOLLOWS the active set (flat model), so a thread's continuity may sit under
        # any currently-mounted workspace dir — hand the reader those candidates. Best-effort: a
        # failing mount resolution only narrows the search to _system + home.
        extra: list = []
        try:
            ms = active_workspaces(wsr.root, subject) + shared_active_mounts(wsr.root, subject, mindex.list(subject))
            extra = [m.path for m in ms]
        except Exception:  # noqa: BLE001
            logger.warning("mount resolution for history failed subject=%s — searching anchored roots only", subject)
        try:
            turns = wsr.history(subject, session, extra_roots=extra)
        except Exception:  # noqa: BLE001 — history is best-effort; a bad path → empty, never an error
            logger.exception("loading session history failed subject=%s session=%s", subject, session)
            turns = []
        return {"turns": turns}

    # ── routines (MVP2) — a scheduled routine compiles to a schedule.v1 cron job whose body is a
    #    unit.v1 dispatch POSTed back to /invocations when due (the runtime owns the durable cron) ──
    @app.post("/api/routines", status_code=201)
    def create_routine(body: RoutineCreate, request: Request):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        try:
            routine = routines_mod.make_routine(
                subject=subject_of(request), name=body.name, cron=body.cron, prompt=body.prompt,
            )
            job_spec = routines_mod.compile_to_job(routine, invocations_url=invocations_url)
        except (ValueError, ValidationError) as e:  # bad cron form / non-conformant routine — fail loud
            raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))
        job = scheduler.schedule(job_spec)
        ran_now = False
        if body.run_now:
            # Fire one immediate run via the dispatcher (no HTTP hop) so the author sees a result now.
            try:
                dispatcher.dispatch(job_spec["request"]["body"])
                ran_now = True
            except Exception:  # noqa: BLE001 — the routine is still scheduled even if the demo run fails
                ran_now = False
        return {"routine": routine, "job_id": job.get("job_id"), "ran_now": ran_now}

    @app.get("/api/routines")
    def list_routines(request: Request):
        if scheduler is None:
            return {"routines": []}
        cards = workspace_routines_mod.routine_cards_for_subject(
            subject_of(request),
            jobs=scheduler.list_jobs(limit=1000),
            workspaces_dir=wsr.root,
        )
        return {"routines": cards}

    @app.patch("/api/routines/{name}/enabled")
    def set_routine_enabled(name: str, body: RoutineEnabledPatch, request: Request):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        try:
            workspace_routines_mod.set_routine_file_enabled(
                subject,
                name,
                enabled=body.enabled,
                workspaces_dir=wsr.root,
            )
            result = workspace_routines_mod.reconcile_workspace_routines(
                subject,
                scheduler=scheduler,
                invocations_url=invocations_url,
                workspaces_dir=wsr.root,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown routine")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "ok": True,
            "name": name,
            "enabled": body.enabled,
            "reconcile": result.__dict__,
        }

    @app.delete("/api/routines/{routine_id}")
    def delete_routine(routine_id: str, request: Request):
        if scheduler is None:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        subject = subject_of(request)
        for job in scheduler.list_jobs():
            meta = job.get("metadata") or {}
            if meta.get("routine_id") == routine_id and meta.get("owner") == subject:
                scheduler.cancel_job(job["job_id"])
                return {"ok": True, "routine_id": routine_id}
        raise HTTPException(status_code=404, detail="unknown routine")

    # ── events (MVP3) — the GENERIC event-source ingress: any event.v1 Event → a unit.v1 dispatch →
    #    the one Dispatcher. agent-api knows no tool/domain; the unit reaches email/calendar via its
    #    toolbelt. Email-triage, post-meeting, news all POST here (one front door, P6) ──
    @app.post("/events", status_code=202)
    def events(event: dict = Body(...)):
        try:
            invocation = event_to_invocation(event)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"invalid event.v1: {e.message}")
        except ValueError as e:  # no plan carried — fail loud (P18)
            raise HTTPException(status_code=422, detail=str(e))
        workload_id = dispatcher.dispatch(invocation)
        return {"workload_id": workload_id, "trigger": invocation["trigger"]}

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

    @app.post("/internal/scaffolds", status_code=201)
    def mint_scaffold(body: ScaffoldMintBody, request: Request):
        """Mint one scaffold and return `{id, url}` — the INTERNAL tier only (flows and the rig).

        Gated on `X-Internal-Secret`, the same edge the meeting room uses, for the same reason: a
        scaffold names the workspaces a chat will mount and the opening its agent will answer, so a
        caller who could mint one for somebody else's address could compose that person's first
        turn. A browser client through the gateway holds no such secret and cannot mint at all.

        EVERYTHING THAT CAN BE WRONG IS WRONG HERE, NOT AT THE CLICK. The preset must exist and be
        non-empty, the kind must be in the catalogue, the terminal's origin must be configured. A
        step calls this BEFORE it sends, so a failure here stops the send — which is the share-gate
        doctrine one layer up (`email_attendees` refuses to mail a link it cannot make work). A
        record that mints happily and opens onto nothing is the exact failure this route exists to
        make impossible.

        THE SHARE TOKEN IS MINTED BY THE CALLER, not here, and that is a deliberate boundary. The
        restricted grant is `POST /meetings/{id}/share` on meeting-api as the meeting's OWNER, which
        needs that owner's gateway key; flows already holds the credential that can produce one
        (`flows_steps/meeting.mint_transcript_share`) and agent-api holds no gateway key by design.
        Giving agent-api one so this route could mint the share would put a key that can act as any
        user into a service whose whole job is to read workspaces. The caller mints, and passes the
        token here; this route composes it into the url so the LINK is still built in one place."""
        if not _internal_caller(request):
            raise HTTPException(status_code=403, detail="minting a scaffold is an internal-tier capability")
        who = str(body.who or "").strip()
        if not who or "@" not in who:
            raise HTTPException(status_code=400, detail="`who` must be the recipient's address")
        if body.kind not in scaffolds_mod.KINDS:
            raise HTTPException(status_code=400,
                                detail=f"unknown scaffold kind {body.kind!r} — the catalogue is "
                                       f"{', '.join(scaffolds_mod.KINDS)}")
        ui = (settings.ui_url if settings is not None else "").rstrip("/")
        if not ui:
            raise HTTPException(status_code=503,
                                detail="VEXA_UI_URL is not set on agent-api — a scaffold url would "
                                       "have no origin, and a link nobody can open is not a touch")
        try:
            fm, _body_text = scaffolds_mod.read_preset(_global_root(), str(body.opening or ""))
        except scaffolds_mod.ScaffoldError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        subject = _email_subject_lookup(who.lower()) or ""
        row = None
        mid = str(body.meeting or "").strip()
        minted_by = str((body.provenance or {}).get("minted_by") or "")
        if mid.isdigit():
            for reader_uid in (subject, minted_by):
                if reader_uid:
                    row = _meeting_owner_lookup(reader_uid, mid)
                    if isinstance(row, dict):
                        break
        group = scaffolds_mod.group_workspace_of(row)

        # THE MOUNT SET, STATED. `_global` always (it is the company layer and every worker carries
        # it), the recipient's own desk when they have one, the group desk when the meeting is bound
        # to one. A caller may override the whole list; it may not omit `_global`.
        if body.workspaces is not None:
            mounts = [w for w in body.workspaces if str(w).strip()]
        else:
            mounts = [subject] if subject else []
            if group and group not in mounts:
                mounts.append(group)
        mounts = [system_mounts.GLOBAL_SLUG] + [m for m in mounts if m != system_mounts.GLOBAL_SLUG]

        # ONE FIRST VISIT PER PERSON, NOT ONE PER SIGN-IN.
        #
        # Every other kind is minted by a flow at the moment it creates a touch, so a second mint is
        # a second touch and deserves its own record. `first-visit` is the opposite: it is minted
        # because nobody sent anything, on every sign-in that arrives without a link. Minting a
        # fresh record each time would give the terminal a new `scaffold-<id>` chat id each time,
        # and therefore a new rail row — a fourth sign-in showing four rows the person never made,
        # which is precisely the defect F34/F35 exist to remove. Caught in review by the terminal
        # worker before it shipped.
        #
        # So the record is reused while it is still PENDING. Once redeemed the person has actually
        # been through it, and a later sign-in is a genuinely new arrival that may hold new facts —
        # a workspace shared with them since, a meeting they have been invited to.
        if body.kind == "first-visit":
            existing = next((r for r in scaffolds.for_recipient(who, pending_only=True)
                             if r.get("kind") == "first-visit"), None)
            if existing:
                logger.info("scaffold REUSED id=%s kind=first-visit who=%s (unredeemed)",
                            existing["id"], who)
                return {"id": existing["id"], "url": f"{ui}/?s={existing['id']}"}

        refs = dict(body.refs or {})
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
        rec = scaffolds.mint({
            "who": who,
            "kind": body.kind,
            "meeting": mid or None,
            "workspaces": mounts,
            "refs": refs,
            "opening": str(body.opening),
            "tabs": body.tabs if body.tabs is not None else scaffolds_mod.frontmatter_list(fm, "tabs"),
            "focus": body.focus if body.focus is not None else (fm.get("focus") or ""),
            "provenance": dict(body.provenance or {}),
        })
        # The link carries the ID and, when the meeting is not the recipient's own, the capability
        # that makes it visible. NOTHING ELSE: no preset name, no mount list, no prompt text. What a
        # person forwards is an id bound to their address and a share bound to theirs.
        url = f"{ui}/?s={rec['id']}"
        if body.share_token:
            from urllib.parse import quote as _quote

            url += f"&tshare={_quote(str(body.share_token), safe='')}"
        logger.info("scaffold MINTED id=%s kind=%s who=%s meeting=%s mounts=%s opening=%s share=%s",
                    rec["id"], rec["kind"], who, rec.get("meeting"), mounts, rec["opening"],
                    bool(body.share_token))
        return {"id": rec["id"], "url": url}

    @app.get("/api/scaffolds/{scaffold_id}")
    def read_scaffold(scaffold_id: str, request: Request):
        """The record, as its recipient. Refuses anyone else; marks REDEEMED on first read.

        A 404 for an unknown id AND for one that is not yours — the id is a capability until redeem
        binds it, so a 403 would tell a prober that a scaffold with that id exists."""
        subject = subject_of(request)
        rec = scaffolds.get(scaffold_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such scaffold")
        if not (_internal_caller(request) or _scaffold_is_for(rec, request, subject)):
            logger.warning("scaffold REFUSED id=%s subject=%s reason=not-the-recipient", scaffold_id, subject)
            raise HTTPException(status_code=404, detail="no such scaffold")
        rec = scaffolds.redeem(scaffold_id, subject) or rec
        try:
            return _scaffold_view(rec, subject)
        except scaffolds_mod.ScaffoldError as e:
            # The preset was there at mint and is not there now — an admin deleted or emptied it.
            # Say so; a silent empty opening is the failure that let the phase greeting win (F5).
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/scaffolds")
    def list_scaffolds(request: Request, mine: bool = True, pending: bool = True):
        """The caller's own scaffolds — what is WAITING for this person behind a link they may never
        have clicked. Step 6 of the build order (`whats_waiting` returns pending scaffolds so the
        person's own agent opens the same record) reads exactly this: one record, two renderers."""
        subject = subject_of(request)
        email = (request.headers.get("x-user-email") or "").strip().lower()
        # Indexed by ADDRESS, because that is what a scaffold is minted against — the recipient
        # usually has no subject yet. With no gateway-injected address there is nothing to look up,
        # and the honest answer is an empty list, never every scaffold on the instance.
        rows = scaffolds.for_recipient(email, pending_only=pending) if email else []
        out = []
        for r in rows:
            try:
                out.append(_scaffold_view(r, subject))
            except scaffolds_mod.ScaffoldError:
                # One scaffold whose preset an admin deleted must not empty the whole list.
                logger.warning("scaffold %s is unrenderable (its preset is gone) — omitted", r.get("id"))
        return {"scaffolds": out}

    @app.get("/api/workspace/tree")
    def ws_tree(request: Request, hidden: bool = False, slug: Optional[str] = None):
        try:
            return {"files": wsr.tree_at(_read_target(request, slug), hidden=hidden)}
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")

    @app.post("/api/workspace/upload")
    async def ws_upload(request: Request, files: list[UploadFile] = File(...)):
        if not files:
            raise HTTPException(status_code=400, detail="no files uploaded")
        subject = subject_of(request)
        try:
            ws = wsr.workspace_dir(subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        uploads = ws / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        pending: list[tuple[Path, bytes, str, str]] = []
        for file in files:
            try:
                content = await file.read()
            finally:
                await file.close()
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"{file.filename or 'upload'} exceeds 25MB")
            safe_name = _upload_filename(file.filename)
            digest = hashlib.sha256(content).hexdigest()
            stored_name = f"{digest[:16]}-{safe_name}"
            target = (uploads / stored_name).resolve()
            if uploads.resolve() not in target.parents:
                raise HTTPException(status_code=400, detail="invalid filename")
            pending.append((target, content, stored_name, f"uploads/{stored_name}"))
        uploaded: list[dict[str, str]] = []
        for target, content, stored_name, path in pending:
            target.write_bytes(content)
            uploaded.append({"name": stored_name, "path": path})
        return {"files": uploaded}

    @app.get("/api/workspace/file")
    def ws_file(request: Request, path: str, slug: Optional[str] = None):
        try:
            content = wsr.read_at(_read_target(request, slug), path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"path": path, "content": content}

    @app.put("/api/workspace/file")
    def ws_file_write(request: Request, body: dict = Body(...)):
        """WRITE one doc — the terminal's in-place page editor (Codex-style). Authorization mirrors the
        MOUNT rules, not the read rules: own baseline/_system always; a shared workspace needs
        contributor+; `_global` only the admin allowlist. Commits so history stays honest."""
        import shutil as _sh  # noqa: F401 — parity with ws_reset's import style
        import subprocess as _sp
        subject = subject_of(request)
        rel = str(body.get("path") or "").strip()
        slug = str(body.get("slug") or "").strip() or None
        content = body.get("content")
        if not rel or rel.startswith("/") or ".." in rel.split("/") or not isinstance(content, str):
            raise HTTPException(status_code=400, detail="need a relative path and string content")
        # `kg/templates/` is the SHAPE of an entity, not one. It is hidden from every tree, so a tab
        # can only be open on it deliberately — and a save from that tab would silently rewrite the
        # shape every future entity is copied from.
        if rel.startswith("kg/templates/"):
            raise HTTPException(status_code=403, detail="kg/templates/ holds entity shapes, not records")
        if slug == system_mounts.GLOBAL_SLUG:
            if not global_layer.is_admin(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only an org admin may edit _global")
            candidates = [Path(settings.workspaces_dir) / system_mounts.GLOBAL_SLUG,
                          Path(settings.global_system_workspace_path or "/nonexistent")]
            target = next((c for c in candidates if c.is_dir() and os.access(c, os.W_OK)), None)
            if target is None:
                raise HTTPException(status_code=404, detail="the organisation tier is not writable here")
        else:
            if slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
                try:
                    membership_mod.require_role(wsr.root, slug, subject, "contributor")
                except MembershipError:
                    pass  # not shared — fall through; _read_target 403s anything outside the active set
            target = _read_target(request, slug, write=True)
        f = (target / rel).resolve()
        if target.resolve() not in f.parents:
            raise HTTPException(status_code=400, detail="invalid path")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        if (target / ".git").is_dir():
            _sp.run(["git", "-C", str(target), "add", rel], check=False, capture_output=True)
            _sp.run(["git", "-C", str(target), "-c", "user.name=vexa-terminal", "-c", "user.email=terminal@vexa.local",
                     "commit", "-m", f"edit {rel} (terminal page editor)"], check=False, capture_output=True)
        return {"path": rel, "written": True}

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

    @app.post("/api/workspace/entity")
    def ws_entity_upsert(request: Request, body: dict = Body(...)):
        """UPSERT one knowledge-graph entity — PRD decision 24, the single call behind `entity_upsert`.

        Creates `kg/entities/<kind>/<slug>.md` with frontmatter and a first dated entry, or appends a
        dated entry to the page already there; refreshes `kg/INDEX.md`; commits both by pathspec with
        the F31 subject shape. Authorization is `ws_file_write`'s, because it is the same act — a
        write into a workspace — and two spellings of one authorization rule is how the second one
        ends up weaker.

        This endpoint exists so the MCP tool can be a THIN FORWARD (PRD §3.3: every host-reaching rig
        tool is a missing endpoint in an owning service wearing a shell command). `workspace_write`'s
        `docker exec` double is the shape this deliberately does not copy.
        """
        subject = subject_of(request)
        kind = str(body.get("kind") or "").strip().lower()
        name = str(body.get("name") or "").strip()
        source = str(body.get("source") or "").strip()
        slug = str(body.get("slug") or "").strip() or None
        raw_facts = body.get("facts")
        if isinstance(raw_facts, str):
            raw_facts = [raw_facts]
        facts = [str(f) for f in (raw_facts or []) if str(f).strip()]

        if slug == system_mounts.GLOBAL_SLUG:
            # `_global` is the company layer, admin-written and read-only to every worker. An entity
            # write is exactly the kind of thing that would drift into it by accident.
            raise HTTPException(status_code=403, detail="_global is the organisation tier — entities "
                                                        "belong on a desk, not in it")
        if slug and slug not in (subject, system_mounts.SYSTEM_SLUG):
            try:
                membership_mod.require_role(wsr.root, slug, subject, "contributor")
            except MembershipError:
                pass  # not shared — _read_target 403s anything outside the active set
        target = _read_target(request, slug, write=True)
        try:
            # THE MOUNT SET IS HANDED IN so a `[[Name]]` whose page lives in another mounted
            # workspace is stored as `[[ws:<id>/<entity-id>]]` (PRD decision 26.3). Without it the
            # link resolves by TITLE in whichever mount the reader searches first, and dies the
            # moment either workspace is renamed — which is the ordinary case, not the edge.
            result = entities_mod.upsert_entity(target, kind, name, facts, source,
                                                mounts=_entity_mounts(subject))
        except entities_mod.EntityRefused as e:
            # 422, not 400: the request is well-formed and the REFUSAL is the product — the agent is
            # meant to read the sentence and fix the fact, not to retry the call.
            raise HTTPException(status_code=422, detail=str(e))
        index_rel = entities_mod.write_index(target, slug or str(subject))
        sha = None
        if result.get("changed"):
            sha = entities_mod.commit_entity(
                target, [result["path"], index_rel],
                subject_path=result["path"], created=bool(result.get("created")),
                author=(str(subject), f"{subject}@vexa.local"))
        return {**result, "index": index_rel, "commit": sha}

    @app.get("/api/workspace/git")
    def ws_git(request: Request, slug: Optional[str] = None):
        """Author-attributed source-control state (branch · working changes · recent commits) of a
        workspace. No ``slug`` → the caller's own primary. A ``slug`` addresses a SHARED workspace the
        caller is a member of (same authorized resolution as tree/file reads) — its commits carry
        ``author`` + ``kind`` so the terminal can show OTHER members' agent pushes as they land."""
        try:
            target = _read_target(request, slug)  # authorizes: a slug outside the caller's mount set → 403
            return wsr.git_state_at(target, viewer=subject_of(request))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")

    @app.get("/api/workspace/git/show")
    def ws_git_show(request: Request, sha: str, slug: Optional[str] = None, path: Optional[str] = None):
        """Unified diff of ONE commit (optionally one file) — same authorized resolution as ws_git — so
        the terminal can highlight exactly what a commit changed."""
        try:
            target = _read_target(request, slug)  # authorizes: a slug outside the caller's mount set → 403
            return wsr.git_diff_at(target, sha, path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")

    # ── workspace lifecycle (SCAFFOLD / TODO(phase-6)) — init from a validated template, swap which
    # validated workspace/template the next dispatch mounts. The seams exist downstream (seeding.seed_workspace
    # for init; VEXA_WORKSPACE_REPO/REF in dispatch/spawn for swap, bridge resolves per-meeting) — Phase 6
    # surfaces them here and wires the slim-client init_workspace()/use_workspace().
    @app.post("/api/workspace/init", status_code=201)
    def ws_init(request: Request):
        """EAGERLY provision this subject's workspace tiers — the "on account creation" seam (so the
        Personal baseline + the private `_system` tier exist BEFORE the first dispatch, instead of being
        lazily seeded on first turn). Materializes the baseline from the VALIDATED workspace-seed template
        (shared.seeding.seed_workspace) and ensures `_system` (system_mounts.ensure_system_workspace).
        Idempotent — existing tiers (`.git` present) are returned untouched, so it's safe to call on every
        login. The same seams the worker uses lazily on first dispatch, surfaced as a control."""
        subject = subject_of(request)
        ws = wsr.workspace_dir(subject)
        # Select the seed out of the registry root (default template for now; per-request template
        # selection lands with the second seed). VEXA_WORKSPACE_SEED_DIR still overrides.
        seed_dir = resolve_seed_dir(
            settings.default_template if settings is not None else None,
            seeds_root=settings.workspace_seeds_dir if settings is not None else None,
        )
        problems = validate_seed(seed_dir)
        if problems:
            raise HTTPException(status_code=500, detail="invalid workspace seed: " + "; ".join(problems))
        existed = (ws / ".git").exists()
        seed_workspace(ws, seed_dir)
        # The PRIVATE SYSTEM tier (`_system`) — always-mounted, holds the light identity reference. Ensure
        # it up front too so identity + chats/settings have a home from the very first turn. Idempotent.
        system_existed = (system_mounts.system_store_path(wsr.root, subject) / ".git").exists()
        system_mounts.ensure_system_workspace(str(wsr.root), subject)
        # The desk's id + registry row. Named by the address that signed in when the gateway gave
        # us one: a desk called "Desk 126" is better than `126` and worse than the person's own
        # address, and this is the one seam that has the address.
        _ws_sync(str(subject), kind="desk", owner=str(subject), ws_dir=ws,
                 name=(request.headers.get("x-user-email") or "").strip() or None)
        return {"workspace": str(ws), "seeded": not existed, "already_initialized": existed,
                "system_seeded": not system_existed}

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

    @app.get("/api/workspaces/by-slug/{slug}")
    def ws_id_by_slug(slug: str, request: Request):
        """The identity of a workspace addressed the OLD way — by slug. What the terminal calls to
        put a NAME where it used to print a directory name (F49: the chat header read `126`)."""
        subject = subject_of(request)
        rec = workspace_registry.by_slug(slug) or _ws_sync(slug)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        return ids_mod.view(rec, access, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member))

    @app.get("/api/workspaces/{workspace_id}")
    def ws_id_resolve(workspace_id: str, request: Request):
        """`{id, name, kind, access}` for one workspace id, from THIS reader's point of view.

        Never 404s and never 403s: `not-yours` and `gone` are ANSWERS (decision 26.3), and a status
        code would make the client render an error where the design says render a greyed chip."""
        subject = subject_of(request)
        rec = workspace_registry.get(workspace_id)
        access = ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member)
        return ids_mod.view(rec, access, workspace_id=workspace_id, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member))

    @app.post("/api/workspaces/{workspace_id}/rename")
    def ws_id_rename(workspace_id: str, request: Request, body: dict = Body(default={})):
        """Rename a workspace. The id does not move, and neither does anything pointing at it —
        that is the single behaviour PRD decision 26 was asked for, and this is the route that
        exercises it.

        WHO (founder ruling, 2026-09-02): the group's OWNER, and instance admins. Nobody else, and
        deliberately not a desk's own owner — a desk's name comes from the address that signed in,
        and renaming one is a question about identity display that has not been asked yet.

        AUDITED: who, old, new, when, kept on the record (capped) and logged. A rename is the one
        operation whose whole point is that nothing else changes, which means the only way to see
        that it happened at all is to have written it down."""
        subject = subject_of(request)
        try:
            rec = ids_mod.rename_audited(
                workspace_registry, workspace_id, str(body.get("name") or ""), by=str(subject),
                is_admin=bool(global_layer.is_admin(settings, str(subject))),
                root=wsr.root, is_member=_ws_is_member)
        except KeyError:
            raise HTTPException(status_code=404, detail="no workspace with that id")
        except ids_mod.RenameRefused as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {**ids_mod.view(rec, ids_mod.ACCESS_READABLE, writable=ids_mod.writable_for(
            rec, subject, root=wsr.root, is_member=_ws_is_member)),
            "renamed_from": (rec.get("renames") or [{}])[-1].get("from")}

    @app.post("/api/desk/touch", status_code=202)
    def desk_touch(request: Request, body: dict = Body(default={})):
        """Record that this person opened one page — the ranking signal behind the desk README.

        202, not 200: it is a report, the caller has nothing to do with the answer, and a panel
        must never wait on bookkeeping to render a document.

        WHAT IT WILL NOT DO is trust the workspace it is told. The body names a workspace id; the
        access rule is applied to it exactly as it is for a link, so a touch cannot be used to ask
        whether a workspace exists, and a page in a workspace this caller cannot read is not
        recorded. The DESK the touch is filed under is always the CALLER'S OWN — never a parameter,
        because "whose desk does this belong to" is not a question a client gets to answer."""
        subject = subject_of(request)
        wid = str(body.get("workspace") or "").strip()
        path = str(body.get("path") or "").strip().lstrip("/")
        if not wid or not path or ".." in path.split("/"):
            raise HTTPException(status_code=400, detail="a touch names a workspace id and a path")
        rec = workspace_registry.get(wid)
        if ids_mod.access_for(rec, subject, root=wsr.root, is_member=_ws_is_member) != ids_mod.ACCESS_READABLE:
            return {"recorded": False}          # not readable — nothing to say, and nothing leaked
        desk = workspace_registry.by_slug(str(subject)) or _ws_sync(str(subject), kind="desk",
                                                                    owner=str(subject))
        if not desk:
            return {"recorded": False}
        workspace_touches.touch(desk["id"], wid, path)
        # Mirror it where the WORKER can read it: the README is regenerated at the end of a turn,
        # in a container that holds the mounts and no redis.
        ids_mod.mirror_touches(desk.get("dir") or (wsr.root / str(subject)),
                               workspace_touches.recent(desk["id"]))
        return {"recorded": True}

    @app.post("/api/links/resolve")
    def links_resolve(request: Request, body: dict = Body(default={})):
        """A page's refs → what each one points at now, per ref: `{ref, title, url, access}`.

        One round trip for a whole document on purpose. The panel renders a doc at once, and a
        request per link would be a burst of them against the same three directories."""
        refs = body.get("refs")
        if not isinstance(refs, list):
            raise HTTPException(status_code=400, detail="refs must be a list of link references")
        slug = str(body.get("slug") or "").strip() or None
        return {"results": link_resolver_mod.resolve_many(
            [str(r) for r in refs], subject=subject_of(request), root=wsr.root,
            registry=workspace_registry, here=_ws_here(request, slug), is_member=_ws_is_member)}

    @app.get("/api/workspace/attached")
    def ws_attached(request: Request):
        """The subject's attachment view: the active slug + the parked workspaces available to swap back
        to, plus ``published_url`` — where the ACTIVE workspace was published (the ``vexa-publish``
        remote's token-free URL), or null when it never was. The client renders a published workspace
        with a link to its GitHub home instead of the publish action."""
        subject = subject_of(request)
        try:
            state = attached_workspaces(wsr.root, subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        state["published_url"] = published_remote_url(wsr.workspace_dir(subject))
        return state

    @app.post("/api/workspace/swap")
    def ws_swap(request: Request, body: WorkspaceSwapBody = Body(default=WorkspaceSwapBody())):
        """Attach a CUSTOM external git repo as this subject's active workspace (swap). The currently
        active workspace is PARKED (kept, never destroyed) so it can be swapped back to; the requested
        repo is restored from a prior park or cloned fresh. Omit ``repo`` to swap back to the seed.

        Mounting is by-folder (``<root>/<subject>`` is what the next dispatch mounts), so the swapped
        tree takes effect on the subject's next turn — no dispatch change needed."""
        subject = subject_of(request)
        key = deploy_keys_mod.workspace_key(subject=subject)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=body.repo or "", subject=subject,
                                      explicit_token=body.token) as cred:
                result = swap_workspace(wsr.root, subject, body.repo, body.ref or "main",
                                        slug=body.slug or None, fresh=body.fresh, token=cred.token,
                                        clone=_clone_fn(cred))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:
            # Already token-redacted (P15). A private repo we hold no credential for lands here — and
            # the answer is the workspace's public key to add, not a prompt for a secret.
            raise _credential_refusal(f"git clone failed: {exc}", subject, None, body.repo or "")
        return {
            "subject": result.subject,
            "active": result.active_slug,
            "repo": result.repo,
            "ref": result.ref,
            "swapped": result.swapped,
            "cloned": result.cloned,
            "parked": result.parked_slug,
            "nested": result.nested,
        }

    # ── the additive mount set (WP-A2.1): ACTIVE-SET membership over swap's park/restore machinery ──────
    @app.get("/api/workspace/active")
    def ws_active(request: Request):
        """The subject's ordered ACTIVE SET — the workspaces the next dispatch mounts (the private baseline
        first, then any activated extras). Each: ``slug, repo, ref, role, path, write, primary``."""
        subject = subject_of(request)
        try:
            mounts = active_workspaces(wsr.root, subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        # Lane A: append the SHARED workspaces the subject is a member of. Enumeration reconciles BOTH
        # membership stores (index ∪ policy/members.json — the same union as the shared listing) so a dead
        # or incomplete index cannot silently drop a locally-held grant from the mount set;
        # shared_active_mounts still re-checks the role authoritatively per workspace. A resolution failure
        # costs the "shared" section of the set, never the subject's own private mounts — and
        # ``index_degraded`` says out loud when the mirror could not be read.
        rows, index_degraded = membership_mod.reconciled_memberships(wsr.root, subject, mindex.list)
        try:
            mounts = mounts + shared_active_mounts(wsr.root, subject, rows)
        except Exception:  # noqa: BLE001 — a shared-mount resolution hiccup must not break the active-set read
            logger.warning("shared-mount resolution failed for subject=%s — returning private mounts only", subject)
        return {
            "subject": subject,
            "active": [
                {"slug": m.slug, "repo": m.repo, "ref": m.ref, "role": m.role,
                 "path": m.path, "write": m.write, "primary": m.primary, "name": m.name}
                for m in mounts
            ],
            "index_degraded": index_degraded,
        }

    @app.post("/api/workspace/activate")
    def ws_activate(request: Request, body: WorkspaceActivateBody = Body(default=WorkspaceActivateBody())):
        """ADD a workspace to the active set WITHOUT parking the others (the additive counterpart of swap).
        Clones/restores the target if needed. Idempotent — an already-active workspace is a no-op."""
        subject = subject_of(request)
        key = deploy_keys_mod.workspace_key(subject=subject)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=body.repo or "", subject=subject,
                                      explicit_token=body.token) as cred:
                result = activate_workspace(wsr.root, subject, body.repo, body.ref or "main",
                                            slug=body.slug or None, token=cred.token,
                                            clone=_clone_fn(cred))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:
            raise _credential_refusal(f"git clone failed: {exc}", subject, None, body.repo or "")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed,
                "cloned": result.cloned, "nested": result.nested}

    @app.post("/api/workspace/new", status_code=201)
    def ws_new(request: Request, body: WorkspaceNewBody = Body(default=WorkspaceNewBody())):
        """CREATE a brand-new BLANK workspace (seeded from the template) at a fresh unique slug and ADD it
        to the active set (additive — the "new workspace" action). Nothing is parked/rebuilt/backed up: the
        private baseline and every other active workspace stay exactly as they were."""
        subject = subject_of(request)
        try:
            result = create_workspace(wsr.root, subject, name=body.name or None)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed,
                "added": True}

    @app.post("/api/workspace/deactivate")
    def ws_deactivate(request: Request, body: WorkspaceDeactivateBody = Body(...)):
        """REMOVE a workspace from the active set (park it — never destroyed). The private baseline can be
        switched off too (sets ``baseline_hidden``; its home tree is untouched, re-activate to switch it back
        on). Idempotent — an already-off / not-active slug is a no-op."""
        subject = subject_of(request)
        try:
            result = deactivate_workspace(wsr.root, subject, body.slug)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        return {"subject": result.subject, "slug": result.slug, "changed": result.changed}

    @app.post("/api/workspace/publish")
    def ws_publish(request: Request, body: WorkspacePublishBody = Body(...)):
        """Publish this subject's vexa-born workspace to GitHub — the counterpart of swap/attach.
        Creates the repo under the caller's account (or ``org``) with their per-call PAT, then pushes
        the active workspace's current branch (FULL history) over the token-scrubbed dedicated remote.
        ``remote_url`` skips creation (pre-created/empty repo). Re-publish = plain push (fast-forward
        or a clear error on divergence — never a force push). The token is used server-side for this
        call only and never stored; every error is token-redacted (P15)."""
        subject = subject_of(request)
        token = (body.token or "").strip() or git_creds.read_github_token(wsr.root, subject)
        if not token:
            raise HTTPException(status_code=400, detail="a GitHub token is required — pass one or save a reusable token")
        try:
            result = publish_workspace(
                wsr.root, subject,
                token=token, repo_name=body.repo_name, private=body.private,
                org=body.org or None, remote_url=body.remote_url or None,
                # slug → any workspace the caller can manage (own parked slot or shared membership,
                # resolved + permission-checked by _manage_dir); omitted keeps the legacy seed target.
                ws_dir=_manage_dir(subject, body.slug) if body.slug else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc) or "invalid subject")
        except RepoExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))   # already token-redacted (P15)
        except PublishError as exc:
            raise HTTPException(status_code=502, detail=str(exc))   # already token-redacted (P15)
        return {
            "repo_url": result.repo_url,
            "pushed_ref": result.pushed_ref,
            "head_sha": result.head_sha,
            "created": result.created,
        }

    @app.post("/api/workspace/rename")
    def ws_rename(request: Request, body: WorkspaceRenameBody = Body(...)):
        """Rename a workspace slot — a DISPLAY label only. The slug and the parked tree are unchanged, so
        swap-back and repo re-attach keep matching. Pass an empty ``name`` to clear the label."""
        subject = subject_of(request)
        try:
            return rename_workspace(wsr.root, subject, body.slug, body.name)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")

    @app.get("/api/workspace/git-token")
    def ws_git_token_get(request: Request):
        """Whether the caller has a SAVED reusable GitHub token, and a masked (last-4) preview of it. The
        clear value is NEVER returned — server-side only (git_credentials)."""
        subject = subject_of(request)
        return {"set": git_creds.read_github_token(wsr.root, subject) is not None,
                "masked": git_creds.masked_github_token(wsr.root, subject)}

    @app.post("/api/workspace/git-token")
    def ws_git_token_set(request: Request, body: GitTokenBody = Body(default=GitTokenBody())):
        """Save (or CLEAR, with an empty token) the caller's reusable GitHub token — stored once, server-
        side, and applied as the fallback credential for every git op across all their repos. Returns the
        masked state, never the clear value."""
        subject = subject_of(request)
        try:
            stored = git_creds.set_github_token(wsr.root, subject, body.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"set": stored, "masked": git_creds.masked_github_token(wsr.root, subject)}

    @app.get("/api/workspace/git-remote-status")
    def ws_git_remote_status(request: Request, slug: Optional[str] = None):
        """The GitHub-sync state of a workspace (default = the caller's primary; ``slug`` = one of their
        own or shared workspaces). Read-only + no network: reports the home remote (origin / vexa-publish),
        its URL, the branch, and ahead/behind counts vs the last-fetched tracking ref. No token needed."""
        subject = subject_of(request)
        ws = _manage_dir(subject, slug)
        s = remote_status(ws)
        return {
            "has_home": s.has_home, "remote": s.remote, "url": s.url, "branch": s.branch,
            "tracked": s.tracked, "ahead": s.ahead, "behind": s.behind,
        }

    @app.post("/api/workspace/push")
    def ws_push(request: Request, body: WorkspacePushBody = Body(...)):
        """Push a workspace's current branch to its GitHub home (origin for attached clones, vexa-publish
        for published vexa-born), fast-forward only — NEVER a force push. The token authenticates the push
        and is never stored; a diverged remote fails loud (pull first). Every error is token-redacted (P15)."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=body.token) as cred:
                if cred.kind == "none":
                    raise HTTPException(status_code=400, detail=(
                        "this workspace has no credential — add its deploy key to the repository "
                        "(POST /api/workspace/{slug}/deploy-key) or save a reusable GitHub token"))
                r = push_origin(ws, token=cred.token, ssh_env=cred.ssh_env)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise _credential_refusal(str(exc), subject, body.slug, home.url or "")  # token-redacted (P15)
        return {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha}

    @app.post("/api/workspace/pull")
    def ws_pull(request: Request, body: WorkspacePullBody = Body(default=WorkspacePullBody())):
        """Fetch + FAST-FORWARD a workspace from its GitHub home. A divergence (local commits the remote
        lacks) is refused — no merge/rebase/force — so it is resolved deliberately. The token (optional for
        public repos) is used for the fetch only and never stored (P15)."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        # A pull REWRITES the tree, so on a shared workspace it is a write: viewers are refused here even
        # though they may read the same workspace through _manage_dir.
        _require_shared_write(subject, body.slug)
        home = remote_status(ws)
        key = _workspace_key(subject, body.slug)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=home.url or "", subject=subject,
                                      explicit_token=body.token) as cred:
                r = pull_origin(ws, token=cred.token, ssh_env=cred.ssh_env)  # None ⇒ public-repo fetch
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise _credential_refusal(str(exc), subject, body.slug, home.url or "")  # token-redacted (P15)
        return {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha,
                "updated": r.updated, "behind_before": r.behind_before}

    @app.get("/api/workspace/purpose")
    def ws_purpose_get(request: Request, slug: Optional[str] = None):
        """Read a workspace's PURPOSE one-liner (default = the caller's primary; ``slug`` = one of their
        own or shared workspaces). ``""`` when unset."""
        subject = subject_of(request)
        ws = _manage_dir(subject, slug)
        return {"purpose": read_purpose(ws)}

    @app.post("/api/workspace/purpose")
    def ws_purpose_set(request: Request, body: WorkspacePurposeBody = Body(default=WorkspacePurposeBody())):
        """Set (or clear) a workspace's PURPOSE — stored in the workspace + committed so it travels when
        shared and feeds the mount preamble. Returns the normalized purpose actually stored."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        return {"purpose": write_purpose(ws, body.purpose)}

    @app.get("/api/meeting/stream")
    def meeting_stream(meeting_id: str, session_uid: str, request: Request):
        """SSE feed for a LIVE meeting — merges the transcript Stream (`tc:meeting:{id}`) and the
        copilot's output Stream (`unit:agent-meet-{sid}:out`) into one feed the terminal renders:
        transcript lines + proactive `card`s + the agent working (`message-delta`/`tool-call`).

        RESUMABLE: every event carries an SSE ``id:`` = the per-stream redis cursors. On reconnect the
        browser echoes the last one as ``Last-Event-ID``; we resume EXACTLY from there (redis streams are
        durable + id-addressable) instead of re-seeding only the last N entries. Without this, a transient
        disconnect (the 'Live stream disconnected — reconnecting' path) dropped every segment published in
        the gap beyond the bounded replay window from the LIVE view — the real-time transcript-loss bug
        (the durable store kept them, so they only reappeared post-time)."""
        if not redis_url:
            raise HTTPException(status_code=501, detail="redis not wired")

        # P0 (cross-tenant leak fix — SSE sibling of the by-id REST ownership check): OWNER-SCOPE the live
        # feed BEFORE opening any redis stream. `meeting_id` (row id) + `session_uid` arrive from the
        # caller's query params; row ids are sequential ints, so without this an authenticated user B could
        # `EventSource(...?meeting_id=<A_row>&session_uid=<A_native>)` and stream tenant A's live transcript
        # + copilot cards (an ACTIVE, enumerable cross-tenant read). Mirror the WS `/ws` path: derive the
        # caller identity (`subject_of` → 401 on no gateway-injected X-User-Id) and verify the caller OWNS
        # the requested row (meeting-api `GET /meetings/{id}` owner-scopes in SQL: `Meeting.user_id ==
        # user_id` → 404 for a foreign/absent row). Fail CLOSED (403) BEFORE the stream opens.
        # OWNER-ONLY for now (matches the WS path today); a shared-workspace membership grant would extend
        # `_meeting_owner_lookup` — the clean seam — but is intentionally NOT honored here yet.
        subject = subject_of(request)  # 401 if no (gateway-injected) identity — fail closed
        owned = _meeting_owner_lookup(subject, meeting_id)
        if owned is None:
            # Absent row, or a row owned by a DIFFERENT tenant → refuse (404-equivalent, no stream opened).
            raise HTTPException(status_code=403, detail="not authorized for this meeting")
        # Defense-in-depth on the copilot out-stream: `session_uid` is ALSO caller-supplied and keys
        # `unit:agent-meet-{session_uid}:out`. The terminal passes the ROW id as `session_uid` for live
        # rows (liveMeetings.ts `session_uid = live ? id : undefined`); the meeting's own native id is
        # accepted for the legacy /api/meeting/start shape (native==row==session). Bind it to the OWNED
        # row so B can't pair its own row with A's key to sniff A's copilot cards.
        owned_native = str(owned.get("native_meeting_id") or "")
        if session_uid not in (owned_native, str(meeting_id)):
            raise HTTPException(status_code=403, detail="session_uid does not match this meeting")

        resume_t, resume_o, resume_p = _decode_sse_cursor(request.headers.get("last-event-id"))

        def gen():
            import time as _time

            import redis

            r = redis.from_url(redis_url, decode_responses=True)
            tkey = f"tc:meeting:{meeting_id}"
            okey = f"unit:agent-meet-{session_uid}:out"
            # ADR 0027: the SSE tails the processed-notes stream DIRECTLY (processed-notes.v1) —
            # baseline cleaned notes reach the view seconds after a segment instead of waiting for
            # an LLM beat on the out-stream, and the worker's `view_end` marker (not a quiet-poll
            # guess) tells us processing is complete.
            pkey = f"proc:meeting:{meeting_id}"
            # Resume EXACTLY from the client's last-seen cursors when present (gapless reconnect);
            # otherwise seed then live-tail (fresh connect). A missing proc cursor (old 2-part id)
            # resumes from 0-0 — a full replay the client's upsert-by-id absorbs, never a gap.
            last = {tkey: resume_t or "$", okey: resume_o or "$", pkey: resume_p or "0-0"}
            idle = 0
            ending = False        # transcript hit session_end — drain notes/cards before meeting-end
            ending_at = 0.0       # when the drain started (monotonic) — bounds a markerless worker
            view_end_seen = False  # the worker's completion marker arrived on the proc stream

            def cursor():
                return _encode_sse_cursor(last, tkey, okey, pkey)

            def seg_events(payload):
                for seg in payload.get("segments", []):
                    yield ({"type": "transcript", "speaker": seg.get("speaker"),
                            "text": seg.get("text"), "t": seg.get("start"),
                            "tsMs": seg.get("abs_start_ms"),
                            "completed": seg.get("completed", True),
                            "id": seg.get("segment_id")}, cursor())

            def retract_event(payload):
                """A `retract` marker (the collector withdrew superseded/over-extended pending drafts) →
                a `retract` SSE event so the terminal drops those segment ids from the live view."""
                ids = payload.get("segment_ids") or []
                if ids:
                    yield ({"type": "retract", "segment_ids": ids}, cursor())

            def note_events(entry_fields):
                """One proc-stream entry → the SAME `note` SSE event the out-stream used to carry
                (meetingLive.ts upserts by note.id). The `view_end` marker flips completion instead."""
                nonlocal view_end_seen
                if entry_fields.get("type") == "view_end":
                    view_end_seen = True
                    return
                try:
                    note = json.loads(entry_fields.get("note") or "null")
                except (json.JSONDecodeError, ValueError):
                    return
                if isinstance(note, dict) and note.get("id") and note.get("text"):
                    yield ({"type": "note", "note": note}, cursor())

            if resume_t is None:   # fresh connect → seed the bounded recent transcript tail
                seed_rows = list(reversed(r.xrevrange(tkey, count=MEETING_STREAM_TRANSCRIPT_REPLAY) or []))
                for entry_id, fields in seed_rows:
                    last[tkey] = entry_id
                    payload = json.loads(fields.get("payload", "{}"))
                    if payload.get("type") == "session_end":
                        ending = True
                        ending_at = _time.monotonic()
                        last.pop(tkey, None)
                        continue
                    if payload.get("type") == "retract":
                        yield from retract_event(payload)
                        continue
                    # A real segment AFTER a session_end in the replay tail means a NEW session resumed
                    # on this reused meeting-row stream (tc:meeting:{id} is shared across a meeting's
                    # sessions). The prior end must NOT close the current live view — without this reset
                    # a stale session_end seeds `ending=True` and fires a premature `meeting-end`, so the
                    # terminal shows "Meeting ended" over a still-live meeting.
                    ending = False
                    yield from seg_events(payload)
            if resume_o is None:   # fresh connect → seed the output (cards/agent-activity) replay
                output_seed_rows = list(reversed(r.xrevrange(okey, count=MEETING_STREAM_OUTPUT_REPLAY) or []))
                for entry_id, fields in output_seed_rows:
                    last[okey] = entry_id
                    yield (json.loads(fields.get("event", "{}")), cursor())
            # The proc stream needs no separate seed pass: the 0-0 resume cursor makes the first
            # xread below deliver its ENTIRE history (bounded by the notes' 1:1 segment cardinality),
            # so a mid-meeting connect renders the complete processed view.

            while True:
                # once the transcript ends, keep polling briefly — the copilot's FINAL beat is still
                # running (~10s of LLM); its notes + the view_end marker arrive on the proc stream.
                resp = r.xread(last, count=500, block=1500 if ending else 15000)
                if not resp:
                    if ending:
                        # End when processing is COMPLETE (view_end drained — evidence, P21), when no
                        # copilot ever wrote (empty proc stream — nothing to wait for), or at the
                        # bounded cap (a worker that died markerless must not hold the view open).
                        try:
                            has_proc = bool(r.exists(pkey))
                        except Exception:  # noqa: BLE001 — an unreadable stream must not wedge the close
                            has_proc = False
                        if (view_end_seen or not has_proc
                                or _time.monotonic() - ending_at > MEETING_STREAM_ENDING_CAP_SEC):
                            live.drop(session_uid)  # leaves the terminal's live-meetings feed
                            yield ({"type": "meeting-end"}, cursor())
                            return
                        # The final beat is still writing — keep draining. But this branch polls every
                        # 1.5s and yields NOTHING, so a drain that waits out the copilot (up to the 45s
                        # cap) goes silent well past the terminal's 20s SSE watchdog → the browser
                        # force-reconnects mid-drain ("stream disconnected" banner + a re-replayed
                        # session_end). Emit a ping so a healthy drain stays visibly alive.
                        yield ({"type": "ping"}, cursor())
                        continue
                    idle += 15000
                    if idle >= 600000:
                        return
                    yield ({"type": "ping"}, cursor())
                    continue
                idle = 0
                for stream, entries in resp:
                    for entry_id, fields in entries:
                        last[stream] = entry_id
                        if stream == tkey:
                            payload = json.loads(fields.get("payload", "{}"))
                            ptype = payload.get("type")
                            if ptype == "session_end":
                                ending = True            # don't end yet — drain the final beat first
                                ending_at = _time.monotonic()
                                last.pop(tkey, None)     # session_end is the last transcript entry
                                break
                            if ptype == "retract":
                                yield from retract_event(payload)
                                continue
                            # A NEW session resumed on this REUSED row (tc:meeting:{id} is shared across a
                            # meeting's sessions): a `session_start` marker — or any real segment — arriving
                            # after a prior `session_end` means the meeting is LIVE again. Clear a stale
                            # `ending` so the ≤45s drain never fires a premature `meeting-end` over it (the
                            # terminal showed "Meeting ended" on a still-live meeting). Mirrors the seed's
                            # reset at connect; without it the live loop kept `ending=True` because it only
                            # ever SET the flag, never cleared it.
                            if ending:
                                ending = False
                            if ptype == "session_start":
                                continue                 # marker only — nothing to render
                            yield from seg_events(payload)
                        elif stream == pkey:
                            yield from note_events(fields)
                        else:
                            yield (json.loads(fields.get("event", "{}")), cursor())

        return StreamingResponse(
            _sse(gen()), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


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

    @app.post("/api/workspace/shared/{workspace_id}/attach")
    def ws_shared_attach(workspace_id: str, request: Request, body: SharedAttachBody = Body(default=SharedAttachBody())):
        """Attach an EXISTING git repo as a shared workspace's tree. Contributor or owner only — a
        viewer may read the group's workspace, never replace it.

        Park-and-clone, exactly as the desk swap: the current tree is kept under the workspace's own
        store and can be swapped back to by slug with no re-clone, so this is reversible. ``policy/``
        (the member list) is carried into the new tree, so an attach can never lock the group out."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
        except MembershipError as exc:
            raise _member_error(exc)
        repo = (body.repo or "").strip() or None
        key = deploy_keys_mod.workspace_key(workspace_id=workspace_id)
        try:
            with wcreds.for_workspace(wsr.root, key=key, repo_url=repo or "", subject=subject,
                                      explicit_token=body.token) as cred:
                clone = _clone_fn(cred)
                result = attach_shared_workspace(wsr.root, workspace_id, repo, body.ref or "main",
                                                 slug=body.slug or None, token=cred.token, clone=clone)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace id")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:   # message already token-redacted (P15)
            raise _credential_refusal(f"git clone failed: {exc}", subject, workspace_id, repo or "")
        return {
            "workspace_id": workspace_id, "active": result.active_slug, "repo": result.repo,
            "ref": result.ref, "attached": result.swapped, "cloned": result.cloned,
            "parked": result.parked_slug, "nested": result.nested,
            "state": ("cloned" if result.cloned else "restored" if result.swapped else "already attached"),
        }

    @app.get("/api/workspace/shared/{workspace_id}/attached")
    def ws_shared_attached(workspace_id: str, request: Request):
        """A shared workspace's attachment view — the active slug and the parked trees available to swap
        back to, plus its GitHub home. Any member may read it (it says WHAT is mounted, never a credential)."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "viewer")
        except MembershipError as exc:
            raise _member_error(exc)
        try:
            state = shared_attached_state(wsr.root, workspace_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace id")
        ws = membership_mod._ws_dir(wsr.root, workspace_id)
        st = remote_status(ws)
        key = deploy_keys_mod.workspace_key(workspace_id=workspace_id)
        state["home"] = {"remote": st.remote, "url": st.url, "branch": st.branch,
                         "ahead": st.ahead, "behind": st.behind}
        # A CAPABILITY, not a secret: whether a credential exists and of what kind — never its value.
        state["credential"] = wcreds.home_capability(wsr.root, key=key, remote=st.remote, url=st.url,
                                                    subject=subject)
        return state

    @app.get("/api/workspace/{slug}/deploy-key")
    def ws_deploy_key_get(slug: str, request: Request):
        """This workspace's PUBLIC deploy key (null when none has been generated). The private half has
        no read path at all — it is sealed in the credential store and materialized only for one git op."""
        subject = subject_of(request)
        _manage_dir(subject, slug)          # authorization: own slot, or a workspace they belong to
        key = _workspace_key(subject, slug)
        pub = deploy_keys_mod.public_key(wsr.root, key)
        return {"slug": slug, "public_key": pub, "fingerprint": deploy_keys_mod.fingerprint(pub),
                "add_as": "a deploy key with WRITE access"}

    @app.post("/api/workspace/{slug}/deploy-key")
    def ws_deploy_key_ensure(slug: str, request: Request, body: dict = Body(default={})):
        """Generate this workspace's deploy key (idempotent — a second call returns the SAME key, so a
        key the person already added to their repo is never invalidated) and say where to add it.

        This is the credential model: they add our PUBLIC key to their repository; nothing of theirs
        ever travels to us, and the private half is sealed at rest and never leaves this server."""
        subject = subject_of(request)
        _manage_dir(subject, slug)
        repo_url = str((body or {}).get("repo") or "")
        try:
            prompt = wcreds.deploy_key_prompt(wsr.root, key=_workspace_key(subject, slug), repo_url=repo_url)
        except deploy_keys_mod.DeployKeyError as exc:
            raise HTTPException(status_code=501, detail=str(exc))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace")
        return {"slug": slug, **prompt, "message": wcreds.prompt_sentence(prompt)}

    @app.post("/api/workspace/shared/{workspace_id}/active")
    def ws_shared_active(workspace_id: str, request: Request, body: SharedActiveBody = Body(...)):
        """Switch a SHARED workspace ON/OFF in the caller's active set (mount vs hide). Membership is
        unchanged — this is a per-user mount preference so a member can 'switch it off' without leaving."""
        subject = subject_of(request)
        try:
            set_shared_active(wsr.root, subject, workspace_id, body.active)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid workspace")
        return {"workspace_id": workspace_id, "active": body.active}

    @app.post("/api/workspace/{slug}/archive")
    def ws_archive(slug: str, request: Request, body: ArchiveBody = Body(default=ArchiveBody())):
        """Archive (collapse, keep the data) or un-archive one of the caller's own workspaces."""
        subject = subject_of(request)
        try:
            set_archived(wsr.root, subject, slug, body.archived)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        return {"slug": slug, "archived": body.archived}

    @app.delete("/api/workspace/{slug}")
    def ws_delete(slug: str, request: Request):
        """DELETE one of the caller's own workspaces — removes the data irreversibly. Baseline is refused."""
        subject = subject_of(request)
        try:
            delete_workspace(wsr.root, subject, slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        return {"slug": slug, "deleted": True}

    @app.post("/api/workspace/reset")
    def ws_reset(request: Request, body: dict = Body(...)):
        """RESET a structural folder to the seed — the delete-equivalent for tiers whose SLOT must
        survive: the caller's `personal` baseline, or `_global` (admin allowlist only). Both are
        "just folders" (founder ruling 2026-08-22) — wipe the content, re-copy the seed, commit.
        `_system` is deliberately NOT resettable: it is sessions/continuity, not knowledge."""
        import shutil as _sh
        import subprocess as _sp
        subject = subject_of(request)
        target = str(body.get("target") or "")
        if target == "_global":
            if not global_layer.is_admin(settings, str(subject)):
                raise HTTPException(status_code=403, detail="only an org admin may reset _global")
            if not settings.global_system_workspace_path:
                raise HTTPException(status_code=404, detail="no _global configured")
            # write through the WORKSPACES-DIR mount (rw in dev) — the host-path mirror mount is ro
            candidates = [Path(settings.workspaces_dir) / "_global", Path(settings.global_system_workspace_path)]
            path = next((c for c in candidates if c.is_dir() and os.access(c, os.W_OK)), candidates[0])
        elif target == "personal":
            path = Path(wsr.workspace_dir(subject))
        else:
            raise HTTPException(status_code=400, detail="reset targets: personal | _global (shared workspaces are deleted; _system is never touched)")
        if not path.is_dir():
            raise HTTPException(status_code=404, detail="workspace not found")
        seed = resolve_seed_dir(seeds_root=os.environ.get("VEXA_WORKSPACE_SEEDS_DIR"))
        for child in path.iterdir():
            if child.name == ".git":
                continue
            _sh.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        _sh.copytree(seed, path, dirs_exist_ok=True)
        if (path / ".git").is_dir():
            _sp.run(["git", "-C", str(path), "add", "-A"], check=False, capture_output=True)
            _sp.run(["git", "-C", str(path), "-c", "user.name=vexa-platform", "-c", "user.email=platform@vexa.local",
                     "commit", "-m", f"reseed {target}"], check=False, capture_output=True)
        return {"target": target, "reset": True}

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

    @app.get("/api/global/state")
    def global_state(request: Request):
        """WHAT THE COMPANY LAYER HOLDS — the wizard's poll, and the honest answer to "why is this
        instance still refusing people".

        Readable by any authenticated subject on purpose: a non-admin who has just been refused at
        the door deserves to be told the instance is mid-setup rather than that they are broken.
        The company NAME is only returned once the gate is down — before that it is a half-written
        answer to a question about somebody's employer."""
        subject = subject_of(request)
        gate = global_layer.instance_state(settings)
        try:
            st = global_layer.state(_global_store())
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not read the organisation tier: {e}")
        down = gate.get("global_setup") == global_layer.COMPLETED
        return {
            "global_setup": gate.get("global_setup", global_layer.MISSING),
            "company": (gate.get("company") or st["company"]) if down else None,
            "present": st["present"],
            "missing_files": st["missing_files"],
            "reasons": st["reasons"],
            "is_repo": st["is_repo"],
            "commits": st["commits"],
            "ready_to_accept": st["ready"],
            "you_are_admin": global_layer.is_admin(settings, str(subject)),
            "gate_sentence": global_layer.GATE_SENTENCE,
        }

    @app.post("/api/global/ready")
    def global_ready(request: Request, body: dict = Body(default={})):
        """ACCEPT the company layer: verify the files, commit them as the admin, lift the gate.

        NOTHING MAY MARK ITSELF READY. The agent that wrote the layer asks for this verb and the
        verb goes and looks — the five files present and non-empty, and a README that opens with
        the company's name and one sentence of what it does. That last rule is the founder's:
        *"the first chat needs to present itself knowing about itself — which company it's from and
        what's their service."* An agent can only say which company it belongs to if a human wrote
        the name down, so the gate does not lift on a README that does not carry one.

        Admin-only, idempotent, and it reports WHY it refused rather than just refusing — the caller
        is an agent mid-conversation with the one person who can fix it."""
        subject = subject_of(request)
        if not global_layer.is_admin(settings, str(subject)):
            raise HTTPException(status_code=403,
                                detail="only the instance admin may accept the company layer")
        root = _global_store()
        st = global_layer.state(root)
        if not st["ready"]:
            return JSONResponse(status_code=409, content={
                "accepted": False,
                "global_setup": global_layer.MISSING,
                "missing_files": st["missing_files"],
                "reasons": st["reasons"],
                "next": "write the missing files into /workspaces/_global, then call this again",
            })
        email = str(body.get("author_email") or "").strip() or f"admin-{subject}@vexa.local"
        name = str(body.get("author_name") or "").strip() or f"vexa admin {subject}"
        try:
            sha = global_layer.commit(root, author_email=email, author_name=name,
                                      message=f"company layer: {st['company']}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"could not commit the company layer: {e}")
        try:
            global_layer.mark_ready(settings, company=st["company"])
        except Exception as e:  # noqa: BLE001
            # The commit stands and the files are on disk; only the MARKER failed. Say exactly that
            # — an agent told "failed" would rewrite files that are already correct.
            raise HTTPException(status_code=502, detail=(
                f"the company layer is committed ({sha}) but the instance gate could not be "
                f"recorded: {e}"))
        return {"accepted": True, "global_setup": global_layer.COMPLETED,
                "company": st["company"], "service": st["service"], "commit": sha,
                "files": st["present"]}

    @app.post("/api/workspace/{workspace_id}/unshare")
    def ws_unshare(workspace_id: str, request: Request):
        """UN-SHARE a workspace (owner only) — move it back into the caller's PRIVATE store and drop every
        member's index entry, so it stops being shared (mirror of share-enable). Returns the new private slug."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "owner")
            members = membership_mod.read_members(wsr.root, workspace_id)
            new_slug = ensure_workspace_private(wsr.root, subject, workspace_id)
        except MembershipError as exc:
            raise _member_error(exc)
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        for m in members:  # best-effort: the shared workspace is gone, so drop the derived index entries
            try:
                mindex.remove(m.get("subject"), workspace_id)
            except Exception:  # noqa: BLE001
                pass
        # The tree moved into the caller's private store and stopped being a group. Its id did NOT
        # change — un-sharing is an administrative act, not a new workspace — so every link into it
        # keeps resolving, and for everyone else it now answers `not-yours`, which is the truth.
        _ws_sync(new_slug, kind="desk", owner=subject,
                 ws_dir=workspace_slot_dir(wsr.root, subject, new_slug))
        return {"slug": new_slug}

    @app.post("/api/workspace/{slug}/share-enable")
    def ws_share_enable(slug: str, request: Request):
        """Make one of the caller's OWN workspaces shareable (promote a private workspace to a top-level
        shared one if needed) and ensure the caller is its owner. Returns the shareable workspace_id — the
        caller then mints invites against it. This is what lets ANY workspace be shared AFTER creation, with
        no share-vs-not decision at create time."""
        subject = subject_of(request)
        try:
            workspace_id, promoted = ensure_workspace_shareable(wsr.root, subject, slug)
            if promoted:
                membership_mod.ensure_owner(wsr.root, workspace_id, subject, index=mindex,
                                            email=request.headers.get("x-user-email"), commit_fn=_pc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="workspace not found")
        except MembershipError as exc:
            raise _member_error(exc)
        return {"workspace_id": workspace_id, "promoted": promoted}

    @app.post("/api/workspace/shared/new", status_code=201)
    def ws_shared_new(request: Request, body: SharedNewBody = Body(default=SharedNewBody())):
        """CREATE a new shared workspace and make the caller its OWNER — the bootstrap for the share flow.
        A fresh top-level workspace (git-inited + seeded) is created at <root>/<workspace_id>; the caller is
        granted owner in BOTH stores (policy/members.json + the index). The caller can then mint invites."""
        subject = subject_of(request)
        try:
            wid = create_shared_workspace_dir(wsr.root, body.name)
            membership_mod.ensure_owner(wsr.root, wid, subject, index=mindex,
                                        email=request.headers.get("x-user-email"), commit_fn=_pc)
            # The id is minted HERE, at creation, for the same reason it exists at all: this is the
            # only moment the workspace's human NAME is known. `create_shared_workspace_dir`
            # slugifies it into a directory and drops it, so without this line "ASWF DNA Project"
            # never existed anywhere and the group could only ever be called
            # `aswf-dna-project-b7b2ee`.
            _ws_sync(wid, kind="group", name=(body.name or "").strip() or None, owner=subject)
        except MembershipError as exc:
            raise _member_error(exc)
        except Exception as exc:  # noqa: BLE001 — surface a clean 500 (dir/seed failure) rather than a stack
            logger.exception("shared-workspace create failed for subject=%s", subject)
            raise HTTPException(status_code=500, detail="could not create shared workspace")
        return {"workspace_id": wid, "role": "owner", "name": body.name}

    @app.post("/api/workspace/invites", status_code=201)
    def ws_invite_create(request: Request, body: InviteCreateBody = Body(...)):
        """Mint a scoped invite token for a shared workspace. Auth: owner OR contributor of the target.
        The workspace must be shareable (reserved/own-private refused). The token is returned ONCE; only
        its hash is persisted in policy/invites.json."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, body.workspace_id, subject, "contributor")
            minted = membership_mod.mint_invite(
                wsr.root, body.workspace_id, role=body.role, created_by=subject,
                expires_in_sec=body.expires_in_sec, max_uses=body.max_uses,
                mode=body.mode, allowed_emails=body.allowed_emails, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        # The client composes the accept URL; we hand back the token + id + terms once.
        return {
            "id": minted.id, "token": minted.token, "role": minted.role,
            "workspace_id": body.workspace_id, "expires_at": minted.expires_at,
            "max_uses": minted.max_uses, "mode": body.mode,
            "accept_path": "/api/workspace/invites/accept",
        }

    @app.get("/api/workspace/invites/preview")
    def ws_invite_preview(request: Request, token: str):
        """READ-ONLY preview of an invite — the target workspace + terms — WITHOUT granting anything.
        Powers the pre-join CONSENT screen: the invitee sees what the workspace is (its purpose), the role
        they'd get, and who shared it BEFORE they log in / join. Capability-gated by the token (whoever
        holds the link may preview it); no membership is checked or created, no use is consumed. 404 for a
        token that matches nothing (never enumerates workspaces)."""
        info = membership_mod.preview_invite(wsr.root, token)
        if info is None:
            raise HTTPException(status_code=404, detail="invalid invite")
        wsid = info["workspace_id"]
        # Human context for the card: the workspace's purpose + who shared it (their email when we've
        # stored it — see the members roster; else the opaque subject as a last resort).
        purpose = read_purpose(membership_mod._ws_dir(wsr.root, wsid))
        shared_by = info.get("created_by")
        for m in membership_mod.read_members(wsr.root, wsid):
            if m.get("subject") == info.get("created_by") and m.get("email"):
                shared_by = m["email"]
                break
        return {
            "workspace_id": wsid, "name": wsid, "purpose": purpose,
            "role": info["role"], "mode": info["mode"], "expires_at": info["expires_at"],
            "shared_by": shared_by, "valid": info["valid"], "reason": info["reason"],
        }

    @app.post("/api/workspace/invites/accept")
    def ws_invite_accept(request: Request, body: InviteAcceptBody = Body(...)):
        """Redeem an invite token (any logged-in user) → membership in BOTH stores, use-count bumped.
        Idempotent per user (accepting twice = one membership, no extra use consumed). The token carries
        NO workspace id — we resolve it by scanning the shareable workspaces' invites for its hash.
        Post-auth redeem (AMENDMENT 5): the caller is an already-authenticated user (X-User-Id); a
        RESTRICTED invite additionally requires their VERIFIED email (X-User-Email, gateway-injected)
        to be in the invite's allowed_emails."""
        subject = subject_of(request)
        # SECURITY BOUNDARY: X-User-Email is trusted as the caller's VERIFIED email ONLY because the
        # gateway strips any client-sent x-user-email and re-injects the value it resolved from the
        # api-key. That invariant holds solely when the gateway is agent-api's SOLE ingress. Today the
        # terminal / host-local clients reach agent-api directly (no gateway hop), so on the direct edge
        # this header is spoofable — restricted-mode invites are NOT a security boundary until agent-api
        # is gateway-fronted (Stage 4). VEXA_REQUIRE_GATEWAY_IDENTITY (checked in subject_of) lets a
        # hardened deploy reject non-gateway callers. See the TOPOLOGY BOUNDARY note in create_app.
        subject_email = request.headers.get("x-user-email")
        h = membership_mod.hash_token(body.token)
        # Resolve which shared workspace this token belongs to by hash (never trust a client-declared id).
        target_ws = None
        root = wsr.root
        for child in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
            slug = child.name
            if slug.startswith(".") or slug in membership_mod.RESERVED_SLUGS:
                continue
            for inv in membership_mod._read_json_list(child, membership_mod.INVITES_FILE):
                if inv.get("hash") == h:
                    target_ws = slug
                    break
            if target_ws:
                break
        if target_ws is None:
            raise HTTPException(status_code=404, detail="invalid invite")
        try:
            result = membership_mod.accept_invite(
                wsr.root, target_ws, token=body.token, subject=subject, subject_email=subject_email,
                index=mindex, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        return result

    @app.delete("/api/workspace/invites/{invite_id}")
    def ws_invite_revoke(invite_id: str, request: Request, workspace_id: str):
        """Revoke an invite (owner/contributor of the workspace)."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            membership_mod.revoke_invite(wsr.root, workspace_id, invite_id, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "invite_id": invite_id}

    @app.get("/api/workspace/invites")
    def ws_invites_list(request: Request, workspace_id: str):
        """List a workspace's invites (owner/contributor). Hashes are never surfaced."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            return {"invites": membership_mod.list_invites(wsr.root, workspace_id)}
        except MembershipError as exc:
            raise _member_error(exc)

    @app.get("/api/workspace/members")
    def ws_members_list(request: Request, workspace_id: str):
        """List a workspace's members (owner/contributor). Opportunistically records the CALLER's own
        verified email onto their member row (self-healing for members granted before emails were stored)
        so the roster shows human labels, not opaque subject ids."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "contributor")
            try:  # best-effort label refresh — never fail the list on a backfill hiccup
                membership_mod.backfill_member_email(
                    wsr.root, workspace_id, subject,
                    request.headers.get("x-user-email"), commit_fn=_pc)
            except Exception:  # noqa: BLE001
                logger.debug("member email backfill skipped for %s in %s", subject, workspace_id, exc_info=True)
            return {"members": membership_mod.read_members(wsr.root, workspace_id)}
        except MembershipError as exc:
            raise _member_error(exc)

    @app.delete("/api/workspace/members/{member_subject}")
    def ws_member_remove(member_subject: str, request: Request, workspace_id: str):
        """Remove a member (owner only)."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "owner")
            membership_mod.remove_member(wsr.root, workspace_id, member_subject, index=mindex, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "subject": member_subject}

    @app.post("/api/workspace/members/{member_subject}/role")
    def ws_member_role(member_subject: str, request: Request, workspace_id: str,
                       body: RoleSetBody = Body(...)):
        """Flip a member's role (owner only) — read <-> read/write permissions."""
        subject = subject_of(request)
        try:
            membership_mod.require_role(wsr.root, workspace_id, subject, "owner")
            rec = membership_mod.set_role(
                wsr.root, workspace_id, member_subject, body.role,
                changed_by=subject, index=mindex, commit_fn=_pc,
            )
        except MembershipError as exc:
            raise _member_error(exc)
        return rec

    @app.post("/api/workspace/{workspace_id}/leave")
    def ws_member_leave(workspace_id: str, request: Request):
        """LEAVE a shared workspace — the caller removes THEMSELVES (any role; no owner gate). The
        last-owner guard still applies: a sole creator must unshare or hand off ownership rather than
        orphan the workspace, so their leave is refused (409) with that message."""
        subject = subject_of(request)
        if membership_mod.is_member(wsr.root, workspace_id, subject) is None:
            raise HTTPException(status_code=404, detail="not a member of this workspace")
        try:
            membership_mod.remove_member(wsr.root, workspace_id, subject, index=mindex, commit_fn=_pc)
        except MembershipError as exc:
            raise _member_error(exc)
        return {"ok": True, "left": workspace_id}

    @app.get("/api/workspace/shared")
    def ws_shared_list(request: Request):
        """The "workspaces shared with me" listing, reconciled across BOTH membership stores.

        ``users.data.memberships[]`` (the index) is the fast, cross-host read; ``policy/members.json``
        is the authoritative one (Q6). Reading ONLY the mirror made every grant on this host invisible
        whenever the internal edge to admin-api was unreachable — the route answered 200 with an empty
        list, so a 403 on that hop rendered in the UI as "you have no shared workspaces" rather than as
        an error. It is now a UNION, never a subtraction: an index row with no local dir is a workspace
        that lives on another host and must still be listed, so the git store only ever ADDS rows the
        index is missing. ``index_degraded`` says out loud when the mirror could not be read."""
        subject = subject_of(request)
        degraded = False
        try:
            rows = list(mindex.list(subject) or [])
        except Exception as exc:  # noqa: BLE001 — the authoritative store still answers; never 500 this
            logger.warning("membership index list failed for subject=%s: %s — serving policy/members.json",
                           subject, exc)
            rows, degraded = [], True
        seen = {r.get("workspace_id") for r in rows}
        for row in membership_mod.list_memberships(wsr.root, subject):
            if row["workspace_id"] not in seen:
                rows.append(row)
        return {"memberships": rows, "index_degraded": degraded}

    # ── Settings → Models "Test" buttons (on-demand credential tests, fail-loud surface) ────────
    # Both test the caller's EFFECTIVE config — the same user > global > env resolution the
    # dispatch overlay / bot_spawn apply — so what's tested is what a turn/bot actually gets.

    @app.get("/api/models/test")
    def models_test(request: Request):
        """Test the effective model credentials NOW: custom mode = a real 1-token completion
        against the endpoint; subscription = mounted-credentials expiry check (the recurring
        stale-Keychain 401 surfaces here with its remedy instead of at the next chat turn)."""
        from control_plane import config_test as _ct
        subject = subject_of(request)
        cfg: dict = {}
        mc = getattr(dispatcher, "_model_config", None)
        if mc is not None:
            try:
                cfg = mc.resolve(subject) or {}
            except Exception as exc:  # resolver down → still test the env floor, but SAY so
                out = _ct.run_models_test({})
                out["summary"] += f" (settings resolver unavailable: {exc} — tested env defaults)"
                return out
        return _ct.run_models_test(cfg)

    @app.get("/api/transcription/test")
    def transcription_test(request: Request):
        """Probe the effective STT backend with its token (GET /balance): catches dead URLs,
        rejected tokens, and the zero-balance-external-account case that 402s every segment."""
        from control_plane import config_test as _ct
        subject = subject_of(request)
        url, token, source = "", "", "env"
        settings = dispatcher.settings
        admin = (settings.admin_api_url or "").rstrip("/")
        if admin:  # same internal edge bot_spawn uses (bot-context carries the resolved override)
            import urllib.request as _ur
            try:
                req = _ur.Request(f"{admin}/internal/users/{subject}/bot-context",
                                  headers={"X-Internal-Secret":
                                           settings.internal_api_secret.get_secret_value()})
                with _ur.urlopen(req, timeout=5) as r:
                    body = json.loads(r.read())
                t = body.get("transcription") or {}
                if t.get("url") or t.get("token"):
                    url, token, source = t.get("url") or "", t.get("token") or "", "settings"
            except Exception:
                pass  # fall through to env — the probe result still says what was tested
        if not url:
            url = os.environ.get("TRANSCRIPTION_SERVICE_URL", "")
            token = token or os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
        elif not token:
            token = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
        return _ct.run_transcription_test(url, token, source)
    return app


# ── ASGI entrypoint (PEP 562) — `uvicorn control_plane.api:app` resolves this lazily ──────────────────
def _build_production_app() -> FastAPI:
    from shared.adapters import AdminApiMembershipIndex, AdminApiModelConfig, LocalIdentityMinter, RedisStreamReader, RuntimeHttpClient, SchedulerHttpClient
    from shared.config import load_settings
    from control_plane.config_preflight import preflight
    from control_plane.workspace_routines import start_workspace_routine_reconciler

    # config.v1 boot preflight (ADR-0026): agent-api has no required-explicit keys today, so this
    # logs the capability tri-states (bot_gateway · model_inference) — a deploy that cannot add bots
    # from URL or whose workers will have NO model credentials says so in the boot log and on
    # /health, instead of failing at first chat with 'Model inference failed: Not logged in'.
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
    # tails transcription_segments → fans tc:meeting:{uid} + arms the copilot dispatch on activity.
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
