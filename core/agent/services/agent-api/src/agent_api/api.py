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
honestly. Built lazily (PEP 562) so ``uvicorn agent_api.api:app`` wires the real adapters at startup.
"""
from __future__ import annotations

import json
import logging
from typing import Iterator, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from . import routines as routines_mod
from . import units
from .dispatch import Dispatcher
from .events import event_to_invocation
from .ports import SchedulerPort, StreamReader
from .workspace_reader import WorkspaceReader

logger = logging.getLogger("agent_api.api")


def _truncate_title(text: str, *, limit: int = 60) -> str:
    """A session's default title — the first prompt, single-lined + truncated."""
    title = " ".join((text or "").split())
    return title[: limit - 1] + "…" if len(title) > limit else title


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
    subject: str
    session: Optional[str] = None


class RoutineCreate(BaseModel):
    """The Routines surface / ``/routine`` create form — compiles to a routine.v1 + a schedule.v1 job."""
    model_config = {"extra": "forbid"}
    subject: str
    name: str
    cron: str
    prompt: str
    run_now: bool = True  # fire one immediate run so the author sees a result without waiting for cron


class MeetingStart(BaseModel):
    """Launch a live-meeting copilot for a REAL meeting. The vexa-cloud bridge POSTs this once it has a
    bot in the meeting; the dispatch then tails ``tc:meeting:{native_id}`` (the stream the bridge feeds)."""
    model_config = {"extra": "forbid"}
    platform: str               # google_meet | teams | zoom
    native_id: str              # the platform meeting id (e.g. a Google Meet code abc-defg-hij)
    subject: str = "u_live"
    title: Optional[str] = None


class MeetingBot(BaseModel):
    """Send OUR self-hosted bot into a meeting from its URL — the terminal's 'add bot' box POSTs this;
    agent-api forwards it to the gateway's POST /bots, and the transcription watcher then auto-attaches
    the copilot once the bot starts transcribing (no per-meeting wiring)."""
    model_config = {"extra": "forbid"}
    url: str
    bot_name: str = "Vexa EI"
    language: str = "en"


class MeetingStop(BaseModel):
    """Remove our bot from a meeting — forwarded to the gateway's DELETE /bots/{platform}/{native_id}."""
    model_config = {"extra": "forbid"}
    native_id: str
    platform: str = "google_meet"


