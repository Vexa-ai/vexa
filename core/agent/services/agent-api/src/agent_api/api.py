"""api.py — the agent-api HTTP front door (the unit control plane's entrypoint).

A thin FastAPI surface mirroring ``runtime_kernel/api.py``. Routes (the gateway api.v1 proxies these):
  POST /invocations          — the dispatcher sink: a unit.v1 Invocation → a runtime.v1 agent spawn
  POST /api/chat             — a warm chat unit turn, streamed as SSE (api.v1 declared)
  POST /api/chat/reset       — drop a session
  GET  /api/sessions         — list a subject's sessions
  GET  /health               — liveness

The LIVE claude-in-container chat exec is INJECTED as a ``ChatRunner`` (the docker-exec-into-the-warm-
unit adapter lands in MVP0); the front door + the dispatcher are the foundation. When no chat runner is
wired, ``/api/chat`` answers ``501`` honestly (P18/P21 — never a fake stream). Built lazily (PEP 562)
so ``uvicorn agent_api.api:app`` wires the real adapters at startup.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Iterator, Optional, Protocol, runtime_checkable

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from . import routines as routines_mod
from .dispatch import Dispatcher
from .events import event_to_invocation
from .ports import SchedulerPort
from .workspace_reader import WorkspaceReader


@runtime_checkable
class ChatRunner(Protocol):
    """Runs one warm chat-unit turn for a subject over their workspace; yields normalized UnitEvents
    (message-delta / tool-call / tool-result / commit / rejected / done). The real adapter docker-exec's
    `claude -p --output-format stream-json --resume` into the subject's warm unit (MVP0)."""

    def run(
        self,
        prompt: str,
        *,
        subject: str,
        session: Optional[str] = None,
        tools: Iterable[str] = (),
    ) -> Iterator[dict]: ...


class _Sessions:
    """In-memory session index (the foundation; a redis-backed adapter lands with persistence)."""

    def __init__(self) -> None:
        self._by_subject: dict[str, set[str]] = {}

    def list(self, subject: str) -> list[str]:
        return sorted(self._by_subject.get(subject, set()))

    def add(self, subject: str, session: str) -> None:
        self._by_subject.setdefault(subject, set()).add(session)

    def drop(self, subject: str, session: str) -> None:
        self._by_subject.get(subject, set()).discard(session)


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


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev)}\n\n"


def create_app(
    dispatcher: Dispatcher,
    *,
    chat_runner: Optional[ChatRunner] = None,
    sessions: Optional[_Sessions] = None,
    reader: Optional[WorkspaceReader] = None,
    scheduler: Optional[SchedulerPort] = None,
    invocations_url: Optional[str] = None,
    workspace_repo_for: Optional[Callable[[str], str]] = None,
) -> FastAPI:
    sess = sessions or _Sessions()
    wsr = reader or WorkspaceReader("/workspaces")
    repo_for = workspace_repo_for or (lambda subject: f"local:{subject}")
    app = FastAPI(title="vexa-agent-api", version="0.12.0")
    app.state.dispatcher = dispatcher
    app.state.sessions = sess
    app.state.scheduler = scheduler

    @app.get("/health")
    def health():
        ok = dispatcher is not None
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "checks": {"dispatcher": ok}},
            status_code=200 if ok else 503,
        )

    @app.post("/invocations", status_code=202)
    def invocations(invocation: dict = Body(...)):
        """The dispatcher sink — any trigger source POSTs a unit.v1 Invocation here."""
        try:
            workload_id = dispatcher.dispatch(invocation)
        except ValidationError as e:  # non-conformant unit.v1 envelope — fail loud (P18)
            raise HTTPException(status_code=400, detail=f"invalid unit.v1 Invocation: {e.message}")
        return {"workload_id": workload_id}

    @app.post("/api/chat")
    def chat(body: ChatBody):
        if chat_runner is None:
            raise HTTPException(status_code=501, detail="chat runner not wired (lands in MVP0)")
        if body.session:
            sess.add(body.subject, body.session)
        events = chat_runner.run(body.prompt, subject=body.subject, session=body.session)
        return StreamingResponse(
            _sse(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat/reset")
    def chat_reset(body: ChatBody):
        if body.session:
            sess.drop(body.subject, body.session)
        return {"ok": True}

    @app.get("/api/sessions")
    def list_sessions(subject: str):
        return {"sessions": sess.list(subject)}

    # ── routines (MVP2) — a scheduled routine compiles to a schedule.v1 cron job whose body is a
    #    unit.v1 Invocation POSTed back to /invocations when due (the runtime owns the durable cron) ──
    @app.post("/api/routines", status_code=201)
    def create_routine(body: RoutineCreate):
        if scheduler is None or not invocations_url:
            raise HTTPException(status_code=501, detail="scheduler not wired")
        try:
            routine = routines_mod.make_routine(
                subject=body.subject, name=body.name, cron=body.cron, prompt=body.prompt,
            )
            job_spec = routines_mod.compile_to_job(
                routine, invocations_url=invocations_url, workspace_repo=repo_for(body.subject),
            )
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

    # ── events (MVP3) — the GENERIC event-source ingress: any event.v1 Event → a unit.v1 Invocation →
    #    the one Dispatcher. agent-api knows no tool/domain; the unit reaches email/calendar/etc via its
    #    toolbelt. Email-triage, post-meeting, news all POST here (one front door, P6) ──
    @app.post("/events", status_code=202)
    def events(event: dict = Body(...)):
        try:
            invocation = event_to_invocation(event, workspace_repo_for=repo_for)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"invalid event.v1: {e.message}")
        except ValueError as e:  # no plan carried — fail loud (P18)
            raise HTTPException(status_code=422, detail=str(e))
        workload_id = dispatcher.dispatch(invocation)
        return {"workload_id": workload_id, "trigger": invocation["trigger"]}

    @app.get("/api/workspace/tree")
    def ws_tree(subject: str):
        return {"files": wsr.tree(subject)}

    @app.get("/api/workspace/file")
    def ws_file(subject: str, path: str):
        try:
            content = wsr.read(subject, path)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid path")
        if content is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"path": path, "content": content}

    return app


# ── ASGI entrypoint (PEP 562) — `uvicorn agent_api.api:app` resolves this lazily ──────────────────
def _build_production_app() -> FastAPI:
    from .adapters import RuntimeHttpClient, SchedulerHttpClient
    from .chat_runner import SubprocessChatRunner
    from .config import load_settings
    from .tools import ToolRegistry

    settings = load_settings()
    runtime = RuntimeHttpClient(settings.runtime_api_url)
    scheduler = SchedulerHttpClient(settings.runtime_api_url)
    chat = SubprocessChatRunner(
        settings.workspaces_dir,
        seed_dir=settings.workspace_seed_dir or None,
        model=settings.agent_model or None,
        tool_registry=ToolRegistry.from_dir(settings.tools_seed_dir),
    )
    # Scheduled/event units with an inline prompt run in-container via the chat runner (the proven
    # MVP0/MVP1 path); the runtime-workload spawn stays the production isolation target (DECISIONS D5).
    dispatcher = Dispatcher(settings, runtime, local_runner=chat)
    invocations_url = settings.agent_api_self_url.rstrip("/") + "/invocations"
    return create_app(
        dispatcher,
        chat_runner=chat,
        reader=WorkspaceReader(settings.workspaces_dir),
        scheduler=scheduler,
        invocations_url=invocations_url,
    )


def __getattr__(name: str):
    if name == "app":
        return _build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
