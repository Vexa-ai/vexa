"""The ``POST /bots`` route — mounts the bot-spawn flow onto the unified meeting-api app.

A mountable ``APIRouter`` (the modular-monolith composition, P2). The caller's identity arrives in
the ``x-user-id`` header the gateway injects after it resolves ``x-api-key`` (the gateway strips any
client-supplied identity header first — anti-spoofing). The route maps the spawn outcomes onto the
HTTP status the gateway forwards verbatim:

  * 201 + ``api.v1`` MeetingResponse on success,
  * 409 when the user already has an active meeting for (platform, native_id),
  * 429 when the runtime kernel rejects the spawn for owner quota,
  * 502 when the kernel could not start the workload.
"""
from __future__ import annotations

import ipaddress
import os
import uuid
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..collector.meeting_link import parse_meeting_url
from .env_flags import env_flag
from .ports import (
    AuthSessionBusy,
    AuthSessionNotConfigured,
    MaxBotsExceeded,
    MeetingRepo,
    QuotaExceeded,
    RuntimeClient,
    SpawnFailed,
    TranscriptionNotConfigured,
)
from .browser_session import request_browser_session
from .invocation import SPAWNABLE_PLATFORMS
from .service import DuplicateMeeting, construct_meeting_url, request_bot

#: Max length of a native meeting id, mirroring the `meetings.platform_specific_id`
#: varchar(255) column. Bounded at the request boundary so an over-long id is a typed
#: 422 here rather than an asyncpg truncation 500 deep in the spawn path (#843).
NATIVE_MEETING_ID_MAX_LEN = 255



