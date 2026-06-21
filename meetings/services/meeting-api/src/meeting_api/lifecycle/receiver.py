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

from .machine import IllegalTransition, LifecycleSink, MeetingStore
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


def create_app(store: Optional[MeetingStore] = None) -> FastAPI:
    """Build the receiver app. `store` lets the eval inspect record state after POSTs."""
    app = FastAPI(title="meeting-api · lifecycle receiver", version="0.12.0")
    # Use `is None` — an empty MeetingStore is falsy (len == 0), so `store or ...`
    # would silently swap in a different store than the caller's.
    sink = LifecycleSink(store=store if store is not None else MeetingStore())
    app.state.sink = sink
    app.state.store = sink.store
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
        try:
            rec = sink.apply(body)
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
            },
        )

    return app
