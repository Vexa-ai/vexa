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
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..collector.meeting_link import parse_meeting_url
from ..service_authority import (
    ServiceAuthorityDenied,
    ServiceAuthorityUnavailable,
)
from .env_flags import InvalidFlagValue, parse_flag, resolve_spawn_flag
from .ports import (
    AuthSessionBusy,
    AuthSessionNotConfigured,
    MaxBotsExceeded,
    MeetingRepo,
    MeetingStopped,
    QuotaExceeded,
    RuntimeClient,
    SpawnFailed,
    TranscriptionNotConfigured,
)
from .invocation import SPAWNABLE_PLATFORMS
from .service import (
    DuplicateMeeting,
    construct_meeting_url,
    request_bot,
    resolve_teams_base_host,
)

#: Max length of a native meeting id, mirroring the `meetings.platform_specific_id`
#: varchar(255) column. Bounded at the request boundary so an over-long id is a typed
#: 422 here rather than an asyncpg truncation 500 deep in the spawn path (#843).
NATIVE_MEETING_ID_MAX_LEN = 255

#: URL-structural characters that must never appear in a native_meeting_id. The id is
#: interpolated into a URL PATH SEGMENT (`construct_meeting_url` — google_meet/teams) and reused
#: as the DELETE path param and the dashboard lookup key, so any of these breaks that use (#892):
#: a Teams passcode left on the id (`…982?p=X8hc…`) built `…/meetup-join/…982?p=X8hc…`
#: (join_failure) and stored an unfindable `platform_specific_id`. No valid id across platforms
#: carries them — Meet dash-codes (`abc-defg-hij`), Zoom digits, Teams `19:…@thread.v2` / bare
#: short ids, and Jitsi rooms all exclude `? # & = /` and whitespace (see collector.meeting_link).
NATIVE_MEETING_ID_URL_CHARS = "?#&=/"

#: Top-level body keys that MEAN "passcode" but are not the api.v1 field. A caller who reaches for
#: one of these is asking for a credential to be used; accepting the request and ignoring the key
#: hands them a bot that joins nothing and a 201 that says it worked — the failure mode a hosted
#: integrator reported after spending hours on it (#892 A2). Named and refused, with the real
#: field in the message.
#:
#: Deliberately a NAMED FAMILY rather than a blanket unknown-key guard. The api.v1 ``POST /bots``
#: request body is OPEN by contract (no ``additionalProperties: false``; ``continue_meeting`` and
#: ``teams_base_host`` both ride on it), so refusing every undeclared key would be a contract
#: break that 422s working integrations — including the ones this refusal exists to protect. The
#: silent-drop class this closes is the one where the DROPPED FIELD IS A CREDENTIAL.
PASSCODE_ALIASES = (
    "password",
    "meeting_password",
    "meetingPassword",
    "meeting_passcode",
    "meetingPasscode",
    "passCode",
    "pass_code",
    "pwd",
)



def _resolve_recording_enabled(value: Optional[object]) -> bool:
    """Recording default for POST /bots: the HTTP skin over the shared ``resolve_spawn_flag`` —
    an explicit request value wins, else the ``RECORDING_ENABLED`` env (default ``true``), so a
    dashboard bot records by default. An unparseable value is a 422, never a silent ``bool()``
    coercion (which would turn the string ``"false"`` into ``True``).

    The resolution itself lives in ``env_flags`` so the auto-join sweep resolves IDENTICALLY
    (#1216): calendar-joined bots record exactly like manual ones."""
    try:
        return resolve_spawn_flag("RECORDING_ENABLED", value, default=True,
                                  field="recording_enabled")
    except InvalidFlagValue as e:
        raise HTTPException(status_code=422, detail=str(e))


def _resolve_transcribe_enabled(value: Optional[object]) -> bool:
    """Transcription default for POST /bots — same shared resolver, same 422 skin (CC3)."""
    try:
        return resolve_spawn_flag("TRANSCRIBE_ENABLED", value, default=True,
                                  field="transcribe_enabled")
    except InvalidFlagValue as e:
        raise HTTPException(status_code=422, detail=str(e))


def _resolve_automatic_leave(value: Optional[object]) -> dict:
    """Translate the public snake_case timeout names into invocation.v1's camelCase shape.

    Admission keeps its deployment default. The active-phase silence timeout is omitted when the
    caller does not set it, allowing the bot module's configurable ten-minute default to apply.
    """
    from .service import lobby_budget_ms

    if value is None:
        return {"waitingRoomTimeout": lobby_budget_ms()}
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

    waiting_room = timeout("max_wait_for_admission", "waiting_room_timeout") or lobby_budget_ms()
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


