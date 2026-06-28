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

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from control_plane import routines as routines_mod
from shared import units
from control_plane import workspace_routines as workspace_routines_mod
from shared.agent_config import DEFAULT_MEETING_MODEL, load_meeting_config
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from control_plane.dispatch import Dispatcher
from control_plane.events import event_to_invocation
from shared.ports import SchedulerPort, StreamReader
from control_plane.workspace_reader import WorkspaceReader

logger = logging.getLogger("agent_api.api")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MEETING_STREAM_TRANSCRIPT_REPLAY = 80
MEETING_STREAM_OUTPUT_REPLAY = 160


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


class _LiveMeetings:
    """In-memory registry of meeting copilots — the terminal's 'meetings' feed. Keyed by session_uid (the
    native Meet code). A stopped/ended meeting is KEPT (``status='stopped'``) so the terminal can offer to
    send the bot back; ``add`` (re)marks it live. The dev-tier foundation."""

    def __init__(self) -> None:
        self._by_uid: dict[str, dict] = {}

    def add(self, meeting: dict) -> None:
        m = dict(meeting)
        m["status"] = "live"
        self._by_uid[meeting["session_uid"]] = m

    def stop(self, session_uid: str) -> None:
        m = self._by_uid.get(session_uid)
        if m:
            m["status"] = "stopped"

    def drop(self, session_uid: str) -> None:
        # the meeting ended — keep the row (stopped) so 'send the bot back' stays available
        self.stop(session_uid)

    def list(self) -> list[dict]:
        return list(self._by_uid.values())


class ChatBody(BaseModel):
    model_config = {"extra": "forbid"}
    prompt: str
    # subject is DERIVED server-side from X-User-Id (P20) — kept here only so a client that still sends it
    # doesn't 422 (extra=forbid); the value is IGNORED. Dropped from the client in Stage 4.
    subject: Optional[str] = None
    session: Optional[str] = None
    # the terminal's active center tab ({kind, ref}) — grounds the chat in what's
    # in focus. Accepted now (Wave 2 wires it into the meeting tool / file context).
    active: Optional[dict] = None


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
    subject: Optional[str] = None  # DERIVED from X-User-Id (P20); ignored if sent.


# The meeting copilot's start brief. The in-container worker drives per-beat extraction with its own
# CARD_PROMPT; this is the envelope's entrypoint (continuity = the session file in the workspace).
_MEETING_BRIEF = (
    "You are the live meeting copilot. Watch the meeting transcript as it streams in and surface the "
    "people, companies, products, and projects worth tagging."
)


def _encode_sse_cursor(last: dict, tkey: str, okey: str) -> str:
    """Pack the per-stream redis cursors into ONE SSE event id (the browser echoes it as
    Last-Event-ID on reconnect → we resume EXACTLY from here, gapless). '-' = not-yet-read."""
    return f"{last.get(tkey, '-')}|{last.get(okey, '-')}"


def _decode_sse_cursor(raw: str | None) -> "tuple[str | None, str | None]":
    """Last-Event-ID → (transcript_id, output_id). None when absent/malformed (fresh connect)."""
    if not raw or "|" not in raw:
        return (None, None)
    t, _, o = raw.partition("|")
    return (t if t and t != "-" else None, o if o and o != "-" else None)


def _sse(events) -> Iterator[str]:
    for item in events:
        # Each item is either a bare event dict, or (event, sse_id) — the id makes reconnects resumable.
        ev, sid = item if isinstance(item, tuple) else (item, None)
        prefix = f"id: {sid}\n" if sid else ""
        yield f"{prefix}data: {json.dumps(ev)}\n\n"


MEETING_READ_TOOL = "meeting.read_transcript"  # the meeting-scoped transcript tool (tools-seed/)