def _resolve_recording_enabled(value: Optional[object]) -> bool:
    """Recording default: an explicit request value wins; else the ``RECORDING_ENABLED`` env
    (default ``true``), so a dashboard bot records by default. The request value is type-validated —
    a bool is honored, a string is parsed (``"true"``/``"false"`` etc.), and any other type is a 422
    (NOT silently ``bool()``-coerced, which would turn the string ``"false"`` into ``True``).

    The env is read through ``env_flag``, so a set-but-empty ``RECORDING_ENABLED=`` keeps the
    default instead of resolving False (see env_flags — the v0.12.5 witness bug)."""
    if value is None:
        return env_flag("RECORDING_ENABLED", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    raise HTTPException(status_code=422, detail="recording_enabled must be a boolean")


def _resolve_transcribe_enabled(value: Optional[object]) -> bool:
    """Transcription default: an explicit request value wins; else the ``TRANSCRIBE_ENABLED`` env
    (default ``true``). Type-validated like ``recording_enabled`` (CC3) — a bare ``bool(...)`` turned the
    JSON string ``"false"`` into ``True``, silently ENABLING transcription a caller asked to disable.

    The env is read through ``env_flag``: a set-but-empty ``TRANSCRIBE_ENABLED=`` kept the default
    OFF and shipped capture-only bots to every Lite self-host (the v0.12.5 witness bug)."""
    if value is None:
        return env_flag("TRANSCRIBE_ENABLED", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    raise HTTPException(status_code=422, detail="transcribe_enabled must be a boolean")


def _resolve_automatic_leave(value: Optional[object]) -> dict:
    """Translate the public snake_case timeout names into invocation.v1's camelCase shape.

    Admission keeps its deployment default. The active-phase silence timeout is omitted when the
    caller does not set it, allowing the bot module's configurable ten-minute default to apply.
    """
    if value is None:
        return {"waitingRoomTimeout": 600_000}
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="automatic_leave must be an object")

    allowed = {
        "max_bot_time", "max_wait_for_admission", "max_time_left_alone",
        "no_one_joined_timeout", "waiting_room_timeout", "everyone_left_timeout",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HTTPException(status_code=422, detail=f"automatic_leave has unknown field(s): {', '.join(unknown)}")

    def timeout(primary: str, legacy: Optional[str] = None) -> Optional[int]:
        raw = value.get(primary)
        if raw is None and legacy is not None:
            raw = value.get(legacy)
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise HTTPException(status_code=422, detail=f"automatic_leave.{primary} must be a positive integer")
        return raw

    waiting_room = timeout("max_wait_for_admission", "waiting_room_timeout") or 600_000
    resolved = {"waitingRoomTimeout": waiting_room}
    no_one_joined = timeout("no_one_joined_timeout")
    everyone_left = timeout("max_time_left_alone", "everyone_left_timeout")
    if no_one_joined is not None:
        resolved["noOneJoinedTimeout"] = no_one_joined
    if everyone_left is not None:
        resolved["everyoneLeftTimeout"] = everyone_left
    return resolved


def _validate_meeting_url(url: object) -> str:
    """SSRF hygiene for the caller-supplied ``meeting_url`` passthrough (zoom AND jitsi — the
    bot's browser navigates wherever this points, so an authenticated caller must not be able to
    aim it at internal infrastructure). Entry-point validation, 422 on violation:

      * must parse cleanly and use ``https`` (the bot joins real deployments over TLS only),
      * host must be non-empty and not ``localhost``/``*.localhost``,
      * host must not be an IP literal (deployments are hostname-addressed; IP literals are the
        cheap way to reach loopback/link-local/private ranges — 10.x, 169.254.x, 127.x, …).

    Static checks only — no DNS resolution on the spawn path (a hostname that RESOLVES to a
    private IP is contained by network policy around the bot runtime, and slow-fails there)."""
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(status_code=422, detail="meeting_url must be a non-empty string")
    raw = url.strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"meeting_url does not parse as a URL: {raw!r}")
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=422,
            detail="meeting_url must use https:// — the bot only joins TLS deployments",
        )
    try:
        host = parsed.hostname
    except ValueError:
        host = None
    if not host:
        raise HTTPException(status_code=422, detail="meeting_url must have a valid hostname")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise HTTPException(
            status_code=422,
            detail="meeting_url cannot target localhost",
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # hostname, not an IP literal — OK
    else:
        raise HTTPException(
            status_code=422,
            detail="meeting_url cannot be an IP literal — use the deployment's hostname",
        )
    return raw


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def _resolve_max_concurrent(x_user_limits: Optional[str]) -> Optional[int]:
    """Parse the gateway's ``X-User-Limits`` header → the per-user max-bots cap (P3e).

    The gateway resolves the user via ``/internal/validate`` (identity.v1) and forwards the limit as
    a header (the parent's ``auth.validate_request`` reads ``X-User-Limits`` as a bare int or a JSON
    ``{"max_concurrent_bots"|"max_concurrent": …}``). Absent/unparseable → ``None`` (no pre-check).
    ``0`` is a REAL value (quota depleted — every spawn rejected), not absence."""
    if not x_user_limits:
        return None
    raw = x_user_limits.strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        pass
    try:
        import json

        obj = json.loads(raw)
        if isinstance(obj, dict):
            v = obj.get("max_concurrent_bots", obj.get("max_concurrent"))
            return int(v) if v is not None else None
    except Exception:
        return None
    return None


def _passcode_from_url(meeting_url: str) -> Optional[str]:
    """The passcode a meeting URL itself carries — zoom's ``?pwd=`` / teams' ``?p=`` query param.
    Consulted only on the derive path (url-only body) and only when the body sent no explicit
    ``passcode``; anything else returns None."""
    try:
        query = parse_qs(urlparse(meeting_url).query)
    except Exception:
        return None
    for key in ("pwd", "p"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    return None


def build_router(repo: MeetingRepo, runtime: RuntimeClient) -> APIRouter:
    """The bot-spawn routes over the injected ``MeetingRepo`` + ``RuntimeClient`` ports."""
    router = APIRouter()

    @router.post("/bots", status_code=201)
    async def create_bot(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_limits: Optional[str] = Header(default=None),
        x_user_webhook_url: Optional[str] = Header(default=None),
        x_user_webhook_secret: Optional[str] = Header(default=None),
        x_user_webhook_events: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        max_concurrent = _resolve_max_concurrent(x_user_limits)
        # Per-user webhook config the gateway forwarded from identity (persisted into meeting.data).
        webhook_events = None
        if x_user_webhook_events:
            try:
                import json as _json

                parsed = _json.loads(x_user_webhook_events)
                webhook_events = parsed if isinstance(parsed, dict) else None
            except Exception:
                webhook_events = None
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="body must be an object")

        platform = str(body.get("platform", "")).strip()
        native_meeting_id = str(body.get("native_meeting_id", "")).strip()
        meeting_url = body.get("meeting_url")
        # A caller-supplied meeting_url is an any-URL passthrough to the bot's browser
        # (zoom/jitsi) — validate at the point of entry (SSRF hygiene, 422 on violation).
        if meeting_url is not None:
            meeting_url = _validate_meeting_url(meeting_url)
        passcode = body.get("passcode")
        # ── browser_session (#816): a session addresses ITSELF ──────────────────────────────────
        # api.v1 seals `browser_session` in the Platform enum with native_meeting_id AND meeting_url
        # both optional, because a browser session has no external meeting to join — it IS the
        # session. The server mints the address the caller cannot supply (`bs-<hex>` + an unguessable
        # session_token) and the row is ACTIVE immediately. This is a DISTINCT runtime workload
        # (profile "browser-session"), NOT a meeting bot, so it is handled HERE — before the
        # meeting-bot guards (required-fields, SPAWNABLE_PLATFORMS, constructibility) that all assume
        # a meeting address. Supplying a native_meeting_id or meeting_url alongside it is a
        # contradiction (a session has no address to give), refused typed. This carves the one
        # platform that legitimately has no meeting address while keeping #794's typed 422 for real
        # meetings (a google_meet/zoom/… with no URL/id) fully intact — the negative control.
        if platform == "browser_session":
            if native_meeting_id or meeting_url:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "browser_session addresses itself — do not supply native_meeting_id or "
                        "meeting_url; the server mints the session id"
                    ),
                )
            minted_id = f"bs-{uuid.uuid4().hex[:8]}"
            try:
                meeting = await request_browser_session(
                    repo,
                    runtime,
                    user_id=user_id,
                    native_meeting_id=minted_id,
                    max_concurrent=max_concurrent,
                )
            except (MaxBotsExceeded, QuotaExceeded) as e:
                raise HTTPException(status_code=429, detail=str(e) or "Bot concurrency limit reached")
            except SpawnFailed as e:
                raise HTTPException(
                    status_code=502, detail=str(e) or "Failed to start browser session workload"
                )
            return JSONResponse(status_code=201, content=meeting)
        # api.v1 promise: a meeting_url provided WITHOUT native_meeting_id is parsed to extract
        # platform, native_meeting_id, and passcode (collector.meeting_link — the same parser the
        # planned-meeting routes use). An underivable URL is a typed 422, NEVER a persisted ''
        # key: (platform, native_meeting_id) is the only user-facing address for stop/transcripts,
        # so an empty id would be a 201 that creates a meeting no API call can reach again.
        # Runs AFTER the SSRF validator (derivation never bypasses the URL guard) and only when
        # the explicit id is absent — a supplied native_meeting_id is authoritative.
        if not native_meeting_id and meeting_url:
            derived = parse_meeting_url(meeting_url)
            if derived is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "'native_meeting_id' is required: it could not be derived from "
                        f"meeting_url '{meeting_url}' (unrecognized meeting link)"
                    ),
                )
            derived_platform, native_meeting_id = derived
            if platform and platform != derived_platform:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"platform '{platform}' disagrees with meeting_url "
                        f"(which is a '{derived_platform}' link) — drop one or make them agree"
                    ),
                )
            platform = derived_platform
            if not passcode:
                passcode = _passcode_from_url(meeting_url)
        if not platform or (not native_meeting_id and not meeting_url):
            raise HTTPException(
                status_code=422,
                detail="'platform' and 'native_meeting_id' (or 'meeting_url') are required",
            )
        # Bound the id to what the column can hold, HERE — not at the INSERT. `meetings
        # .platform_specific_id` is varchar(255); an over-long or NUL-bearing id used to travel the
        # whole spawn path and die on asyncpg's StringDataRightTruncationError — a 500 roughly 5.6s
        # in, while every other malformed field is refused at this boundary with a typed 422 (#843).
        # Applied after URL-derivation so a derived id is bounded too.
        #
        # Length and control bytes ONLY. The id's SHAPE is deliberately not validated: ids that look
        # wrong do join (a bare-numeric Teams id transcribed a real meeting in production), so a
        # format rule would refuse working meetings while fixing nothing.
        if native_meeting_id:
            if len(native_meeting_id) > NATIVE_MEETING_ID_MAX_LEN:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"'native_meeting_id' is {len(native_meeting_id)} characters; "
                        f"the maximum is {NATIVE_MEETING_ID_MAX_LEN}"
                    ),
                )
            if any(ch == "\x7f" or ch < " " for ch in native_meeting_id):
                raise HTTPException(
                    status_code=422,
                    detail="'native_meeting_id' contains control characters",
                )
        # Reject a platform the meeting-bot flow cannot invoke, up front (→ 422) and BEFORE any DB
        # write. Without this, a platform outside the sealed invocation.v1 enum sailed past the
        # constructibility guard below, wrote its `requested` meeting row, and then died inside
        # build_invocation's schema validation: a 500, plus an ORPHANED active row that 409s the
        # user's retry on the dedup guard. The refusal names the real state of the world.
        # (`browser_session` is in api.v1's WIDER enum but is NOT a meeting bot — it is handled by
        # its own spawn branch above and never reaches here.)
        if platform not in SPAWNABLE_PLATFORMS:
            supported = ", ".join(sorted(SPAWNABLE_PLATFORMS))
            raise HTTPException(
                status_code=422,
                detail=(
                    f"platform '{platform}' cannot be spawned as a meeting bot — supported: "
                    f"{supported}"
                ),
            )
        # Reject an unsupported platform up front (→ 422), instead of letting the spawn flow fail deep in
        # the invocation builder with an uncaught jsonschema error (→ 500): a meeting URL must be
        # CONSTRUCTIBLE — the platform has a URL template (google_meet/teams), or the caller supplied an
        # explicit meeting_url (required for zoom AND jitsi — a jitsi room name is deployment-scoped, so
        # only the full URL says WHICH deployment to join).
        if not meeting_url and construct_meeting_url(platform, native_meeting_id) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported platform '{platform}' without a meeting_url — "
                    "use google_meet/teams, or provide meeting_url (required for zoom/jitsi)"
                ),
            )

        transcribe_enabled = _resolve_transcribe_enabled(body.get("transcribe_enabled"))

        try:
            meeting = await request_bot(
                repo,
                runtime,
                user_id=user_id,
                platform=platform,
                native_meeting_id=native_meeting_id,
                bot_name=body.get("bot_name"),
                passcode=passcode,
                meeting_url=meeting_url,
                language=body.get("language"),
                task=body.get("task"),
                transcription_tier=body.get("transcription_tier", "realtime"),
                recording_enabled=_resolve_recording_enabled(body.get("recording_enabled")),
                transcribe_enabled=transcribe_enabled,
                automatic_leave=_resolve_automatic_leave(body.get("automatic_leave")),
                # P3c — continue_meeting is accepted off the OPEN api.v1 request body (MeetingCreate
                # has no additionalProperties:false), so the wire is not rejected; documenting it as
                # a public typed field needs a vN+1 (lane:contract) — see the bot_spawn README.
                continue_meeting=bool(body.get("continue_meeting", False)),
                max_concurrent=max_concurrent,
                webhook_url=x_user_webhook_url,
                webhook_secret=x_user_webhook_secret,
                webhook_events=webhook_events,
            )
        except TranscriptionNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except AuthSessionNotConfigured as e:
            # Deployment misconfiguration (BOT_AUTHENTICATED without a complete userdata store) —
            # a service-side 503 like the transcription gate, never a silent anonymous join.
            raise HTTPException(status_code=503, detail=str(e))
        except AuthSessionBusy as e:
            # One stored session, one live bot: the second concurrent authenticated spawn is
            # refused naming the conflicting meeting (per-identity serialization, #725).
            raise HTTPException(status_code=409, detail=str(e))
        except DuplicateMeeting as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (MaxBotsExceeded, QuotaExceeded) as e:
            raise HTTPException(status_code=429, detail=str(e) or "Bot concurrency limit reached")
        except SpawnFailed as e:
            raise HTTPException(status_code=502, detail=str(e) or "Failed to start bot workload")

        return JSONResponse(status_code=201, content=meeting)

    return router