def _resolve_voice_agent_enabled(value: Optional[object]) -> bool:
    """Voice agent for POST /bots (api.v1 ``voice_agent_enabled``): off unless THIS request asks.
    There is no deployment default — a voice-agent bot can be made to talk and show content in
    someone's meeting, so it is an explicit per-spawn opt-in. Same parse and 422 as the other flags."""
    if value is None:
        return False
    try:
        return parse_flag(value, "voice_agent_enabled")
    except InvalidFlagValue as e:
        raise HTTPException(status_code=422, detail=str(e))



#: The external agent page the bot seats in the meeting. Long enough for a signed session URL.
AGENT_PAGE_URL_MAX_LEN = 2048


def _resolve_agent_page_url(body: dict) -> Optional[str]:
    """``voice_agent_settings.url`` for POST /bots — the external voice agent this ONE bot carries.

    Per request, never per deployment: the page speaks in the caller's meeting with the caller's
    agent, so it is named by the caller. ``voice_agent_url`` / ``voiceAgentUrl`` and the camelCase
    settings key are accepted as the same field, since integrators send all four shapes.

    The bot opens this page in its own browser inside the deployment network, so it passes the same
    guard as a webhook: http(s) only, no credentials, public addresses only.
    """
    settings = body.get("voice_agent_settings") or body.get("voiceAgentSettings")
    raw: Optional[object] = None
    if isinstance(settings, dict):
        for key in ("url", "voice_agent_url", "voiceAgentUrl"):
            if isinstance(settings.get(key), str) and settings[key].strip():
                raw = settings[key]
                break
    if raw is None:
        for key in ("voice_agent_url", "voiceAgentUrl"):
            if isinstance(body.get(key), str) and body[key].strip():
                raw = body[key]
                break
    if raw is None:
        if settings is not None and not isinstance(settings, dict):
            raise HTTPException(status_code=422, detail="voice_agent_settings must be an object with a url")
        return None

    url = str(raw).strip()
    if len(url) > AGENT_PAGE_URL_MAX_LEN:
        raise HTTPException(status_code=422,
                            detail=f"voice_agent_settings.url must be at most {AGENT_PAGE_URL_MAX_LEN} characters")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=422, detail="voice_agent_settings.url must be an http(s) URL")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise HTTPException(status_code=422,
                            detail="voice_agent_settings.url must not contain whitespace or control characters")
    from urllib.parse import urlsplit
    if urlsplit(url).username is not None or urlsplit(url).password is not None:
        raise HTTPException(status_code=422, detail="voice_agent_settings.url must not carry credentials")

    # A self-hosted deployment runs its agent on its own network, so private addresses are the
    # NORMAL case there and the guard is opt-in per deployment (VEXA_AGENT_ALLOW_PRIVATE_URLS).
    # A shared deployment keeps it on: the bot's browser would otherwise render an internal service
    # onto its camera in a stranger's meeting. Cloud metadata stays refused either way — no agent
    # lives there, and it is the one address whose contents are always credentials.
    from ..webhooks.ssrf import SSRFError, validate_webhook_url
    from .env_flags import InvalidFlagValue, parse_flag
    host = urlsplit(url).hostname or ""
    if host in ("169.254.169.254", "metadata.google.internal", "metadata"):
        raise HTTPException(status_code=422,
                            detail="voice_agent_settings.url is not an allowed agent URL: cloud metadata")
    # Narrow first: a deployment naming the hosts its agents live on keeps the guard for every
    # other address, which is what an operator actually wants — one agent host reachable, the rest
    # of their network still refused.
    allowlist = {h.strip().lower() for h in os.environ.get("VEXA_AGENT_URL_ALLOWLIST", "").split(",") if h.strip()}
    if host.lower() in allowlist:
        return url
    try:
        allow_private = parse_flag(os.environ.get("VEXA_AGENT_ALLOW_PRIVATE_URLS", ""),
                                   "VEXA_AGENT_ALLOW_PRIVATE_URLS")
    except InvalidFlagValue:
        allow_private = False          # a deployment that misconfigured the knob keeps the guard
    if allow_private:
        return url
    try:
        validate_webhook_url(url)
    except SSRFError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"voice_agent_settings.url is not an allowed agent URL: {str(e).removeprefix('Webhook URL ')}"
                " — set VEXA_AGENT_ALLOW_PRIVATE_URLS=true on a deployment whose agent is on its own network"
            ),
        ) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="voice_agent_settings.url is not a valid URL") from None
    return url


