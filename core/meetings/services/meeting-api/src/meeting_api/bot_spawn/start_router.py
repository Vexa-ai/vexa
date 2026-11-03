"""``POST /internal/meetings/{meeting_id}/start`` — Flows starts the bot for one planned row (ADR-0052 §5).

Reached only through the gateway's service door (Flows' key), which signs the subject's identity on
the hop; the public edge has no row for it. The subject must own the row. Everything else — the
locks, the safeguards, the spawn with ``join_at`` — is ``planned_start.start_planned_meeting``.

Answers (JSON ``{"code", "detail", …}``):

* 201 ``started`` — the bot's pod is starting; ``meeting`` is the claimed row, ``join_at`` when it
  opens the meeting.
* 404 ``not_found`` — no such row, or not the subject's.
* 409 ``not_scheduled`` — no longer ``scheduled``, ``auto_join`` off, stopped, deleted by its owner,
  no link or start time: nothing to do, ever. ``past_deadline`` — start + ``BOOKED_GRACE_S`` passed. ``already_live`` — a
  bot already owns the room (``holder``). ``too_early`` — before the pod may start (``Retry-After``).
* 429 ``max_bots`` / ``backoff`` — the account's ceiling is full, or this occurrence is inside a hold
  a prior attempt earned; ``Retry-After`` says when to call again.
* 402 ``service_denied`` — no credit. 403 ``blocked`` — a block list refuses it. 422
  ``meeting_host_refused``. 503 (``Retry-After``) — identity, authority, STT or the runtime is not
  available right now.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from .planned_start import start_planned_meeting

START_PATH = "/internal/meetings/{meeting_id}/start"


@dataclass
class StartDeps:
    """What the route needs that a request does not carry — the same things the sweep is given.

    ``fetch_bot_context`` None = no identity edge configured: the account's cap cannot be read, so
    the start is refused unless ``allow_uncapped`` (``AUTO_JOIN_ALLOW_UNCAPPED=1``)."""

    fetch_bot_context: Optional[Callable[[int], Awaitable[Optional[dict]]]] = None
    publish_status: Optional[Callable[..., Awaitable[None]]] = None
    transcribe_gate: Optional[Callable[[], Optional[str]]] = None
    token_secret: Optional[str] = None
    redis_url: Optional[str] = None
    redis_grant: Any = None
    allow_uncapped: bool = False
    deployment_block: Any = None
    retry_backoff_s: float = 300.0


def build_start_router(repo, runtime, authority=None, *, deps: Optional[StartDeps] = None) -> APIRouter:
    router = APIRouter()
    deps = deps or StartDeps()

    @router.post(START_PATH, include_in_schema=False)
    async def start_planned(meeting_id: int, x_user_id: Optional[str] = Header(default=None)):
        if not x_user_id or not x_user_id.strip().isdigit():
            raise HTTPException(status_code=401, detail="Missing user identity")
        outcome = await start_planned_meeting(
            meeting_id, user_id=int(x_user_id), repo=repo, runtime=runtime, authority=authority,
            fetch_bot_context=deps.fetch_bot_context, publish_status=deps.publish_status,
            transcribe_gate=deps.transcribe_gate, retry_backoff_s=deps.retry_backoff_s,
            token_secret=deps.token_secret, redis_url=deps.redis_url,
            redis_grant=deps.redis_grant, allow_uncapped=deps.allow_uncapped,
            deployment_block=deps.deployment_block,
        )
        body: dict = {"code": outcome.code, "detail": outcome.message, **outcome.extra}
        if outcome.meeting is not None:
            # Only what Flows needs: never the row's data (webhook config, links, passcodes).
            data = outcome.meeting.get("data") if isinstance(outcome.meeting.get("data"), dict) else {}
            body["meeting"] = {"id": outcome.meeting.get("id"),
                               "status": outcome.meeting.get("status")}
            body["join_at"] = data.get("join_at")
        headers = {"Retry-After": str(outcome.retry_after)} if outcome.retry_after else None
        return JSONResponse(status_code=outcome.http, content=body, headers=headers)

    return router
