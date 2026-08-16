"""``create_app(config, ...) -> FastAPI`` — the chat door.

The whole surface is four routes:

| Route | What it does |
|---|---|
| ``GET /health`` | liveness + the signing-key *fingerprint* (never the key) |
| ``GET /door/verify?t=…`` | verify the magic link (single-use), lazily create the identity, set the session cookie, redirect to the record |
| ``GET /door/meeting/{id}`` | the record view + the reply box, scope-checked |
| ``POST /door/steer`` | append a dated entry to the person's personal-instructions doc |

**Lazy identity** happens in exactly one place — the first *successful* verify. A malformed,
expired, replayed or tampered token creates nothing, which is the point of "nothing is stored
on their behalf before that".

**Scope** is enforced on every record read: a session may read the one meeting its token
names, and group context is reachable only at ``member`` scope (see :mod:`chat_door.scope`).
Group context is not rendered at all in v0 — the check exists so the boundary is testable now
rather than retrofitted later.

Both the meetings client and the store are injectable so the tests drive the shipped app
in-process against a fake upstream and a temp directory.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import pages, scope as scope_rules
from .config import DoorConfig
from .local_records import LocalCorpusRecords
from .meetings_client import MeetingsClient
from .store import FileIdentityStore, IdentityStore
from .tokens import TokenError, TokenSigner

SESSION_COOKIE = "vexa_door_session"


def create_app(
    config: DoorConfig,
    *,
    signer: TokenSigner | None = None,
    store: IdentityStore | None = None,
    meetings: MeetingsClient | None = None,
) -> FastAPI:
    app = FastAPI(title="Vexa chat door (dev v0)", docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.signer = signer or TokenSigner(bytes(config.signing_key))
    app.state.store = store or FileIdentityStore(config.store_dir)
    if meetings is not None:
        app.state.meetings = meetings
    elif config.records_dir is not None:
        # Dev-only escape hatch — the page labels every record it serves this way.
        app.state.meetings = LocalCorpusRecords(config.records_dir)
    else:
        app.state.meetings = MeetingsClient(
            config.meetings_url, api_key=config.meetings_api_key
        )

    @app.middleware("http")
    async def _door_headers(request: Request, call_next):
        """Two headers the whole surface needs, for one reason each.

        ``Referrer-Policy: no-referrer`` — the verify URL *is* the token, so no request the
        browser makes afterwards may carry it in a ``Referer``.
        ``Cache-Control: no-store`` — every page here is one person's record behind a bearer
        link; a shared cache holding it is the same leak by another route.
        """
        response = await call_next(request)
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "chat-door",
                "stage": "dev-v0",
                # The fingerprint lets two processes agree they hold the same key without
                # either of them ever emitting it.
                "signing_key_fingerprint": config.signing_key.fingerprint,
                "signing_key_ephemeral": config.signing_key.generated,
            }
        )

    @app.get("/door/verify")
    def verify(request: Request, t: str = "") -> Response:
        if not t:
            return HTMLResponse(
                pages.error_page(
                    "This link is incomplete",
                    "The address is missing its token. Open the link from your artifact email.",
                ),
                status_code=400,
            )
        try:
            claims = app.state.signer.verify(t, expect_kind="link")
        except TokenError as exc:
            return HTMLResponse(
                pages.error_page(
                    "This link no longer opens",
                    _explain(exc.reason),
                    exc.reason,
                ),
                status_code=401,
            )

        # First successful verification is the moment the person becomes a user.
        app.state.store.ensure_user(claims.subject, now=datetime.now(timezone.utc))

        session = app.state.signer.issue(
            kind="session",
            subject=claims.subject,
            meeting_id=claims.meeting_id,
            scope=scope_rules.normalize(claims.scope),
            ttl_seconds=config.session_ttl_seconds,
        )
        response = RedirectResponse(f"/door/meeting/{claims.meeting_id}", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session,
            max_age=config.session_ttl_seconds,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/door/meeting/{meeting_id}")
    def meeting_page(request: Request, meeting_id: str) -> Response:
        session = _session_of(app, request)
        if isinstance(session, Response):
            return session
        decision = scope_rules.may_read_meeting(session.meeting_id, meeting_id)
        if not decision.allowed:
            return HTMLResponse(
                pages.error_page(
                    "Not yours to open",
                    "This door was opened for a different meeting. Use the link from the "
                    "artifact email for the meeting you want.",
                    decision.reason,
                ),
                status_code=403,
            )
        record = app.state.meetings.fetch(meeting_id)
        return HTMLResponse(
            pages.record_page(
                subject=session.subject,
                scope=scope_rules.normalize(session.scope),
                record=record,
                instructions_excerpt=app.state.store.read_instructions(session.subject),
            )
        )

    @app.post("/door/steer")
    def steer(request: Request, meeting_id: str = Form(...), text: str = Form(...)) -> Response:
        session = _session_of(app, request)
        if isinstance(session, Response):
            return session
        decision = scope_rules.may_read_meeting(session.meeting_id, meeting_id)
        if not decision.allowed:
            return HTMLResponse(
                pages.error_page(
                    "Not yours to steer",
                    "This door was opened for a different meeting.",
                    decision.reason,
                ),
                status_code=403,
            )
        if not (text or "").strip():
            return HTMLResponse(
                pages.error_page("Nothing to save", "Write something first."), status_code=400
            )
        app.state.store.append_instruction(session.subject, text, meeting_id=meeting_id)
        record = app.state.meetings.fetch(meeting_id)
        return HTMLResponse(
            pages.record_page(
                subject=session.subject,
                scope=scope_rules.normalize(session.scope),
                record=record,
                steer_ack="Appended to your personal instructions.",
                instructions_excerpt=app.state.store.read_instructions(session.subject),
            )
        )

    return app


def _session_of(app: FastAPI, request: Request):
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return HTMLResponse(
            pages.error_page(
                "No session here",
                "Open the link from your artifact email — that is the only door.",
                "no_session",
            ),
            status_code=401,
        )
    try:
        return app.state.signer.verify(raw, expect_kind="session")
    except TokenError as exc:
        return HTMLResponse(
            pages.error_page("Your session ended", _explain(exc.reason), exc.reason),
            status_code=401,
        )


def _explain(reason: str) -> str:
    return {
        "token_expired": "This link has expired. Ask for a fresh artifact email.",
        "token_already_used": "This link was already opened once. Links are single-use; "
        "your session cookie keeps you in from the same browser.",
        "token_signature_invalid": "This link was altered in transit and will not open.",
        "token_malformed": "This link is not a valid door link.",
        "token_wrong_kind": "This is not a door link.",
        "no_session": "Open the link from your artifact email.",
    }.get(reason, "This link will not open.")