# The meeting copilot's start brief. The in-container worker drives per-beat extraction with its own
# CARD_PROMPT; this is the envelope's entrypoint (continuity = the session file in the workspace).
_MEETING_BRIEF = (
    "You are the live meeting copilot. Watch the meeting transcript as it streams in and surface the "
    "people, companies, topics, decisions, and action items worth acting on."
)


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev)}\n\n"


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

    @app.get("/health")
    def health():
        ok = dispatcher is not None
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "service": "agent-api", "checks": {"dispatcher": ok}},
            status_code=200 if ok else 503,
        )

    @app.post("/invocations", status_code=202)
    def invocations(invocation: dict = Body(...)):
        """The dispatcher sink — any trigger source POSTs a unit.v1 dispatch here."""
        try:
            workload_id = dispatcher.dispatch(invocation)
        except ValidationError as e:  # non-conformant unit.v1 envelope — fail loud (P18)
            raise HTTPException(status_code=400, detail=f"invalid unit.v1 dispatch: {e.message}")
        return {"workload_id": workload_id}

    @app.post("/api/meeting/start", status_code=202)
    def meeting_start(body: MeetingStart):
        """Launch (or touch) a live-meeting copilot for a real meeting — built through the ONE
        ``make_dispatch`` like every other trigger. ``meeting_id == session_uid == native_id`` so the
        transcript wire (``tc:meeting:{id}``), the dispatch (``agent-meet-{id}``), and the terminal all
        key on the same id. The bridge feeds ``tc:meeting:{native_id}``; the worker tails it."""
        inv = units.make_dispatch(
            subject=body.subject, trigger="transcription",
            start=units.entrypoint(inline=_MEETING_BRIEF),
            context={"kind": "meeting", "meeting": {
                "meeting_id": body.native_id, "session_uid": body.native_id, "platform": body.platform,
            }},
        )
        unit_id = dispatcher.dispatch(inv)
        meeting = {
            "meeting_id": body.native_id, "session_uid": body.native_id, "native_id": body.native_id,
            "platform": body.platform, "title": body.title or f"{body.platform} · {body.native_id}",
            "unit_id": unit_id,
        }
        live.add(meeting)
        return meeting

    @app.get("/api/meetings/live")
    def meetings_live():
        """The active meeting copilots — the terminal's live-meetings feed."""
        return {"meetings": live.list()}

    @app.post("/api/meeting/bot", status_code=202)
    def meeting_bot(body: MeetingBot):
        """Send our self-hosted bot into the meeting at ``url`` (forwarded to the gateway's POST /bots).
        The transcription watcher then sees the bot's transcript and attaches the copilot automatically."""
        import os
        import re
        import urllib.error
        import urllib.request

        mt = re.search(r"meet\.google\.com/([a-z0-9]{3}-[a-z0-9]{4}-[a-z0-9]{3})", body.url)
        if not mt:
            raise HTTPException(status_code=400, detail=f"not a Google Meet URL: {body.url!r}")
        native_id = mt.group(1)
        key = os.environ.get("VEXA_BOT_API_KEY", "")
        if not key:
            raise HTTPException(status_code=501, detail="bot launch not configured (VEXA_BOT_API_KEY unset)")
        gw = os.environ.get("VEXA_GATEWAY_URL", "http://gateway:8000").rstrip("/")
        req = urllib.request.Request(
            gw + "/bots",
            data=json.dumps({"platform": "google_meet", "native_meeting_id": native_id,
                             "bot_name": body.bot_name, "language": body.language}).encode(),
            method="POST", headers={"Content-Type": "application/json", "X-API-Key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise HTTPException(status_code=e.code, detail=f"bot launch failed: {e.read().decode()[:200]}")
        return {"platform": "google_meet", "native_id": native_id,
                "meeting_id": res.get("id"), "status": res.get("status")}

    @app.post("/api/meeting/stop", status_code=202)
    def meeting_stop(body: MeetingStop):
        """Remove our bot from the meeting (gateway DELETE /bots/{platform}/{native_id}) and mark the
        meeting stopped in the feed so the terminal can offer to send the bot back."""
        import os
        import urllib.error
        import urllib.request

        key = os.environ.get("VEXA_BOT_API_KEY", "")
        if not key:
            raise HTTPException(status_code=501, detail="bot control not configured (VEXA_BOT_API_KEY unset)")
        gw = os.environ.get("VEXA_GATEWAY_URL", "http://gateway:8000").rstrip("/")
        req = urllib.request.Request(
            f"{gw}/bots/{body.platform}/{body.native_id}", method="DELETE", headers={"X-API-Key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=20):
                pass
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404 = already no active bot — still mark it stopped locally
                raise HTTPException(status_code=e.code, detail=f"stop failed: {e.read().decode()[:200]}")
        live.stop(body.native_id)
        return {"native_id": body.native_id, "stopped": True}

    @app.get("/api/meetings")
    def list_meetings():
        """The terminal's meetings list — live AND past. Proxies meeting-api `GET /meetings` (the user's
        meetings, all statuses, newest first) and MERGES the live copilot registry so a live meeting
        carries its copilot `unit_id`; past meetings carry their recording/transcript handles."""
        import os
        import urllib.error
        import urllib.request

        live_by_id = {m["session_uid"]: m for m in live.list()}
        rows: list[dict] = []
        seen: set[str] = set()
        key = os.environ.get("VEXA_BOT_API_KEY", "")
        if key:
            gw = os.environ.get("VEXA_GATEWAY_URL", "http://gateway:8000").rstrip("/")
            try:
                req = urllib.request.Request(gw + "/meetings?limit=50", headers={"X-API-Key": key})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode() or "{}")
                items = data.get("meetings") if isinstance(data, dict) else (data or [])
                for mt in items or []:
                    native = mt.get("native_meeting_id") or mt.get("native_id")
                    if not native or native in seen:
                        continue
                    seen.add(native)
                    copilot = live_by_id.get(native)
                    db_status = (mt.get("status") or "").lower()
                    is_live = (copilot and copilot.get("status") == "live") or db_status in ("active", "joining", "requested")
                    platform = mt.get("platform") or "google_meet"
                    rows.append({
                        "native_id": native, "platform": platform,
                        "title": f"{'Google Meet' if platform == 'google_meet' else platform} · {native}",
                        "status": "live" if is_live else "past",
                        "start": mt.get("start_time"), "end": mt.get("end_time"),
                        "has_recording": bool((mt.get("data") or {}).get("recordings")),
                        "unit_id": (copilot or {}).get("unit_id"),
                    })
            except Exception:  # noqa: BLE001 — the list must still return the live registry if the proxy fails
                logger.exception("meetings list proxy to meeting-api failed")
        # live copilots not yet reflected in the DB list (just-started) — keep them visible
        for m in live.list():
            if m["session_uid"] not in seen:
                rows.append({
                    "native_id": m["session_uid"], "platform": m.get("platform") or "google_meet",
                    "title": m.get("title"), "status": m.get("status", "live"),
                    "start": None, "end": None, "has_recording": False, "unit_id": m.get("unit_id"),
                })
        return {"meetings": rows}

    @app.post("/api/chat")
    def chat(body: ChatBody):
        """A chat *now*-dispatch: spawn the isolated container, stream its Stream back as SSE."""
        if stream_reader is None:
            raise HTTPException(status_code=501, detail="stream relay not wired")
        session = body.session or units.DEFAULT_CHAT_SESSION
        # Upsert the durable index on use: a new thread is titled by its first prompt; an existing one
        # just bumps last_active (title preserved).
        is_new = not any(r["session"] == session for r in sess.list(body.subject))
        sess.upsert(body.subject, session,
                    title=_truncate_title(body.prompt) if is_new else None)
        inv = units.make_dispatch(
            subject=body.subject, trigger="message",
            start=units.entrypoint(inline=body.prompt), context={"kind": "none", "session": session},
        )
        unit_id = dispatcher.dispatch(inv)  # spawn-or-touch the thread's warm chat unit
        return StreamingResponse(
            _sse(stream_reader.read(unit_id)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "X-Unit-Id": unit_id, "X-Chat-Session": session},
        )

    @app.post("/api/chat/reset")
    def chat_reset(body: ChatBody):
        """Drop a conversation thread: remove it from the index AND delete its continuity file so a
        future turn on the same name starts a fresh conversation (not a resume of the old one)."""
        session = body.session or units.DEFAULT_CHAT_SESSION
        sess.drop(body.subject, session)
        try:
            wsr.drop_session(body.subject, session)
        except Exception:  # noqa: BLE001 — index drop is the contract; the file delete is best-effort
            logger.exception("dropping continuity file failed subject=%s session=%s", body.subject, session)
        return {"ok": True}

    @app.get("/api/sessions")
    def list_sessions(subject: str):
        return {"sessions": sess.list(subject)}

    @app.get("/api/sessions/{session}/history")
    def session_history(session: str, subject: str):
        """The session's prior conversation, as simplified turns the terminal can render (so clicking a
        saved chat re-opens its history). Tolerant: a missing/empty transcript returns ``{turns: []}``;
        an invalid subject/session never 500s."""
        try:
            turns = wsr.history(subject, session)
        except Exception:  # noqa: BLE001 — history is best-effort; a bad path → empty, never an error
            logger.exception("loading session history failed subject=%s session=%s", subject, session)
            turns = []
        return {"turns": turns}

    # ── routines (MVP2) — a scheduled routine compiles to a schedule.v1 cron job whose body is a
    #    unit.v1 dispatch POSTed back to /invocations when due (the runtime owns the durable cron) ──
    @app.post("/api/routines", status_code=201)
    def create_routine(body: RoutineCreate):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        try:
            routine = routines_mod.make_routine(
                subject=body.subject, name=body.name, cron=body.cron, prompt=body.prompt,
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
    def list_routines(subject: str):
        if scheduler is None:
            return {"routines": []}
        cards = [routines_mod.routine_card_from_job(j) for j in scheduler.list_jobs()]
        return {"routines": [c for c in cards if c and c.get("owner") == subject]}

    @app.delete("/api/routines/{routine_id}")
    def delete_routine(routine_id: str, subject: str):
        if scheduler is None:
            raise HTTPException(status_code=501, detail="scheduler not wired")
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
    def ws_tree(subject: str, hidden: bool = False):
        return {"files": wsr.tree(subject, hidden=hidden)}

    @app.get("/api/workspace/file")
    def ws_file(subject: str, path: str):
        try:
            content = wsr.read(subject, path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"path": path, "content": content}

    @app.get("/api/workspace/git")
    def ws_git(subject: str):
        """Real source-control state (branch · working changes · recent commits) of the workspace."""
        try:
            return wsr.git_state(subject)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid subject")

    @app.get("/api/meeting/stream")
    def meeting_stream(meeting_id: str, session_uid: str):
        """SSE feed for a LIVE meeting — merges the transcript Stream (`tc:meeting:{id}`) and the
        copilot's output Stream (`unit:agent-meet-{sid}:out`) into one feed the terminal renders:
        transcript lines + proactive `card`s + the agent working (`message-delta`/`tool-call`)."""
        if not redis_url:
            raise HTTPException(status_code=501, detail="redis not wired")

        def gen():
            import redis

            r = redis.from_url(redis_url, decode_responses=True)
            tkey = f"tc:meeting:{meeting_id}"
            okey = f"unit:agent-meet-{session_uid}:out"
            last = {tkey: "0", okey: "0"}
            idle = 0
            ending = False  # transcript hit session_end — drain trailing cards before meeting-end
            while True:
                # once the transcript ends, poll ONLY the out-stream (briefly) to flush trailing cards.
                # on a reconnect both streams carry a full backlog at once: end the transcript LAST so
                # session_end can't terminate the generator before the card Stream is replayed.
                resp = r.xread(last, count=500, block=1500 if ending else 15000)
                if not resp:
                    if ending:               # out-stream drained → now it's safe to end
                        live.drop(session_uid)  # leaves the terminal's live-meetings feed
                        yield {"type": "meeting-end"}
                        return
                    idle += 15000
                    if idle >= 600000:
                        return
                    yield {"type": "ping"}
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
                            for seg in payload.get("segments", []):
                                yield {"type": "transcript", "speaker": seg.get("speaker"),
                                       "text": seg.get("text"), "t": seg.get("start"),
                                       "completed": seg.get("completed", True),
                                       "id": seg.get("segment_id")}
                        else:
                            yield json.loads(fields.get("event", "{}"))

        return StreamingResponse(
            _sse(gen()), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# ── ASGI entrypoint (PEP 562) — `uvicorn agent_api.api:app` resolves this lazily ──────────────────
def _build_production_app() -> FastAPI:
    from .adapters import LocalIdentityMinter, RedisStreamReader, RuntimeHttpClient, SchedulerHttpClient
    from .config import load_settings
    from .workspace_routines import start_workspace_routine_reconciler

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
    from . import transcription_watcher
    transcription_watcher.start(settings.redis_url, dispatcher, app.state.live_meetings)
    return app


def __getattr__(name: str):
    if name == "app":
        return _build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
