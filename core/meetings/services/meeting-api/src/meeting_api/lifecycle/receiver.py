"""The lifecycle.v1 HTTP receiver — meeting-api's control-plane callback endpoint.

Mirrors the parent's `/bots/internal/callback/status_change` (callbacks.py): the bot
POSTs a lifecycle.v1 LifecycleEvent; the receiver validates it AT THE SEAM (jsonschema
by path against the sealed `lifecycle.v1` schema — the `runtime/tests/test_api.py`
`_conforms` discipline), drives the FSM via `LifecycleSink`, and surfaces an illegal
transition as HTTP 409.

`create_app()` is the front door for the eval's FastAPI `TestClient`. No DB, no redis —
the record store is in-memory (`MeetingStore`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from referencing import Registry, Resource

from .machine import IllegalTransition, LifecycleSink, MeetingStore, TransitionSource
from .webhook import build_status_change_envelope, build_typed_envelope
from ..obs import TraceMiddleware, log_event


def _load_lifecycle_schema() -> dict:
    """Locate the sealed lifecycle.v1 schema by walking up to the monorepo root.

    The schema is the SEAM (P8) — loaded by path, not imported, so the receiver
    validates against the exact published contract the bot emits to.
    """
    rel = Path("meetings") / "contracts" / "lifecycle.v1" / "lifecycle.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"monorepo root with {rel} not found")


_SCHEMA = _load_lifecycle_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def conforms(obj: Dict[str, Any], shape: str) -> None:
    """Validate `obj` against `lifecycle.v1#/$defs/<shape>` (raises on non-conformance)."""
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


def create_app(
    store: Optional[MeetingStore] = None,
    *,
    on_status_change: Optional[Any] = None,
) -> FastAPI:
    """Build the receiver app. `store` lets the eval inspect record state after POSTs.

    `on_status_change(envelope)` (optional) is the webhook-emit port: each FSM advance builds the
    sealed `meeting.status_change` webhook.v1 envelope and hands it here. The eval injects a sink
    that records every delivery; production wires the WebhookSink. The receiver is a bot callback,
    so transitions it drives carry `transition_source=bot_callback`.
    """
    app = FastAPI(title="meeting-api · lifecycle receiver", version="0.12.0")
    # Use `is None` — an empty MeetingStore is falsy (len == 0), so `store or ...`
    # would silently swap in a different store than the caller's.
    sink = LifecycleSink(store=store if store is not None else MeetingStore())
    app.state.sink = sink
    app.state.store = sink.store
    app.state.status_change_webhooks = []  # every emitted status_change envelope, for the eval
    app.state.typed_webhooks = []  # every emitted TYPED envelope (started/completed/failed)
    # Bind the upstream gateway's X-Trace-Id for each request so this hop's structured logs
    # (logevent.v1) correlate with the gateway's on the same trace_id.
    app.add_middleware(TraceMiddleware)

    @app.get("/health")
    async def health() -> Dict[str, str]:
        # gate:health is the orchestrator's to wire; this is the receiver it points at.
        return {"status": "ok", "records": str(len(sink.store))}

    @app.post("/bots/internal/callback/lifecycle")
    async def lifecycle_callback(request: Request) -> JSONResponse:
        body = await request.json()

        # 1. Validate at the seam — jsonschema by path against the sealed contract.
        try:
            conforms(body, "LifecycleEvent")
        except jsonschema.ValidationError as e:
            log_event(
                "lifecycle_event_rejected",
                audience="system",
                level="warning",
                span="lifecycle.callback",
                fields={"reason": "schema_violation", "detail": e.message},
            )
            return JSONResponse(
                status_code=422,
                content={"status": "error", "detail": f"lifecycle.v1 schema violation: {e.message}"},
            )

        # 2. Drive the FSM. Illegal transitions → 409 (parent surfaces the same rejection).
        #    The receiver is a bot callback → transition_source=bot_callback.
        try:
            change = sink.apply_change(body, transition_source=TransitionSource.BOT_CALLBACK)
        except IllegalTransition as e:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error",
                    "detail": str(e),
                    "connection_id": e.connection_id,
                    "from": e.frm.value if e.frm is not None else None,
                    "to": e.to.value,
                },
            )
        rec = change.record

        # 3. Emit the meeting.status_change webhook (sealed webhook.v1 envelope), plus the TYPED
        #    event the transition maps to (meeting.started / meeting.completed / bot.failed) —
        #    additive: status_change always fires; the typed event rides alongside (recorded on
        #    app.state.typed_webhooks so the status_change log keeps its 1:1-per-advance invariant).
        envelope = build_status_change_envelope(change)
        app.state.status_change_webhooks.append(envelope)
        typed = build_typed_envelope(change)
        if typed is not None:
            app.state.typed_webhooks.append(typed)
        for env in (envelope, typed) if typed is not None else (envelope,):
            if on_status_change is not None:
                maybe = on_status_change(env)
                if hasattr(maybe, "__await__"):
                    await maybe

        # USER-facing: the meeting's lifecycle advanced (surfaced on the user's timeline).
        log_event(
            "meeting_lifecycle_advanced",
            audience="user",
            span="lifecycle.callback",
            meeting_id=rec.connection_id,
            fields={"meeting_status": rec.status.value if rec.status else None},
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "accepted",
                "connection_id": rec.connection_id,
                "meeting_status": rec.status.value if rec.status else None,
                "completion_reason": rec.completion_reason.value if rec.completion_reason else None,
                "failure_stage": rec.failure_stage.value if rec.failure_stage else None,
                "transition_source": change.transition_source.value,
                "status_transition": rec.status_transition,
                "data": rec.data,
            },
        )

    return app