def _meeting_grounding(active: "dict | None", session: str, prompt: str) -> "tuple[dict, list[str], str]":
    """Cookbook #1 — chat grounding via a meeting-scoped tool. If the terminal's ACTIVE tab is a meeting,
    return (meeting context, [the transcript tool], a prompt prefixed with "you are in a live meeting …");
    otherwise the plain (none-context, no tools, prompt). The agent then reads meetings' published
    ``/transcripts`` through the tool on demand — never a file, never the other domain's internals (P23/P3)."""
    a = active or {}
    if a.get("kind") != "meeting":
        return ({"kind": "none", "session": session}, [], prompt)
    m = a.get("meeting") or a  # tolerate {kind, meeting:{…}} or a flat {kind, platform, native_id}
    native = m.get("native_id") or m.get("ref")
    if not native:
        return ({"kind": "none", "session": session}, [], prompt)
    platform = m.get("platform") or "google_meet"
    ctx = {"kind": "meeting", "session": session,
           "meeting": {"platform": platform, "native_id": native}}
    preamble = (f"You are in a live meeting ({platform}/{native}). Use the `{MEETING_READ_TOOL}` tool to "
                f"read its transcript when you need it.\n\n")
    return (ctx, [MEETING_READ_TOOL], preamble + prompt)


