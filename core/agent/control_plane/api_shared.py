"""api_shared.py — everything agent-api's routes are built OUT of, and none of the wiring.

Split out of `api.py` so the per-owner routers in `control_plane/routers/` can import it. They
cannot import `api.py`: `api.py` imports THEM, and a module that imports its own importer is a
cycle whose behaviour depends on which line the interpreter reached first — a fragile thing to
put under 79 routes.

Nothing here changed in the move. It is the same request/response models, the same pure helpers
and the same constants, in the same order, byte for byte; `git log --follow` and a `git diff -M`
show it as a move. What DID change is that they now have exactly one home, reachable from both
`create_app` and every router, rather than being reachable only from inside one 3,914-line file.
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
from fastapi.responses import JSONResponse, Response, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from control_plane import meeting_room
from control_plane import meeting_steering
from control_plane import schedule_digest as schedule_digest_mod
from control_plane import routines as routines_mod
from control_plane.config_preflight import NOT_CONFIGURED, capability_state, missing_capability_keys
from shared import units
from workspaces.shared import entities as entities_mod
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
from workspaces.shared import workspace_paths as wpaths
from control_plane import global_layer
from control_plane import version as version_mod
from control_plane import system_mounts
from control_plane import scaffolds as scaffolds_mod
from control_plane import model_endpoint
from control_plane import chat_intents
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

class _LiveMeetings:
    """In-memory registry of live meetings — the terminal's 'meetings' feed. Keyed by session_uid (the
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
    # THE INTENT (PRD decision 32/35). A button pressed on a page — Extend, Create this page,
    # Explore a term in a transcript, Highlight the transcript — is an ACT on a named thing, not a
    # sentence somebody typed. The terminal has sent this since decision 32 landed client-side; with
    # `extra="forbid"` above and no field here, every one of those presses 422'd. It is a plain dict
    # rather than a model on purpose: the CLOSED vocabulary is `chat_intents.INTENT_PRESETS`, an
    # unknown kind is ignored, and a client one release ahead must not be refused at the door.
    intent: Optional[dict] = None


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


class ScaffoldHandBody(BaseModel):
    """`POST /api/scaffolds/hand` — a HAND LINK (`/?ask=<preset>&meeting=<row>`) turned into a record.

    Two fields, both NAMES, neither prompt text — the same rule as the internal mint and for the same
    reason (PRD 6, decisions 13/18): a URL must never be able to drive somebody's agent. What made
    this route necessary is that the terminal used to substitute `?meeting=`/`?ws=` straight into the
    composed opening, so a crafted link put attacker-chosen text into the first turn.

    There is deliberately NO `who`: the recipient is the SIGNED-IN CALLER, taken from the session.
    A field would let anyone who can reach this route mint a first turn for somebody else."""
    model_config = {"extra": "forbid"}
    preset: str                                # a preset NAME in _global/asks/, never text
    meeting: Optional[str] = None              # a meetings-domain ROW id the CALLER can see, or None


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


def _encode_sse_cursor(last: dict, tkey: str) -> str:
    """The transcript stream's redis cursor as the SSE event id (the browser echoes it as
    Last-Event-ID on reconnect → we resume EXACTLY from here, gapless). '-' = not-yet-read.
    ONE part since PRD decision 34: the copilot out-stream and the processed-notes stream that
    used to occupy the other two are gone, and with them everything they carried."""
    return str(last.get(tkey, "-"))


def _decode_sse_cursor(raw: str | None) -> "str | None":
    """Last-Event-ID → the transcript stream id, or None when absent/malformed (fresh connect).
    Tolerates the retired multi-part form (``transcript|output|processed``) a client reconnecting
    across the deploy still holds: the first field was always the transcript cursor."""
    if not raw:
        return None
    head = raw.split("|")[0].strip()
    return head if head and head != "-" else None


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

    ONE spelling, shared with the dispatch overlay and the Test button (F93): this used to be a
    third hand-written copy of "is this custom", and the three disagreed — the Test button certified
    a configuration the turn would not use. It now delegates to `model_endpoint.has_custom_endpoint`
    so the pre-flight gate and the dispatch cannot answer differently."""
    return model_endpoint.has_custom_endpoint(cfg)


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
    """Fold the live transcript Stream ``tc:meeting:{stream_key}`` — the SAME stream the terminal
    renders — into ordered ``speaker: text`` lines for chat
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
      post (completed/failed/stopped)— fold the recorded transcript ``tc:meeting:{row}``; if there is
                                       none say so plainly (fail loud, never fabricate).

    There is ONE transcript and one fold of it: the "processed notes" the post branch used to prefer
    were the in-product inference pipeline's output, and PRD decision 34 removed that pipeline.
    Returns the plain (none-context, no tools, prompt) when the active tab isn't a meeting."""
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
# an ACTIVE, enumerable cross-tenant read. We mirror the WS `/ws` pattern (gateway
# `authorize_subscribe` → `Meeting.user_id == user_id`) and the by-id REST path (`get_transcript_by_id`
# owner-scopes in SQL): verify the caller OWNS the row BEFORE opening the redis stream. Fail CLOSED.
#
# agent-api has no meetings DB; it asks meeting-api `GET /meetings/{meeting_id}` forwarding the
# gateway-injected `X-User-Id` (meeting-api's `_resolve_user_id` trusts it exactly as its by-id path does)
# — a row owned by another user (or absent) returns 404 there → we treat it as NOT-OWNED. The returned
# record's `native_meeting_id` also lets us confirm the requested `session_uid` belongs to the SAME owned
# meeting, so B can't pair its own row with A's native to sniff A's feed. Returns the owned
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


