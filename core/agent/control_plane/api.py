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

from control_plane import meeting_steering
from control_plane import schedule_digest as schedule_digest_mod
from control_plane import routines as routines_mod
from shared import units
from control_plane import workspace_routines as workspace_routines_mod
from shared.agent_config import default_meeting_model, load_meeting_config
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from control_plane.workspace_attach import (
    CloneError,
    activate_workspace,
    active_workspaces,
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
    swap_workspace,
    workspace_dir_for,
)
from control_plane.workspace_publish import PublishError, RepoExistsError, publish_workspace, published_remote_url
from control_plane.workspace_git_sync import RemoteSyncError, pull_origin, push_origin, remote_status
from control_plane.workspace_purpose import read_purpose, write_purpose
from control_plane import workspace_membership as membership_mod
from control_plane import git_credentials as git_creds
from control_plane import system_mounts
from control_plane.workspace_membership import MembershipError, MembershipIndex, InMemoryMembershipIndex
from control_plane.dispatch import Dispatcher
from control_plane.events import event_to_invocation
from shared.ports import SchedulerPort, StreamReader
from control_plane.workspace_reader import WorkspaceReader

logger = logging.getLogger("agent_api.api")
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
        # Each item is either a bare event dict, or (event, sse_id) — the id makes reconnects resumable.
        ev, sid = item if isinstance(item, tuple) else (item, None)
        prefix = f"id: {sid}\n" if sid else ""
        yield f"{prefix}data: {json.dumps(ev)}\n\n"


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
    }

    if phase == "prep":
        when = str(m.get("scheduled_at") or "").strip()
        fields["when"] = f", scheduled for {when}" if when else " (no time set yet)"
        workspace = str(m.get("workspace_id") or "").strip()
        fields["workspace"] = (
            f"The prep workspace bound to this meeting is \"{workspace}\" — ground your research and "
            f"write the brief there (its kg/ entities cover the attendees and companies). "
            if workspace
            else "No prep workspace is bound to this meeting yet — say so when it matters. "
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
    row = schedule_digest_mod.find_row(
        rows, meeting_id=focus.get("meeting_id"),
        platform=focus.get("platform"), native_id=focus.get("native_id") or focus.get("ref"))
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
) -> FastAPI:
    if sessions is not None:
        sess = sessions
    elif redis_url:
        import redis as _redis

        sess = _Sessions(_redis.from_url(redis_url, decode_responses=True))
    else:
        sess = _Sessions()
    live = _LiveMeetings()
    wsr = reader or WorkspaceReader("/workspaces")
    mindex: MembershipIndex = membership_index if membership_index is not None else InMemoryMembershipIndex()
    app = FastAPI(title="vexa-agent-api", version="0.12.0")
    app.state.dispatcher = dispatcher
    app.state.sessions = sess
    app.state.live_meetings = live
    app.state.scheduler = scheduler
    settings = dispatcher.settings if dispatcher is not None else None
    # The SSE ownership gate's owner-lookup (P0): default = HTTP to meeting-api; injectable for L2 tests.
    _meeting_owner_lookup = meeting_owner_lookup or _http_meeting_owner_lookup(
        settings.meeting_api_url if settings is not None else "")
    # The ambient schedule digest's rows source (context bundle): TTL-cached meeting-api fetch;
    # injectable for L2 tests, same seam style as meeting_owner_lookup.
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
            # Upsert the durable index on first use of a thread: a new thread is titled by its first
            # prompt; an existing one just bumps last_active (title preserved).
            is_new = not any(r["session"] == session for r in sess.list(subject))
            sess.upsert(subject, session,
                        title=_truncate_title(body.prompt) if is_new else None)
            unit_id = dispatcher.dispatch(inv)  # spawn-or-touch the thread's warm chat unit
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

    def _read_target(request: Request, slug: Optional[str]) -> Path:
        """Resolve which workspace dir a READ (tree/file) targets, returning its ABSOLUTE PATH. Default (no
        slug) = the caller's primary baseline. A `slug` addresses ANOTHER mount in the caller's active set —
        their own non-primary private workspaces (which live under .attached, NOT <root>/<slug>) OR a SHARED
        workspace they're a member of. Authorization is by construction: the set is built for THIS subject
        (own actives + shared_active_mounts over their memberships), so a slug not in it → 403. This is what
        lets the KNOWLEDGE panel render one section per active mount without leaking arbitrary workspaces."""
        subject = subject_of(request)
        target = (slug or "").strip()
        # _system — the caller's OWN private-system workspace (RW, surfaced hidden-by-default in the files
        # panel). It's a per-subject dispatch mount, not in the active set, so authorize it directly here:
        # it can only ever resolve to THIS subject's own .system store — never another user's.
        if target == system_mounts.SYSTEM_SLUG:
            return system_mounts.system_store_path(wsr.root, subject)
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
        return {"workspace": str(ws), "seeded": not existed, "already_initialized": existed,
                "system_seeded": not system_existed}

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
        _tok = (body.token or "").strip() or git_creds.read_github_token(wsr.root, subject)
        try:
            result = swap_workspace(wsr.root, subject, body.repo, body.ref or "main",
                                    slug=body.slug or None, fresh=body.fresh, token=_tok or None)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:
            # message is already token-redacted (P15); private repo without/with a bad token lands here.
            raise HTTPException(status_code=502, detail=f"git clone failed: {exc}")
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
        # Lane A: append the SHARED workspaces the subject is a member of. The index (users.data.memberships[])
        # only ENUMERATES candidates; shared_active_mounts re-checks the role authoritatively per workspace.
        # A failing index costs the "shared" section of the set, never the subject's own private mounts.
        try:
            mounts = mounts + shared_active_mounts(wsr.root, subject, mindex.list(subject))
        except Exception:  # noqa: BLE001 — a shared-mount resolution hiccup must not break the active-set read
            logger.warning("shared-mount resolution failed for subject=%s — returning private mounts only", subject)
        return {
            "subject": subject,
            "active": [
                {"slug": m.slug, "repo": m.repo, "ref": m.ref, "role": m.role,
                 "path": m.path, "write": m.write, "primary": m.primary, "name": m.name}
                for m in mounts
            ],
        }

    @app.post("/api/workspace/activate")
    def ws_activate(request: Request, body: WorkspaceActivateBody = Body(default=WorkspaceActivateBody())):
        """ADD a workspace to the active set WITHOUT parking the others (the additive counterpart of swap).
        Clones/restores the target if needed. Idempotent — an already-active workspace is a no-op."""
        subject = subject_of(request)
        _tok = (body.token or "").strip() or git_creds.read_github_token(wsr.root, subject)
        try:
            result = activate_workspace(wsr.root, subject, body.repo, body.ref or "main",
                                        slug=body.slug or None, token=_tok or None)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workspace")
        except CloneError as exc:
            raise HTTPException(status_code=502, detail=f"git clone failed: {exc}")
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
        token = (body.token or "").strip() or git_creds.read_github_token(wsr.root, subject)
        if not token:
            raise HTTPException(status_code=400, detail="a GitHub token is required — pass one or save a reusable token")
        try:
            r = push_origin(ws, token=token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise HTTPException(status_code=502, detail=str(exc))  # already token-redacted (P15)
        return {"remote": r.remote, "url": r.url, "branch": r.branch, "head_sha": r.head_sha}

    @app.post("/api/workspace/pull")
    def ws_pull(request: Request, body: WorkspacePullBody = Body(default=WorkspacePullBody())):
        """Fetch + FAST-FORWARD a workspace from its GitHub home. A divergence (local commits the remote
        lacks) is refused — no merge/rebase/force — so it is resolved deliberately. The token (optional for
        public repos) is used for the fetch only and never stored (P15)."""
        subject = subject_of(request)
        ws = _manage_dir(subject, body.slug)
        token = (body.token or "").strip() or git_creds.read_github_token(wsr.root, subject)  # None ⇒ public-repo fetch
        try:
            r = pull_origin(ws, token=token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except RemoteSyncError as exc:
            raise HTTPException(status_code=502, detail=str(exc))  # already token-redacted (P15)
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
                        continue  # the final beat is still writing — keep draining
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
                            if payload.get("type") == "session_end":
                                ending = True            # don't end yet — drain the final beat first
                                ending_at = _time.monotonic()
                                last.pop(tkey, None)     # session_end is the last transcript entry
                                break
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
        """The "workspaces shared with me" listing from the index (users.data.memberships[])."""
        subject = subject_of(request)
        try:
            return {"memberships": mindex.list(subject)}
        except Exception:
            return {"memberships": []}
    return app


# ── ASGI entrypoint (PEP 562) — `uvicorn control_plane.api:app` resolves this lazily ──────────────────
def _build_production_app() -> FastAPI:
    from shared.adapters import AdminApiMembershipIndex, LocalIdentityMinter, RedisStreamReader, RuntimeHttpClient, SchedulerHttpClient
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
    if settings.admin_api_url:
        membership_index = AdminApiMembershipIndex(
            settings.admin_api_url, settings.internal_api_secret.get_secret_value(),
        )
    # Lane A: the Dispatcher takes the SAME index so shared workspaces the subject is a member of enter
    # the dispatch mount set (read-only for Slice 1), not just the /active listing.
    dispatcher = Dispatcher(settings, runtime, identity, membership_index=membership_index)
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
    return app


def __getattr__(name: str):
    if name == "app":
        return _build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