def _resolve_automatic_leave(value: Optional[object]) -> dict:
    """Translate the public snake_case timeout names into invocation.v1's camelCase shape.

    Admission keeps its deployment default. The active-phase silence timeout is omitted when the
    caller does not set it, allowing the bot module's configurable ten-minute default to apply.
    """
    from .service import lobby_budget_ms

    if value is None:
        return {"waitingRoomTimeout": lobby_budget_ms()}
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

    waiting_room = timeout("max_wait_for_admission", "waiting_room_timeout") or lobby_budget_ms()
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


def build_router(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    authority=None,
) -> APIRouter:
    """The bot-spawn routes over the injected ``MeetingRepo`` + ``RuntimeClient`` + authority ports.

    NO ``fetch_bot_context`` HERE, on purpose. The person's default bot name is still resolved by
    the domain that owns the bot, on every path a bot is spawned (founder ruling, 2026-09-02 — no
    fourth store) — but ``request_bot`` ALREADY fetches that person's whole spawn context, on this
    same request, to resolve the transcription backend and the capture-signal decision. A
    router-level fetcher made `POST /bots` ask identity for the same body TWICE, with two different
    timeouts (10s here, 5s there), for one string: up to +15s on a path a person is waiting on,
    while each fetcher's comment claimed to be the only one. The name now comes out of the context
    the service already has (``service._bot_name_from_context``)."""
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

        # A passcode sent under a name we do not read is a credential DROPPED — refuse before any
        # DB, runtime or network work, and name the field that works. Only a NON-EMPTY alias is
        # refused: a caller whose client emits `"password": null` for an unset field asked for
        # nothing, and 422-ing them would break working integrations over an absent value.
        supplied_aliases = [
            k for k in PASSCODE_ALIASES
            if isinstance(body.get(k), str) and body.get(k).strip()
        ]
        if supplied_aliases:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{', '.join(repr(a) for a in supplied_aliases)} is not a recognized field and "
                    "the meeting passcode it carries would be ignored — send the passcode as "
                    "'passcode', or supply the full 'meeting_url' with the passcode in its query"
                ),
            )

        platform = str(body.get("platform", "")).strip()
        native_meeting_id = str(body.get("native_meeting_id", "")).strip()
        meeting_url = body.get("meeting_url")
        # A caller-supplied meeting_url is an any-URL passthrough to the bot's browser
        # (zoom/jitsi) — validate at the point of entry (SSRF hygiene, 422 on violation).
        if meeting_url is not None:
            meeting_url = _validate_meeting_url(meeting_url)
        passcode = body.get("passcode")
        # api.v1's `teams_base_host` — WHICH Teams web client a constructed URL is built on. The
        # MCP link parser fills it from the link it parsed (gov./dod. clouds, teams.live.com for
        # personal meetings); without it every constructed URL lands on the world-wide host, so a
        # GCC-High caller's bot browses to a meeting that is not theirs. The bot navigates this
        # host, so an unrecognized one is a typed 422 here, never a passthrough (same SSRF rule
        # `_validate_meeting_url` applies to the URL path).
        teams_base_host = body.get("teams_base_host")
        if teams_base_host is not None:
            if not isinstance(teams_base_host, str):
                raise HTTPException(status_code=422, detail="teams_base_host must be a string")
            if resolve_teams_base_host(teams_base_host) is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"teams_base_host '{teams_base_host}' is not a Teams web client host — "
                        "use teams.microsoft.com, teams.live.com, gov.teams.microsoft.us or "
                        "dod.teams.microsoft.us"
                    ),
                )
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
        # Length and control bytes; plus the URL-structural chars below. The id's SEMANTIC shape is
        # STILL not validated: ids that look wrong do join (a bare-numeric Teams id transcribed a
        # real meeting in production), so a format rule would refuse working meetings. The one shape
        # rule is that the id must be a bare, URL-safe token — it is embedded into a URL path segment
        # and a lookup key, not carrying its own query string.
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
            # URL-structural chars (#892). A passcode accidentally left on the id
            # (`397421056486982?p=X8hc…`) is short and control-free, so it passed both guards above,
            # then built a broken join URL and stored an unfindable id. Refuse at the door and name
            # the fix. Whitespace beyond the control range (a literal space) is caught here too.
            if any(ch in NATIVE_MEETING_ID_URL_CHARS or ch.isspace() for ch in native_meeting_id):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "'native_meeting_id' must be the bare meeting id and cannot contain URL "
                        "characters ('?', '#', '&', '=', '/') or spaces — pass any passcode in "
                        "'passcode' or supply the full 'meeting_url' instead"
                    ),
                )
        # Reject a platform the meeting-bot flow cannot invoke, up front (→ 422) and BEFORE any DB
        # write. Without this, a platform outside the sealed invocation.v1 enum but WITH a
        # meeting_url (api.v1 seals more platforms than invocation.v1 — `browser_session`, #816)
        # sailed past the constructibility guard below, wrote its `requested` meeting row, and then
        # died inside build_invocation's schema validation: a 500, plus an ORPHANED active row that
        # 409s the user's retry on the dedup guard. The refusal names the real state of the world.
        if platform not in SPAWNABLE_PLATFORMS:
            supported = ", ".join(sorted(SPAWNABLE_PLATFORMS))
            raise HTTPException(
                status_code=422,
                detail=(
                    f"platform '{platform}' cannot be spawned as a meeting bot — supported: "
                    f"{supported}"
                    + (
                        ". browser_session is a provisioning workload, not a meeting bot; its "
                        "0.12 runtime path is not yet restored (tracked in "
                        "https://github.com/Vexa-ai/vexa/issues/816)"
                        if platform == "browser_session" else ""
                    )
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

        # THE NAME THIS PERSON'S BOT SHOWS UP AS — an explicit name on THIS request, and nothing
        # else here. `request_bot` fills in this person's default from identity out of the bot
        # context it already fetches (one hop, three readers: transcription backend, capture-signal
        # decision, bot name), and the deployment default underneath that. A FAILED LOOKUP NEVER
        # STOPS THE SPAWN: a name is a nicety, joining the call is the product.
        bot_name = body.get("bot_name")

        try:
            meeting = await request_bot(
                repo,
                runtime,
                authority=authority,
                user_id=user_id,
                platform=platform,
                native_meeting_id=native_meeting_id,
                bot_name=bot_name,
                passcode=passcode,
                meeting_url=meeting_url,
                teams_base_host=teams_base_host,
                language=body.get("language"),
                task=body.get("task"),
                transcription_tier=body.get("transcription_tier", "realtime"),
                recording_enabled=_resolve_recording_enabled(body.get("recording_enabled")),
                transcribe_enabled=transcribe_enabled,
                # An agent URL turns the voice agent on by itself: a caller who named the page the
                # bot should seat has asked for it, and a bot with the page but the flag off would
                # open that page and seat nobody.
                voice_agent_enabled=(
                    True if _resolve_agent_page_url(body)
                    else _resolve_voice_agent_enabled(body.get("voice_agent_enabled"))
                ),
                agent_page_url=_resolve_agent_page_url(body),
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
        except ServiceAuthorityDenied as e:
            # `code` and `reason` are for a program to branch on; `message` and `action_url` are
            # for whoever has to DO something about it. This service authors neither of the second
            # pair — it carries what the deciding service said, so a deployment can change what it
            # tells people without an OSS release, and a deployment that says nothing is unchanged
            # (both fields OMITTED, never null).
            #
            # `reason` is passed through WHATEVER it says. There is no allow-list here and there
            # must not be one: a reason this build has never heard of still reaches the caller
            # intact, because the alternative is a refusal that no surface can explain.
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "service_not_allowed",
                    "reason": e.reason,
                    "decision_id": e.decision_id,
                    **e.caller_fields(),
                },
            )
        except ServiceAuthorityUnavailable:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "service_authority_unavailable",
                    "reason": "service_authority_unavailable",
                },
            )
        except MeetingStopped as e:
            # The user's stop wins over this spawn — either it raced the workload creation, or the
            # request asked to CONTINUE a stopped meeting. 409 (conflicting state), not 5xx: nothing
            # is broken, and the detail names the request that works.
            raise HTTPException(status_code=409, detail=str(e))
        except DuplicateMeeting as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (MaxBotsExceeded, QuotaExceeded) as e:
            raise HTTPException(status_code=429, detail=str(e) or "Bot concurrency limit reached")
        except SpawnFailed as e:
            raise HTTPException(status_code=502, detail=str(e) or "Failed to start bot workload")

        return JSONResponse(status_code=201, content=meeting)

    return router