def create_app(
    dispatcher: Dispatcher,
    *,
    stream_reader: Optional[StreamReader] = None,
    sessions: Optional[_Sessions] = None,
    reader: Optional[WorkspaceReader] = None,
    scheduler: Optional[SchedulerPort] = None,
    invocations_url: Optional[str] = None,
    redis_url: Optional[str] = None,
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
    app = FastAPI(title="vexa-agent-api", version="0.12.0")
    app.state.dispatcher = dispatcher
    app.state.sessions = sess
    app.state.live_meetings = live
    app.state.scheduler = scheduler
    settings = dispatcher.settings if dispatcher is not None else None

    def subject_of(request: Request) -> str:
        """The authenticated subject (P20). The gateway resolves the api-key → user_id and injects
        ``X-User-Id``; agent-api derives the workspace/chat/quota partition from THAT, never from the
        client body/query. Fail-closed (401) when the header is absent, unless a single-user fallback
        (``VEXA_AGENT_DEFAULT_SUBJECT``) is configured for a direct/self-host deploy with no gateway in front."""
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
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "service": "agent-api", "checks": {"dispatcher": ok}},
            status_code=200 if ok else 503,
        )

    @app.get("/api/models")
    def models(request: Request):
        subject = subject_of(request)
        streaming_model = settings.meeting_model or DEFAULT_MEETING_MODEL
        try:
            streaming_model = load_meeting_config(wsr.workspace_dir(subject)).model
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

    @app.post("/api/meeting/process", status_code=202)
    def meeting_process(body: MeetingProcess, request: Request):
        """User-controlled copilot PROCESSING for a meeting. Processing is OPT-IN: the transcription
        watcher only arms / keeps the copilot alive while ``proc:meeting:{native}`` is set — so OFF means
        NO processing runs at all (the raw transcript still streams).

        ON resumes from the per-meeting CURSOR (``proc:meeting:{native}:cursor`` = the last raw transcript
        stream-id already cleaned): the copilot gets ``transcript_start_id = cursor`` and processes the gap
        ``[cursor → tail]`` in ONE catch-up pass, then continues live. A never-processed meeting has no
        cursor ⇒ ``'0-0'`` (process the whole history). OFF just clears the flag — the cursor is FROZEN at
        the last processed entry so a later re-enable gap-fills from exactly where we left off."""
        import redis as _redis

        r = _redis.from_url(redis_url, decode_responses=True)
        # The opt-in flag has its OWN key suffix — it must NOT collide with the processed-notes STREAM
        # ``proc:meeting:{native}`` the worker XADDs (worker.py), else a GET on the flag hits a stream →
        # WRONGTYPE (crashes the watcher's arm loop). ``:cursor`` is likewise a distinct sibling key.
        flag = f"proc:meeting:{body.native_id}:on"
        cursor_key = f"proc:meeting:{body.native_id}:cursor"
        if not body.on:
            try:
                r.delete(flag)  # cursor is intentionally LEFT in place (frozen) for the next re-enable
            except Exception:  # noqa: BLE001 — best-effort; the watcher reaps the copilot on TTL anyway
                pass
            return {"native_id": body.native_id, "processing": False}
        cursor: str | None = None
        try:
            r.set(flag, "1")
            cursor = r.get(cursor_key)
        except Exception:  # noqa: BLE001
            cursor = None
        # Gap-fill from the cursor (last cleaned raw id); no cursor yet ⇒ from the start of the transcript.
        start_id = cursor or "0-0"
        inv = units.make_dispatch(
            subject=subject_of(request), trigger="transcription",
            start=units.entrypoint(inline=_MEETING_BRIEF),
            context={"kind": "meeting", "meeting": {
                "meeting_id": body.native_id, "session_uid": body.native_id,
                "platform": body.platform, "transcript_start_id": start_id,
            }},
        )
        dispatcher.dispatch(inv)
        return {"native_id": body.native_id, "processing": True, "resumed_from": start_id}

    @app.post("/api/chat")
    def chat(body: ChatBody, request: Request):
        """A chat *now*-dispatch: spawn the isolated container, stream its Stream back as SSE."""
        if stream_reader is None:
            raise HTTPException(status_code=501, detail="stream relay not wired")
        subject = subject_of(request)  # server-derived (P20); body.subject is ignored
        session = body.session or units.DEFAULT_CHAT_SESSION
        # Upsert the durable index on use: a new thread is titled by its first prompt; an existing one
        # just bumps last_active (title preserved).
        is_new = not any(r["session"] == session for r in sess.list(subject))
        sess.upsert(subject, session,
                    title=_truncate_title(body.prompt) if is_new else None)
        # Ground the chat in the terminal's ACTIVE meeting (if any) by AUTHORIZING a per-turn,
        # meeting-scoped tool — the agent reads the transcript through meetings' published /transcripts
        # contract on demand, never a file (P23/P3). cookbook pattern #1: a tool is granted only when the
        # context warrants, scoped to the one resource. The scoped token is minted downstream by the
        # dispatcher from the invocation's `tools` (P15 — the user key never enters the worker).
        ctx, tools, prompt = _meeting_grounding(body.active, session, body.prompt)
        inv = units.make_dispatch(
            subject=subject, trigger="message",
            start=units.entrypoint(inline=prompt), context=ctx, tools=tools,
        )
        unit_id = dispatcher.dispatch(inv)  # spawn-or-touch the thread's warm chat unit
        return StreamingResponse(
            _sse(stream_reader.read(unit_id)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
        )

    @app.post("/api/chat/reset")
    def chat_reset(body: ChatBody, request: Request):
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
        try:
            turns = wsr.history(subject, session)
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

    @app.get("/api/workspace/tree")
    def ws_tree(request: Request, hidden: bool = False):
        return {"files": wsr.tree(subject_of(request), hidden=hidden)}

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
    def ws_file(request: Request, path: str):
        subject = subject_of(request)
        try:
            content = wsr.read(subject, path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"path": path, "content": content}

    @app.get("/api/workspace/git")
    def ws_git(request: Request):
        """Real source-control state (branch · working changes · recent commits) of the workspace."""
        try:
            return wsr.git_state(subject_of(request))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")

    # ── workspace lifecycle (SCAFFOLD / TODO(phase-6)) — init from a validated template, swap which
    # validated workspace/template the next dispatch mounts. The seams exist downstream (seeding.seed_workspace
    # for init; VEXA_WORKSPACE_REPO/REF in dispatch/spawn for swap, bridge resolves per-meeting) — Phase 6
    # surfaces them here and wires the slim-client init_workspace()/use_workspace().
    @app.post("/api/workspace/init", status_code=201)
    def ws_init(request: Request):
        """Materialize this subject's workspace from the VALIDATED workspace-seed template via the single
        seed primitive (shared.seeding.seed_workspace). Idempotent — an existing workspace (`.git`) is
        returned untouched. The seam the worker uses on first dispatch, surfaced as a control."""
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
        return {"workspace": str(ws), "seeded": not existed, "already_initialized": existed}

    @app.post("/api/workspace/swap", status_code=501)
    def ws_swap(request: Request):
        """Select which validated workspace/template the next dispatch MOUNTS (carry VEXA_WORKSPACE_REPO/REF).
        TODO(phase-6): the selection seam exists in dispatch.py/spawn.py (and bridge resolves per-meeting);
        surfacing it as a control needs the dispatch to accept + thread a per-subject workspace selection."""
        raise HTTPException(status_code=501, detail="workspace swap not wired yet — needs dispatch selection (Phase 6)")

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

        resume_t, resume_o = _decode_sse_cursor(request.headers.get("last-event-id"))

        def gen():
            import redis

            r = redis.from_url(redis_url, decode_responses=True)
            tkey = f"tc:meeting:{meeting_id}"
            okey = f"unit:agent-meet-{session_uid}:out"
            # Resume EXACTLY from the client's last-seen cursors when present (gapless reconnect);
            # otherwise seed a bounded recent tail then live-tail (fresh connect).
            last = {tkey: resume_t or "$", okey: resume_o or "$"}
            idle = 0
            ending = False  # transcript hit session_end — drain trailing cards before meeting-end

            def cursor():
                return _encode_sse_cursor(last, tkey, okey)

            def seg_events(payload):
                for seg in payload.get("segments", []):
                    yield ({"type": "transcript", "speaker": seg.get("speaker"),
                            "text": seg.get("text"), "t": seg.get("start"),
                            "tsMs": seg.get("abs_start_ms"),
                            "completed": seg.get("completed", True),
                            "id": seg.get("segment_id")}, cursor())

            if resume_t is None:   # fresh connect → seed the bounded recent transcript tail
                seed_rows = list(reversed(r.xrevrange(tkey, count=MEETING_STREAM_TRANSCRIPT_REPLAY) or []))
                for entry_id, fields in seed_rows:
                    last[tkey] = entry_id
                    payload = json.loads(fields.get("payload", "{}"))
                    if payload.get("type") == "session_end":
                        ending = True
                        last.pop(tkey, None)
                        continue
                    yield from seg_events(payload)
            if resume_o is None:   # fresh connect → seed the output (cards/notes) replay
                output_seed_rows = list(reversed(r.xrevrange(okey, count=MEETING_STREAM_OUTPUT_REPLAY) or []))
                for entry_id, fields in output_seed_rows:
                    last[okey] = entry_id
                    yield (json.loads(fields.get("event", "{}")), cursor())

            while True:
                # once the transcript ends, poll ONLY the out-stream (briefly) to flush trailing cards.
                resp = r.xread(last, count=500, block=1500 if ending else 15000)
                if not resp:
                    if ending:               # out-stream drained → now it's safe to end
                        live.drop(session_uid)  # leaves the terminal's live-meetings feed
                        yield ({"type": "meeting-end"}, cursor())
                        return
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
                                ending = True            # don't end yet — finish draining the out-stream
                                last.pop(tkey, None)     # session_end is the last transcript entry
                                break
                            yield from seg_events(payload)
                        else:
                            yield (json.loads(fields.get("event", "{}")), cursor())

        return StreamingResponse(
            _sse(gen()), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# ── ASGI entrypoint (PEP 562) — `uvicorn control_plane.api:app` resolves this lazily ──────────────────
def _build_production_app() -> FastAPI:
    from shared.adapters import LocalIdentityMinter, RedisStreamReader, RuntimeHttpClient, SchedulerHttpClient
    from shared.config import load_settings
    from control_plane.workspace_routines import start_workspace_routine_reconciler

    settings = load_settings()
    runtime = RuntimeHttpClient(settings.runtime_api_url)
    scheduler = SchedulerHttpClient(settings.runtime_api_url)
    identity = LocalIdentityMinter(settings.dispatch_signing_key.get_secret_value())
    dispatcher = Dispatcher(settings, runtime, identity)
    invocations_url = settings.agent_api_self_url.rstrip("/") + "/invocations"
    app = create_app(
        dispatcher,
        stream_reader=RedisStreamReader(settings.redis_url),
        reader=WorkspaceReader(settings.workspaces_dir),
        scheduler=scheduler,
        invocations_url=invocations_url,
        redis_url=settings.redis_url,
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
    from control_plane import transcription_watcher
    transcription_watcher.start(settings.redis_url, dispatcher, app.state.live_meetings)
    return app


def __getattr__(name: str):
    if name == "app":
        return _build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
