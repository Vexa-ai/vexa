"""``python -m vexa_mailroom`` — the production entrypoint: the poll loop plus the liveness app.

Composition root. It reads the environment (``config.settings_from_env``), builds the real
adapters (Mailpit + the public API), and runs ONE background loop alongside uvicorn: every
``MAILROOM_POLL_INTERVAL_S`` it calls ``Mailroom.poll_once()``, logs the tally, and sleeps. A poll
that raises is logged and the loop continues — an unreachable mailbox is a degraded mailbox, not a
dead service.

**A misconfigured mailroom boots.** No API key, no workspace map, no reachable Mailpit: the app
still serves ``/health`` and says `ingest.configured=false` with the reason. The alternative — a
crash loop — hides the reason inside a restart counter.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .adapters import MailpitSource, MeetingApiClient
from .app import create_app
from .config import Settings, settings_from_env
from .service import Mailroom
from .store import FileStore

log = logging.getLogger("vexa_mailroom")


class _DryRunMeetingApi:
    """Decides everything, mutates nothing — ``MAILROOM_DRY_RUN=1`` for the first live smoke."""

    def __init__(self) -> None:
        self._next_id = -1

    async def create_planned_meeting(self, **kwargs) -> dict:
        self._next_id -= 1
        log.info("mailroom[dry-run]: would POST /meetings %s", kwargs)
        return {"id": self._next_id, "status": "dry-run"}

    async def update_planned_meeting(self, meeting_id: int, **fields) -> dict:
        log.info("mailroom[dry-run]: would PATCH /meetings/%s %s", meeting_id, fields)
        return {"id": meeting_id, "status": "dry-run"}

    async def cancel_planned_meeting(self, meeting_id: int) -> bool:
        log.info("mailroom[dry-run]: would DELETE /meetings/%s", meeting_id)
        return True


def build(settings: Optional[Settings] = None) -> tuple[Settings, Optional[Mailroom], str]:
    """(settings, mailroom-or-None, reason) — the composition, separated so tests can call it."""
    s = settings or settings_from_env()
    missing = [name for name, ok in (("MAILROOM_API_KEY", bool(s.api_key) or s.dry_run),
                                     ("MAILROOM_WORKSPACE_MAP", bool(s.workspaces)),
                                     ("MAILPIT_URL", bool(s.mailpit_url))) if not ok]
    if missing:
        return (s, None, "unset: " + ", ".join(missing))
    store = FileStore(s.state_path)
    meetings = _DryRunMeetingApi() if s.dry_run else MeetingApiClient(s.meeting_api_url, s.api_key)
    mailroom = Mailroom(
        source=MailpitSource(s.mailpit_url),
        meetings=meetings,
        store=store,
        notices=store,
        workspaces=s.workspaces,
        auto_join=s.auto_join,
        batch_limit=s.batch_limit,
    )
    return (s, mailroom, "")


async def _poll_loop(mailroom: Mailroom, interval_s: float) -> None:
    while True:
        try:
            result = await mailroom.poll_once()
            if result.outcomes:
                log.info("mailroom: poll → %s", result.counts)
        except Exception:
            log.exception("mailroom: poll failed")
        await asyncio.sleep(interval_s)


def main() -> None:
    import uvicorn

    settings, mailroom, reason = build()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    if mailroom is None:
        log.warning("mailroom: ingest disabled — %s", reason)

    app = create_app(mailroom, internal_secret=settings.internal_secret or None,
                     ready=mailroom is not None, reason=reason)

    if mailroom is not None:
        @app.on_event("startup")
        async def _start() -> None:                          # pragma: no cover - runtime wiring
            app.state.poller = asyncio.create_task(
                _poll_loop(mailroom, settings.poll_interval_s))

        @app.on_event("shutdown")
        async def _stop() -> None:                           # pragma: no cover - runtime wiring
            task = getattr(app.state, "poller", None)
            if task:
                task.cancel()

    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
