"""``create_app(mailroom, ...) -> FastAPI`` — liveness plus the operator's four read/act routes.

The mailroom is a POLLER, not an HTTP product: the app exists so the process has a liveness probe
(gate:health), so an operator can watch what the mailbox did without reading a state file on a
container, and so a smoke run can drive one poll on demand instead of waiting for the tick.

| Route | What |
|---|---|
| `GET /health` | liveness — `{status:"ok", service:"mailroom"}`, no auth, no mailbox hop |
| `POST /internal/poll` | run ONE poll now → the outcome tally (what a tick would have done) |
| `GET /internal/bindings` | the series↔meeting bindings this mailbox holds |
| `GET /internal/notices` | the recorded "no group effect" notices, newest last |

Everything under `/internal` is guarded by ``MAILROOM_INTERNAL_SECRET`` when one is configured
(``X-Internal-Secret``), and the service is never fronted by the gateway — it has no public
surface at all. With no secret configured the routes are open, which is correct for a
loopback-bound dev container and is asserted, not assumed, in ``tests/test_access.py``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, HTTPException

from .service import Mailroom

SERVICE_NAME = "mailroom"


def create_app(mailroom: Optional[Mailroom] = None, *, internal_secret: Optional[str] = None,
               ready: bool = True, reason: str = "") -> FastAPI:
    """The app. ``mailroom=None`` (or ``ready=False``) still serves ``/health`` — a mailbox that is
    not configured must report itself, not fail to boot (capability degrade, the meeting-api
    calendar-sync idiom)."""
    app = FastAPI(title="Vexa mailroom", version="0.12.0")

    def _guard(secret: Optional[str]) -> None:
        if internal_secret and (secret or "") != internal_secret:
            raise HTTPException(status_code=401, detail="internal secret required")

    def _require_mailroom() -> Mailroom:
        if mailroom is None:
            raise HTTPException(status_code=503, detail=reason or "mailroom is not configured")
        return mailroom

    @app.get("/health")
    async def health() -> dict:
        body = {"status": "ok", "service": SERVICE_NAME}
        if not ready or mailroom is None:
            body["ingest"] = {"configured": False, "reason": reason or "not configured"}
        else:
            body["ingest"] = {"configured": True,
                              "workspaces": sorted(mailroom.workspaces.keys())}
        return body

    @app.post("/internal/poll")
    async def poll(x_internal_secret: Optional[str] = Header(default=None)) -> dict:
        _guard(x_internal_secret)
        result = await _require_mailroom().poll_once()
        return result.as_dict()

    @app.get("/internal/bindings")
    async def bindings(x_internal_secret: Optional[str] = Header(default=None)) -> dict:
        _guard(x_internal_secret)
        return {"bindings": list(await _require_mailroom().bindings())}

    @app.get("/internal/notices")
    async def notices(limit: int = 50,
                      x_internal_secret: Optional[str] = Header(default=None)) -> dict:
        _guard(x_internal_secret)
        return {"notices": list(await _require_mailroom().recent_notices(limit))}

    return app
