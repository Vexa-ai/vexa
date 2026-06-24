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
from typing import Iterator, Optional, Protocol, runtime_checkable

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel

from .dispatch import Dispatcher
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


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev)}\n\n"


def create_app(
    dispatcher: Dispatcher,
    *,
    chat_runner: Optional[ChatRunner] = None,
    sessions: Optional[_Sessions] = None,
    reader: Optional[WorkspaceReader] = None,
) -> FastAPI:
    sess = sessions or _Sessions()
    wsr = reader or WorkspaceReader("/workspaces")
    app = FastAPI(title="vexa-agent-api", version="0.12.0")
    app.state.dispatcher = dispatcher
    app.state.sessions = sess

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
    from .adapters import RuntimeHttpClient
    from .chat_runner import SubprocessChatRunner
    from .config import load_settings

    settings = load_settings()
    runtime = RuntimeHttpClient(settings.runtime_api_url)
    dispatcher = Dispatcher(settings, runtime)
    chat = SubprocessChatRunner(
        settings.workspaces_dir,
        seed_dir=settings.workspace_seed_dir or None,
        model=settings.agent_model or None,
    )
    return create_app(dispatcher, chat_runner=chat, reader=WorkspaceReader(settings.workspaces_dir))


def __getattr__(name: str):
    if name == "app":
        return _build_production_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
