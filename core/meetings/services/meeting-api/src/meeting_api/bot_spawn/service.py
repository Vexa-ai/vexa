"""The ``POST /bots`` flow — port of the parent ``meetings.request_bot`` (P2 core + P3 control-plane).

P3 added (all behind the same injected ports, so the flow still runs offline):
  * **continue_meeting** (P3c) — when the prior meeting for (platform, native_id) is TERMINAL,
    reuse the SAME meeting row + create a NEW ``MeetingSession`` instead of a fresh meeting (the
    409 only fires for a CONCURRENT, still-active prior meeting). Transcripts/recordings stay keyed
    by the meeting row, so a continued run preserves them.
  * **max-bots** (P3e) — a per-user concurrency pre-check: count the user's ACTIVE bots (excluding
    infra ``browser_session``) and reject the N+1th with 429 BEFORE spawning; the runtime kernel's
    own ``QuotaExceeded`` remains the defense-in-depth backstop.

The flow (parent ``meetings.py`` lines ~1010-1403, reduced to the standard-bot branch):
  1. construct the meeting URL (or use the supplied one),
  2. dedup — 409 if the user already has an active/requested meeting for (platform, native_id),
  2b. max-bots — 429 if the user is at their per-user concurrency cap (P3e),
  2c. continue_meeting — reuse a TERMINAL prior meeting row + add a session (P3c),
  3. insert the ``Meeting`` row (status ``requested``) → meeting_id (unless reusing one),
  4. mint the MeetingToken + build the ``invocation.v1`` invocation (BOT_CONFIG),
  5. spawn the platform's bot workload over ``runtime.v1`` (``RuntimeClient.create_workload`` —
     profile ``meeting-bot`` or ``discord-bot``, resolved from ``platform``),
  6. eager-create the ``MeetingSession`` keyed by the bot's ``connectionId`` (== session_uid),
  7. write the kernel workload id back as ``bot_container_id``,
  8. return the ``api.v1`` ``MeetingResponse`` (now listing its ``sessions``).
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config_preflight import CONFIG_FAULT_KINDS, cached_probe_verdict
from ..obs import log_event
from ..service_authority import (
    AllowAllServiceAuthority,
    ServiceAuthorityDenied,
    ServiceAuthorityRequest,
    ServiceAuthorityUnavailable,
)
from .env_flags import env_flag
from .invocation import (
    NO_MEETING_URL_PLATFORMS,
    build_invocation,
    build_workload_spec,
    mint_meeting_token,
)
from .ports import (
    AuthSessionBusy,
    AuthSessionNotConfigured,
    DuplicateMeeting,
    MaxBotsExceeded,
    MeetingRepo,
    MeetingStopped,
    QuotaExceeded,
    RuntimeClient,
    SpawnFailed,
    TranscriptionNotConfigured,
    _stopped_reopen_detail,
)

# Re-exported here (defined in ports.py to avoid an adapters→service circular import) so callers that
# already do ``from .service import DuplicateMeeting`` (the router) keep working.
__all__ = [
    "request_bot",
    "construct_meeting_url",
    "apply_join_passcode",
    "resolve_teams_base_host",
    "DuplicateMeeting",
    "MeetingStopped",
    "LOBBY_BUDGET_MS",
    "DEFAULT_LOBBY_BUDGET_S",
    "lobby_budget_ms",
    "NO_MEETING_URL_PLATFORMS",
]

# The waiting-room budget the control plane ISSUES to every bot it spawns (``automatic_leave
# .waitingRoomTimeout``): how long the bot may sit in a lobby, silently polling, before it gives up
# and reports its own ``awaiting_admission_timeout``. It is a DEADLINE WE WROTE, so every window the
# control plane measures a not-yet-admitted bot against must outlast it — the reconcile sweep derives
# its pre-active grace from this budget (``lifecycle.reconcile.default_preactive_grace``) rather
# than carrying a second, independently-drifting number (#862).
#
# 15 minutes by default (#1208): an auto-joined bot is dispatched BEFORE the scheduled start
# (``AUTO_JOIN_LEAD_S``), so it reaches the lobby before a human host is there to admit it — the
# budget has to cover the human's lateness, not just the click. The prior 10-minute budget is
# exactly the ~10.4-minute banding the admission-timeout failure class shows in prod (#267): bots
# were dying ON the deadline we handed them, which is a budget too small, not a join defect.
DEFAULT_LOBBY_BUDGET_S = 900
LOBBY_BUDGET_MS = DEFAULT_LOBBY_BUDGET_S * 1000


def lobby_budget_ms() -> int:
    """The lobby budget (ms) this deployment issues — ``VEXA_LOBBY_BUDGET_S``, default 900.

    Read at CALL time, never frozen at import, so every window derived from it (the reconcile
    sweep's pre-active grace) sees the same value the spawn path issues even when the env is set
    after import. Unparseable or non-positive values fall back to the default rather than issuing a
    deadline of zero — a bot given a zero budget gives up before it has knocked."""
    raw = os.getenv("VEXA_LOBBY_BUDGET_S")
    if raw is None or not raw.strip():
        return DEFAULT_LOBBY_BUDGET_S * 1000
    try:
        seconds = float(raw)
    except ValueError:
        return DEFAULT_LOBBY_BUDGET_S * 1000
    if seconds <= 0:
        return DEFAULT_LOBBY_BUDGET_S * 1000
    return int(seconds * 1000)

# Non-terminal statuses (parent's active set) — a prior meeting in one of these blocks a new spawn.
_ACTIVE_STATUSES = ("requested", "joining", "awaiting_admission", "active", "stopping")
_TERMINAL_STATUSES = ("completed", "failed")

# How stale an `stt` probe verdict may be and still refuse a spawn (#511 C3). Matches the probe's
# declared ttl_s: past it the cache holds no actionable opinion, so a spawn proceeds rather than
# blocking on a verdict that predates the operator's fix.
_STT_VERDICT_MAX_AGE_S = 60.0

# Construct-URL templates per platform (the parent's ``Platform.construct_meeting_url``, core set).
# NO jitsi template: a jitsi room name is scoped to a DEPLOYMENT (meet.jit.si is only the public
# one), so constructing a URL from the bare id would silently join the public room of that name —
# the wrong meeting, on someone else's deployment. jitsi callers pass an explicit ``meeting_url``
# (same passthrough zoom uses); the UI, MCP, and calendar paths all carry it.
#
# Teams is NOT one template: see ``_teams_url`` — its two id shapes join over two different paths.
_URL_TEMPLATES = {
    "google_meet": "https://meet.google.com/{native_meeting_id}",
}

# NO_MEETING_URL_PLATFORMS (discord — no join-by-URL concept at all, a different case from the
# templates above) now lives in invocation.py, imported above, so build_invocation's meetingUrl
# null-survival check reads the SAME set as the router's URL-required gate below.

# The two Teams meeting-id shapes, and why one template cannot serve both.
#
#   * THREAD id — ``19:meeting_…@thread.v2``, the id inside a classic ``/l/meetup-join/`` deep
#     link. It joins at ``/l/meetup-join/<thread>`` and carries no separate passcode: the deep
#     link's own query string is the credential, so a caller holding this id holds the whole URL.
#   * SHORT id — the 10–15 digit meeting id Teams prints next to "Meeting ID / Passcode" and
#     hands out as ``…/meet/<id>?p=<passcode>``. It joins at ``/meet/<id>``, and the passcode is
#     a SEPARATE value by construction — this is the ONLY shape for which a bare id plus its own
#     ``passcode`` field is a complete address.
#
# Only the SHORT shape is matched: it is the one that needs a different path from the one every
# Teams id used to get, and its 10–15 digit range mirrors the SSOT parsers that produce these ids
# (``collector.meeting_link``, the MCP ``link_parser``). Everything else — thread ids included —
# stays on ``/l/meetup-join/``. Interpolating a SHORT id into that path builds
# ``/l/meetup-join/397421056486982``: the wrong path for that id, and with nowhere for the
# passcode to go (#892).
_TEAMS_SHORT_ID = re.compile(r"^\d{10,15}$")

#: Teams web-client host SUFFIXES a constructed URL may be built on — the same set the join
#: layer recognizes as "this page IS Teams" (``join/src/msteams/auth-redirect.ts``
#: ``TEAMS_HOST_SUFFIXES``): world-wide, personal, the GCC-High/DoD clouds, and the
#: cloud.microsoft rename. A host is accepted when it equals a suffix or is a subdomain of one.
#:
#: The caller names the host through the api.v1 ``teams_base_host`` field (the MCP link parser
#: fills it from the link it parsed) and the bot's browser then navigates it — so this is an
#: ALLOWLIST, not a passthrough. An arbitrary host here is the same SSRF surface
#: ``_validate_meeting_url`` guards on the URL path, and it would also aim an anonymous join at a
#: look-alike page. ``teams.microsoft.com`` is the default when the caller names nothing.
_TEAMS_HOST_SUFFIXES = (
    "teams.microsoft.com",
    "teams.live.com",
    "teams.microsoft.us",
    "teams.cloud.microsoft",
)
_TEAMS_DEFAULT_HOST = "teams.microsoft.com"


def resolve_teams_base_host(teams_base_host: Optional[str]) -> Optional[str]:
    """The Teams host to build a constructed URL on, or ``None`` when the caller named a host
    that is not a Teams web client (the caller's error — the router turns it into a 422).

    Absent/blank resolves to the world-wide default, so every existing caller keeps today's host.
    """
    host = (teams_base_host or "").strip().lower().rstrip("/")
    if not host:
        return _TEAMS_DEFAULT_HOST
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in _TEAMS_HOST_SUFFIXES):
        return host
    return None


def _teams_url(native_meeting_id: str, teams_base_host: Optional[str]) -> str:
    """The join URL for a Teams id, chosen by the id's SHAPE (see ``_TEAMS_SHORT_ID`` above).

    An id matching neither shape keeps the classic ``/l/meetup-join/`` path: that is what every
    such id resolved to before this rule existed, and shape does not predict joinability here
    (a bare-numeric id outside the 10–15 range has transcribed real meetings), so an unrecognized
    id is left on its established path rather than rerouted on a guess.
    """
    host = resolve_teams_base_host(teams_base_host) or _TEAMS_DEFAULT_HOST
    if _TEAMS_SHORT_ID.match(native_meeting_id):
        return f"https://{host}/meet/{native_meeting_id}"
    return f"https://{host}/l/meetup-join/{native_meeting_id}"


async def _fetch_bot_context(user_id: int) -> dict:
    """The whole per-user spawn context from admin-api (``/internal/users/{id}/bot-context``).

    Best-effort BY CONTRACT — identity unreachable / non-200 / unset ADMIN_API_URL returns ``{}``
    and every caller below degrades to its own default. A lookup failure must NEVER block a spawn;
    that property is why this is one call whose result is read by two resolvers rather than two
    calls that can each fail differently.
    """
    admin_api_url = (os.getenv("ADMIN_API_URL") or "").rstrip("/")
    internal_secret = os.getenv("INTERNAL_API_SECRET") or ""
    if not (admin_api_url and internal_secret):
        return {}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{admin_api_url}/internal/users/{user_id}/bot-context",
                headers={"X-Internal-Secret": internal_secret},
            )
        if r.status_code != 200:
            return {}
        body = r.json()
        return body if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _transcription_from_context(ctx: dict) -> dict:
    """The Settings-configured transcription backend out of a bot-context body: admin-api resolves
    user pref > platform setting into ``{"transcription": {url, token, provider}}``. Absent →
    ``{}``, and the spawn keeps the process env (the pre-Settings behaviour). The token crosses ONLY
    that internal hop."""
    transcription = ctx.get("transcription")
    return transcription if isinstance(transcription, dict) else {}


def _capture_signal_from_context(ctx: dict) -> bool:
    """Whether this spawn tapes its raw captured-signal stream — DEFAULT ON.

    admin-api resolves user > platform_settings > default-on and ALWAYS states the key, so anything
    other than an explicit ``False`` here means we could not read a decision: unreachable identity,
    an older admin-api that predates the field, or an unset ADMIN_API_URL. All of those default ON,
    because prod meetings are the fixture source and a transient identity blip must not silently
    turn collection off fleet-wide. The kill switch is an explicit ``false``, nothing else.
    """
    return ctx.get("capture_signal") is not False


def construct_meeting_url(
    platform: str,
    native_meeting_id: str,
    *,
    teams_base_host: Optional[str] = None,
) -> Optional[str]:
    """Best-effort meeting URL for ``(platform, native_id)`` (zoom needs more than the id →
    None; the caller may pass an explicit ``meeting_url`` instead).

    PASSCODE-FREE by contract. This is the URL that gets PERSISTED as
    ``data.constructed_meeting_url`` and read back on every ``MeetingResponse``, so a credential
    must never reach it. The passcode is applied separately, to the invocation URL alone
    (``apply_join_passcode``) — the split is what lets the bot receive a joinable address while
    ordinary meeting readback stays clean (#892 A4).
    """
    if platform == "teams":
        return _teams_url(native_meeting_id, teams_base_host)
    tmpl = _URL_TEMPLATES.get(platform)
    return tmpl.format(native_meeting_id=native_meeting_id) if tmpl else None


def apply_join_passcode(platform: str, url: Optional[str], passcode: Optional[str]) -> Optional[str]:
    """The URL the BOT navigates: ``url`` with the caller's separate ``passcode`` carried on it.

    Only Teams short links (``…/meet/<id>``) take a passcode through the URL — that is where Teams
    itself puts it (``?p=``), and a bare short id plus a separate passcode is otherwise not a
    complete address. Every other platform reaches its passcode a different way and is returned
    unchanged: zoom and jitsi type theirs into a page (``join/src/zoom/join.ts``,
    ``join/src/jitsi/password.ts``) off the invocation's own ``passcode`` field, and a Teams thread
    id's ``/l/meetup-join/`` deep link carries its credential in the URL the caller already holds.

    A ``p`` the URL already carries WINS — a caller who pasted a full link is holding the
    authoritative one, and a stale separate ``passcode`` must not overwrite it.
    """
    if platform != "teams" or not url or not passcode:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not re.match(r"^/meet/[^/]+/?$", parsed.path or ""):
        return url
    query = parse_qsl(parsed.query or "", keep_blank_values=True)
    if any(k == "p" and v for k, v in query):
        return url
    query.append(("p", passcode))
    return urlunparse(parsed._replace(query=urlencode(query)))


def _meeting_response(row: dict, *, sessions: Optional[list] = None) -> dict:
    """Shape a meeting row into an ``api.v1`` MeetingResponse-conforming dict (required:
    id, user_id, status, bot_container_id, start_time, end_time, created_at, updated_at).

    P3c — when ``sessions`` is supplied, the response also lists the meeting's ``session_uid``s
    (the N bots that ran against this meeting row). This rides in ``data.sessions`` (the api.v1
    ``data`` field is an open object — see the contract note in the bot_spawn README) so the
    SEALED ``MeetingResponse`` schema is honoured without an edit; a public typed ``sessions``
    field would need a ``vN+1`` (flagged)."""
    data = dict(row.get("data")) if isinstance(row.get("data"), dict) else {}
    if sessions is not None:
        data["sessions"] = list(sessions)
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "platform": row.get("platform"),
        "native_meeting_id": row.get("native_meeting_id") or row.get("platform_specific_id"),
        "constructed_meeting_url": data.get("constructed_meeting_url"),
        "status": row.get("status", "requested"),
        "bot_container_id": row.get("bot_container_id"),
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
        "completion_reason": data.get("completion_reason"),
        "failure_stage": data.get("failure_stage"),
        "data": data,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _stopped_spawn_detail(meeting_id: Any) -> str:
    """The 409 a spawn fenced by a racing stop answers with. Outward-facing: it says the stop won
    and names the one action that works."""
    return (
        f"meeting {meeting_id} was stopped while the bot was being started, so no bot was "
        f"started — POST /bots again to start a new one"
    )


async def _stop_requested_on(repo: MeetingRepo, meeting_id: Any) -> bool:
    """This row's CURRENT user-stop flag, read fresh from the store.

    Deliberately not served from the row dict the spawn already holds: the whole point is to see a
    write that landed after it. Best-effort against a repo that predates ``get_meeting`` — an older
    ``MeetingRepo`` implementation simply keeps the pre-fence behaviour, backstopped by the
    post-spawn interlock, rather than raising AttributeError through a spawn."""
    getter = getattr(repo, "get_meeting", None)
    if getter is None:
        return False
    try:
        row = await getter(meeting_id)
    except Exception:  # noqa: BLE001 — a read failure must never fail a spawn; the interlock backstops
        return False
    return bool(((row or {}).get("data") or {}).get("stop_requested"))


async def _terminalize_as_stopped(
    repo: MeetingRepo, meeting_id: Any, status: Optional[str], *,
    fenced_before_spawn: bool = False,
) -> None:
    """Land a stop-fenced row on its truthful terminal: ``failed`` / ``stopped``.

    ``failed`` and not ``completed`` because the bot never reached the meeting, and ``completed`` is
    reachable only from ACTIVE — writing it here would launder a never-admitted bot into a served
    visit, which is precisely #807. The reason is the user's (``stopped``), so
    ``lifecycle.occurrence`` reads the row as USER_STOPPED and the calendar never re-dispatches the
    occurrence. Best-effort: the reconcile sweep is the backstop, and a failure here must not mask
    the 409 the caller is about to receive."""
    try:
        await repo.fail_meeting(
            meeting_id=meeting_id,
            reason=(
                "stopped by the user before the bot workload was created"
                if fenced_before_spawn
                else "stopped by the user while the bot workload was being created"
            ),
            failure_stage=status if status in ("requested", "joining", "awaiting_admission") else "requested",
            completion_reason="stopped",
            data={"stop_requested": True},
        )
    except Exception as e:  # noqa: BLE001 — reconcile backstops; never mask the stop verdict
        log_event("bot_spawn_stop_terminalize_failed", audience="system", level="error",
                  span="bots.create", meeting_id=str(meeting_id), fields={"error": str(e)})


async def request_bot(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    authority=None,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    bot_name: Optional[str] = None,
    passcode: Optional[str] = None,
    meeting_url: Optional[str] = None,
    teams_base_host: Optional[str] = None,
    language: Optional[str] = None,
    task: Optional[str] = None,
    transcription_tier: str = "realtime",
    recording_enabled: bool = False,
    transcribe_enabled: bool = True,
    automatic_leave: Optional[dict] = None,
    continue_meeting: bool = False,
    max_concurrent: Optional[int] = None,
    redis_url: Optional[str] = None,
    meeting_api_url: Optional[str] = None,
    internal_secret: Optional[str] = None,
    token_secret: Optional[str] = None,
    # Per-user webhook config (the gateway forwards it from identity's /internal/validate). Persisted
    # into meeting.data so the lifecycle callback delivers status_change events with no users-table read.
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    webhook_events: Optional[dict] = None,
) -> dict:
    """Run the spawn flow and return a MeetingResponse-shaped dict.

    Raises ``DuplicateMeeting`` (409), ``MaxBotsExceeded`` / ``QuotaExceeded`` (429), or
    ``SpawnFailed`` (502/failed).

    ``continue_meeting`` (P3c): if the prior meeting for (platform, native_id) is TERMINAL, reuse
    that row + add a new session instead of creating a fresh meeting. ``max_concurrent`` (P3e): the
    per-user cap — the spawn is rejected if the user already has that many ACTIVE bots. A cap
    ``<= 0`` means the quota is DEPLETED (every spawn rejected) — 0 is never "unlimited"; ``None``
    means no cap was provided, so no pre-check.
    """
    authority = authority or AllowAllServiceAuthority()
    # 1. URL.
    constructed_url = meeting_url or construct_meeting_url(
        platform, native_meeting_id, teams_base_host=teams_base_host
    )
    # The address the BOT gets, which is not the address we STORE: the passcode rides on the join
    # URL (Teams' own `?p=`) and stays off `data.constructed_meeting_url`, which every
    # MeetingResponse reads back. Both derive from the same constructed URL, so they address the
    # same meeting — they differ only in carrying the credential (#892 A1/A4).
    join_url = apply_join_passcode(platform, constructed_url, passcode)

    # 1b. Resolve the transcription backend and gate BEFORE any DB write (C1, reorder not
    #     duplicate): the old router gate refused pre-insert; resolving here keeps that property —
    #     a refused spawn must not leave an orphaned `requested` meeting row (whose retry after
    #     fixing config would then 409 on the dedup guard). STT creds the bot transcribes with —
    #     the process env is the bottom fallback; a configured backend from Settings (user pref >
    #     platform setting, resolved by admin-api's bot-context) overrides it per spawn. The
    #     resolved values flow down unchanged to the invocation build. Note: config.v1's `stt`
    #     capability tri-state still drives boot preflight + /health; the spawn path trusts THIS
    #     resolver instead (issue #502 C1) because Settings-configured STT is invisible to the
    #     env-only capability check.
    transcription_service_url = os.getenv("TRANSCRIPTION_SERVICE_URL") or None
    transcription_service_token = os.getenv("TRANSCRIPTION_SERVICE_TOKEN") or None
    transcription_model = os.getenv("TRANSCRIPTION_MODEL") or None
    bot_context = await _fetch_bot_context(user_id)
    configured = _transcription_from_context(bot_context)
    # O-TEL-1 fixture collection, resolved from the SAME best-effort lookup (one hop, two readers).
    # Default ON: only an explicit false from identity stops the tape.
    capture_signal_enabled = _capture_signal_from_context(bot_context)
    transcription_provider: Optional[str] = "none" if not transcribe_enabled else None
    if configured.get("url"):
        transcription_service_url = configured["url"]
        # A configured backend's token replaces the env token even when empty — the env token
        # belongs to the ENV backend, never to a user-supplied endpoint. Same rule for the
        # model id: the env model names the ENV backend's served model, so a configured
        # backend carries its own (unset → the client's whisper-1 default).
        transcription_service_token = configured.get("token") or None
        transcription_model = configured.get("model") or None
        configured_provider = configured.get("provider")
        if transcribe_enabled and configured_provider in ("vexa", "customer"):
            transcription_provider = configured_provider
    elif transcribe_enabled and transcription_service_url:
        # The process-level backend is operated by this Vexa deployment. A Settings response,
        # however, is not inferred from its URL: mixed-version identity may return a customer URL
        # without provenance, and guessing there could turn an unknown service into a Vexa charge.
        transcription_provider = "vexa"
    if transcribe_enabled and not transcription_service_url:
        raise TranscriptionNotConfigured(
            "no transcription backend configured — set it in Settings or environment variables "
            "TRANSCRIPTION_SERVICE_URL + TRANSCRIPTION_SERVICE_TOKEN"
        )
    # 1b-ii. SET-but-MISCONFIGURED is the other half of the same gate (#511 C3): a URL/token that is
    #     present but rejected (or points at a 404) used to spawn a bot that joined, captured, and
    #     transcribed NOTHING. Refuse it here, with the probe's own named reason, before the DB
    #     write. Three bounds keep the refusal honest:
    #       * CACHED verdicts only (boot preflight seeds them, /health refreshes them per ttl) — no
    #         probe I/O rides the spawn path;
    #       * CONFIG faults only — a `unreachable` verdict is the endpoint being down, and refusing
    #         on it would make every spawn fail for a minute whenever STT restarts. The bot's own
    #         client retries; a wrong URL never heals by itself. Only the latter is ours to refuse;
    #       * the ENV backend only — the verdict describes that endpoint, so a Settings-configured
    #         backend (a different endpoint) must never be blocked by the env one's health.
    if transcribe_enabled and not configured.get("url"):
        verdict = cached_probe_verdict("stt", max_age_s=_STT_VERDICT_MAX_AGE_S)
        if verdict is not None and verdict.get("kind") in CONFIG_FAULT_KINDS:
            log_event(
                "bot_spawn_stt_backend_unhealthy", audience="user", level="warning",
                span="bots.create", user_id=user_id,
                fields={"reason": verdict.get("reason"), "status": verdict.get("status")},
            )
            raise TranscriptionNotConfigured(
                f"the configured transcription backend is not working: {verdict.get('reason')} — "
                f"fix TRANSCRIPTION_SERVICE_URL / TRANSCRIPTION_SERVICE_TOKEN; this re-tests within "
                f"{int(_STT_VERDICT_MAX_AGE_S)}s, or call /health?force=1 to re-probe now"
            )

    # 1c. Authenticated-bot mode (#724, deployment-scoped knob — Q1-A): when BOT_AUTHENTICATED is
    #     set, EVERY spawn carries the sealed invocation.v1 auth block, so the bot restores the
    #     deployment's provisioned browser session (`make login`) and joins signed-in. Config is
    #     gated loud BEFORE any DB write (the TranscriptionNotConfigured precedent) — a half-
    #     configured knob must never spawn a bot that silently joins anonymous. Env vocabulary
    #     matches the provisioning CLI: BOT_USERDATA_S3_PATH + BOT_S3_{ENDPOINT,BUCKET,ACCESS_KEY,
    #     SECRET_KEY} (scoped userdata credentials — never the deployment's admin S3 creds; they
    #     ride the invocation env into the bot container, so their blast radius must stay the
    #     userdata prefix).
    authenticated = env_flag("BOT_AUTHENTICATED", False)
    auth_userdata_path: Optional[str] = None
    auth_s3: dict[str, Optional[str]] = {}
    if authenticated:
        auth_userdata_path = os.getenv("BOT_USERDATA_S3_PATH") or None
        auth_s3 = {
            "s3_endpoint": os.getenv("BOT_S3_ENDPOINT") or None,
            "s3_bucket": os.getenv("BOT_S3_BUCKET") or None,
            "s3_access_key": os.getenv("BOT_S3_ACCESS_KEY") or None,
            "s3_secret_key": os.getenv("BOT_S3_SECRET_KEY") or None,
        }
        if not (auth_userdata_path and auth_s3["s3_endpoint"] and auth_s3["s3_bucket"]):
            raise AuthSessionNotConfigured(
                "BOT_AUTHENTICATED is set but the userdata store is incomplete — set "
                "BOT_USERDATA_S3_PATH + BOT_S3_ENDPOINT + BOT_S3_BUCKET (and scoped "
                "BOT_S3_ACCESS_KEY/BOT_S3_SECRET_KEY); provision the session with `make login`"
            )

    # 2c. continue_meeting (P3c): reuse a TERMINAL prior meeting row if asked. The reused row keeps
    #     its id (so its transcripts/recordings survive); a fresh session is appended below. This read
    #     stays a plain query — the reused-row path reopens an existing terminal row (no NEW active row
    #     is inserted), so it is not part of the dedup/cap TOCTOU window.
    reused_row: Optional[dict] = None
    if continue_meeting:
        latest = await repo.find_latest(user_id, platform, native_meeting_id)
        latest_data = (latest or {}).get("data") or {}
        deletion = latest_data.get("artifact_deletion") or {}
        recording_delete = any(
            r.get("deletion_pending") for r in (latest_data.get("recordings") or [])
            if isinstance(r, dict)
        )
        # A row whose artifacts are deleted, or being deleted, is not reusable: reopening it in place
        # would attach a new run to a record the owner has erased. Such a request falls through to the
        # fresh-insert path and gets a NEW row, which is what "deleted" has to mean.
        if (
            latest and latest.get("status") in _TERMINAL_STATUSES
            and not deletion and not recording_delete
        ):
            # F4 — a row the USER STOPPED is never reopened. `continue_meeting` reopens a terminal
            # row IN PLACE (clearing its terminal attribution) with none of the guarded-create path's
            # protections, so it was the one way back into a run the user had ended: on stage rev
            # 193 it resurrected 26313 to `requested` with `stop_requested` still true, and the row
            # then read as a live meeting nobody had asked for. The 409 names the path that works —
            # a stopped meeting is finished, and a new run is a new POST /bots.
            if (latest.get("data") or {}).get("stop_requested"):
                log_event(
                    "bot_spawn_continue_refused_stopped", audience="user", level="warning",
                    span="bots.create", user_id=user_id, meeting_id=str(latest["id"]),
                )
                raise MeetingStopped(_stopped_reopen_detail(latest["id"]))
            reused_row = latest

    # 2+2b+3. Dedup + max-bots cap + INSERT, made ATOMIC (ROB1/ROB2). Replaces the old read-check-
    #     then-act sequence (find_active → count_active_bots → create_meeting), whose three separate
    #     transactions opened a TOCTOU window: under concurrent POST /bots, every coroutine passed the
    #     pre-checks before any inserted its `requested` row → over-provision past the cap / double-
    #     spawn one meeting. create_meeting_guarded does dedup + cap + insert in ONE transaction (the
    #     real adapter serializes per-user with a pg advisory lock + a unique partial index backstop;
    #     the fake has no await between the check and the insert). The continue_meeting (reused-
    #     terminal-row) path reopens an existing row and is unchanged.
    # 2d. Per-identity serialization (#725 C2): one stored session = one live bot. Refuse a
    #     second concurrent authenticated spawn against the same userdata path with a typed 409
    #     naming the conflicting meeting. Control-plane pre-check against the tracked active set
    #     (the issue's default); it runs just before the row insert, so the remaining window is a
    #     single request interleaving — the storage-side lock fork stays available if a multi-
    #     replica deployment ever witnesses it.
    if authenticated and auth_userdata_path:
        conflict = await repo.find_active_by_userdata(auth_userdata_path)
        if conflict is not None and (reused_row is None or conflict["id"] != reused_row["id"]):
            log_event(
                "bot_spawn_auth_session_busy", audience="user", level="warning",
                span="bots.create", user_id=user_id,
                fields={"conflicting_meeting_id": conflict["id"]},
            )
            raise AuthSessionBusy(conflict["id"], auth_userdata_path)

    # The service identity exists before any meeting row or workload. It is a
    # per-run identity (not the database row id): continued runs reuse a
    # meeting row but remain distinct delivered services and distinct billing
    # settlements. The admitted decision is frozen into meeting.data and
    # therefore travels with the terminal meeting projection.
    connection_id = str(uuid.uuid4())
    service_identity = f"meeting-session:{connection_id}"
    active_concurrency = await repo.count_active_bots(
        user_id=user_id,
        exclude_meeting_id=(
            reused_row["id"] if reused_row is not None else None
        ),
    )
    if transcription_provider is None and authority.configured:
        raise ServiceAuthorityUnavailable(
            "configured service authority requires resolved service provenance"
        )
    authority_request = ServiceAuthorityRequest.admit(
        user_id=user_id,
        request_id=f"{service_identity}:admit",
        service_identity=service_identity,
        transcription_provider=transcription_provider or "none",
        active_concurrency=active_concurrency,
    )
    authority_decision = await authority.decide(authority_request)
    if (
        authority_decision.enforced
        and not authority_decision.allow
    ):
        raise ServiceAuthorityDenied(
            authority_decision.reason,
            authority_decision.decision_id,
        )
    authority_record = {
        **authority_decision.to_record(),
        "mode": authority.mode,
        "service_mode": "bot",
        "transcription_provider": authority_request.transcription_provider,
        "lifecycle_contract_version":
            authority_request.lifecycle_contract_version,
        "last_boundary_at": None,
        "last_decision_id": authority_decision.decision_id,
        "teardown_confirmed": False,
    }

    if reused_row is not None:
        # continue_meeting reopens an EXISTING terminal row (no new active row inserted), so it is not
        # part of the fresh-insert TOCTOU window — but the per-user cap still applies (a continued run
        # is an active bot). Keep the original pre-check here, excluding the row being reopened from the
        # count, to preserve the P3e semantics (test_max_bots.test_continue_meeting_session_counts_against_cap).
        if max_concurrent is not None:
            # cap <= 0 = depleted: reject without counting (0 >= cap holds for any cap <= 0).
            active = 0
            if max_concurrent > 0:
                active = await repo.count_active_bots(
                    user_id=user_id, exclude_meeting_id=reused_row["id"],
                )
            if active >= max_concurrent:
                log_event(
                    "bot_spawn_max_bots_exceeded", audience="user", level="warning",
                    span="bots.create", user_id=user_id,
                    fields={"active": active, "cap": max_concurrent},
                )
                raise MaxBotsExceeded(user_id, max_concurrent)
        row = await repo.reopen_meeting(
            meeting_id=reused_row["id"],
            data_patch={
                "transcribe_enabled": transcribe_enabled,
                "recording_enabled": recording_enabled,
                "transcription_provider": transcription_provider,
                "service_authority": authority_record,
            },
        )
    else:
        meeting_data: dict[str, Any] = {}
        if constructed_url:
            meeting_data["constructed_meeting_url"] = constructed_url
        meeting_data["transcribe_enabled"] = transcribe_enabled
        meeting_data["recording_enabled"] = recording_enabled
        if transcription_provider is not None:
            meeting_data["transcription_provider"] = transcription_provider
        meeting_data["service_authority"] = authority_record
        # The serialization key for authenticated spawns — find_active_by_userdata matches on it.
        if authenticated and auth_userdata_path:
            meeting_data["auth_userdata_path"] = auth_userdata_path
        # Per-user webhook config carried on the meeting (delivered by the lifecycle callback).
        # Stripped from any outbound meeting projection (webhooks.delivery._INTERNAL_DATA_KEYS). At
        # the API response edge the treatment splits (collector.projection): `webhook_secret` is
        # never served to anyone (SENSITIVE_OMIT_KEYS); `webhook_url`/`webhook_events` are served to
        # the OWNER — the v0.10 REST contract reads them back off the meeting row — and stripped for
        # a share/workspace reader (OWNER_ONLY_KEYS).
        if webhook_url:
            meeting_data["webhook_url"] = webhook_url
            if webhook_secret:
                meeting_data["webhook_secret"] = webhook_secret
            if webhook_events:
                meeting_data["webhook_events"] = webhook_events
        try:
            row = await repo.create_meeting_guarded(
                user_id=user_id,
                platform=platform,
                native_meeting_id=native_meeting_id,
                data=meeting_data,
                max_concurrent=max_concurrent,
            )
        except MaxBotsExceeded:
            log_event(
                "bot_spawn_max_bots_exceeded", audience="user", level="warning",
                span="bots.create", user_id=user_id,
                fields={"cap": max_concurrent},
            )
            raise
    meeting_id = row["id"]

    # 4. MeetingToken + invocation. connection_id IS the session_uid (parent's connectionId).
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    meeting_api_url = meeting_api_url or os.getenv("MEETING_API_URL", "http://meeting-api:8080")
    internal_secret = internal_secret if internal_secret is not None else os.getenv(
        "INTERNAL_API_SECRET"
    )
    # STT creds were resolved and gated at step 1b (before the meeting-row write); the resolved
    # transcription_service_url/token/model flow into the invocation below. Without either the bot
    # joins + captures but cannot transcribe — None-safe: omitted from the invocation when unset
    # (transcribe still gated by transcribe_enabled, which step 1b refuses when unresolvable).
    # Token must outlive the bot's max active time (default 4h, see bot deriveMaxActiveMs) or
    # transcription dies mid-meeting when the JWT expires. Default 5h; override per deployment.
    token_ttl_seconds = int(os.getenv("MEETING_TOKEN_TTL_SECONDS") or 18000)
    token = mint_meeting_token(
        meeting_id, user_id, platform, native_meeting_id, secret=token_secret, ttl_seconds=token_ttl_seconds
    )
    invocation = build_invocation(
        meeting_id=meeting_id,
        platform=platform,
        meeting_url=join_url,
        bot_name=bot_name or (os.getenv("DEFAULT_BOT_NAME") or f"VexaBot-{uuid.uuid4().hex[:6]}"),
        passcode=passcode,
        token=token,
        native_meeting_id=native_meeting_id,
        connection_id=connection_id,
        language=language,
        task=task,
        transcription_tier=transcription_tier,
        redis_url=redis_url,
        meeting_api_callback_url=f"{meeting_api_url}/bots/internal/callback/lifecycle",
        internal_secret=internal_secret,
        transcribe_enabled=transcribe_enabled,
        transcription_service_url=transcription_service_url,
        transcription_service_token=transcription_service_token,
        transcription_model=transcription_model,
        recording_enabled=recording_enabled,
        capture_modes=(["audio", "video"] if recording_enabled else None),
        # O-TEL-1: the tape is INDEPENDENT of recording_enabled — a meeting the user never asked to
        # record still yields a fixture. Both ride the same upload endpoint below.
        capture_signal_enabled=capture_signal_enabled,
        recording_upload_url=f"{meeting_api_url}/internal/recordings/upload",
        authenticated=True if authenticated else None,
        userdata_s3_path=auth_userdata_path,
        s3_endpoint=auth_s3.get("s3_endpoint"),
        s3_bucket=auth_s3.get("s3_bucket"),
        s3_access_key=auth_s3.get("s3_access_key"),
        s3_secret_key=auth_s3.get("s3_secret_key"),
        # Explicit caller windows win; otherwise omit everyoneLeftTimeout so the bot's
        # silence-window module default applies (the lobby window stays forgiving for
        # human-in-the-loop dashboard joins).
        automatic_leave=automatic_leave or {"waitingRoomTimeout": lobby_budget_ms()},
    )

    # 4b. THE SPAWN FENCE (F2, stage rev 193 row 26313). Re-read this row's user-stop flag from the
    #     store — NOT from the snapshot above — immediately before the workload is created. A DELETE
    #     that landed while the token was minted and the invocation built has already committed
    #     `stop_requested`; creating the pod now would put a bot in a meeting the user has already
    #     said no to, and the stop's own direct teardown cannot reach a workload that does not exist
    #     yet. Refusing HERE means the common case creates no pod at all.
    if await _stop_requested_on(repo, meeting_id):
        await _terminalize_as_stopped(repo, meeting_id, row.get("status"), fenced_before_spawn=True)
        log_event("bot_spawn_fenced_by_stop", audience="user", level="warning",
                  span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                  fields={"phase": "before_workload_create"})
        raise MeetingStopped(_stopped_spawn_detail(meeting_id))

    # 5. Spawn over runtime.v1.
    spec = build_workload_spec(
        workload_id=f"mtg-{meeting_id}-{connection_id[:8]}",
        invocation=invocation,
        callback_url=f"{meeting_api_url}/runtime/callback",
    )
    try:
        result = await runtime.create_workload(spec)
        # Defense in depth at the service/port seam (#718 C2): the adapter already refuses a dead
        # spawn (non-201, or a 201 whose body is state=stopped/destroyed), but the service must not
        # trust ANY port's optimism either — a returned non-live state is a spawn failure here too, so
        # no code path proceeds to a 201 over a workload that never came up.
        spawned_state = result.get("state")
        if spawned_state in ("stopped", "destroyed"):
            raise SpawnFailed(f"workload dead on spawn: {result.get('stopReason') or spawned_state}")
    except QuotaExceeded:
        log_event(
            "bot_spawn_quota_exceeded", audience="user", level="warning",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise
    except SpawnFailed as e:
        # No workload came up. Mark the just-inserted meeting row `failed` with the reason so no
        # `requested` row lingers for the 5-minute reaper to flip reason-less (#718): the failure and
        # its cause are on the row NOW, and POST /bots answers 502 with the same reason. The row is
        # failed BY ID — the MeetingSession is not created until after a successful spawn, so the
        # session-keyed update_meeting_status cannot reach it yet.
        reason = str(e) or "bot workload failed to start"
        try:
            await repo.fail_meeting(meeting_id=meeting_id, reason=reason, failure_stage="requested")
        except Exception as fail_err:  # noqa: BLE001 — failing the row is best-effort; never mask the spawn error
            log_event(
                "bot_spawn_fail_row_error", audience="system", level="error",
                span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                fields={"error": str(fail_err)},
            )
        log_event(
            "bot_spawn_failed", audience="system", level="error",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
            fields={"reason": reason},
        )
        raise

    workload_id = result.get("workloadId") or result.get("name") or spec["workloadId"]

    # 6+7. Eager-create the MeetingSession (connectionId == session_uid) + write the kernel workload id
    #      back as bot_container_id. The workload is ALREADY running, so a failure here would orphan it
    #      (a live bot with no session row to resolve its uploads, the meeting stuck `requested`) —
    #      ROB3. Wrap both DB writes: on failure, tear the just-created workload DOWN (best-effort) and
    #      re-raise as SpawnFailed so the route maps it to 502 and no half-spawned state is left behind.
    try:
        # For a continued meeting this APPENDS a session to the reused row — N sessions per meeting (P3c).
        await repo.create_session(meeting_id=meeting_id, session_uid=connection_id)
        row = await repo.set_bot_container(meeting_id=meeting_id, bot_container_id=workload_id)
    except Exception as e:  # noqa: BLE001 — any post-spawn DB failure must trigger compensation
        try:
            await runtime.delete_workload(workload_id)
        except Exception as teardown_err:  # noqa: BLE001 — teardown is best-effort, never masks the cause
            log_event(
                "bot_spawn_orphan_teardown_failed", audience="system", level="error",
                span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                fields={"workload_id": workload_id, "error": str(teardown_err)},
            )
        log_event(
            "bot_spawn_post_spawn_db_failed", audience="system", level="error",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
            fields={"workload_id": workload_id, "error": str(e)},
        )
        raise SpawnFailed(
            f"post-spawn DB write failed; workload {workload_id} torn down"
        ) from e

    # THE INTERLOCK — the half of the fence that has no TOCTOU hole (F2).
    #
    # The pre-spawn fence above narrows the window; it cannot close it, because a DELETE can always
    # land between that read and `create_workload`. What closes it is the ORDER the two paths write
    # and read in, which is a plain flag-then-check interlock:
    #
    #     spawn:  write bot_container_id (committed, above)  →  read stop_requested (here)
    #     stop:   write stop_requested   (committed)         →  read bot_container_id (stop_router)
    #
    # Each side PUBLISHES its own fact before READING the other's, so at least one of the two reads
    # must observe the other's write, whatever the interleaving — there is no schedule in which the
    # stop misses the container AND the spawn misses the flag. Either the stop tears the workload
    # down directly, or this does. (The hole on rev 193 was that neither half held: the stop read a
    # row snapshot taken BEFORE its own write, so it saw `bot_container_id=None`; and this check read
    # only STATUS, which a stop against a PRE-ACTIVE row deliberately does not change (#807) and
    # which a stop against a session-less row cannot change at all. Both sides looked, both missed.)
    raced = await repo.get_lifecycle_state_by_session(session_uid=connection_id)
    raced_status = (raced or {}).get("status")
    raced_stop = bool(((raced or {}).get("data") or {}).get("stop_requested"))
    if raced_stop or raced_status in ("stopping", "completed", "failed"):
        try:
            await runtime.delete_workload(workload_id)
            log_event("bot_spawn_raced_stop_torn_down", audience="system", level="warning",
                      span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                      fields={"workload_id": workload_id, "raced_status": raced_status,
                              "stop_requested": raced_stop})
        except Exception as teardown_err:  # noqa: BLE001 — teardown is best-effort, never masks the spawn
            log_event("bot_spawn_raced_stop_teardown_failed", audience="system", level="error",
                      span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
                      fields={"workload_id": workload_id, "error": str(teardown_err)})
        if raced_stop:
            # The run is over before it began, and the row must SAY SO now rather than sit
            # non-terminal until a reaper guesses. Truthfully: `failed` (the FSM's only legal
            # pre-active terminal) with the user's own reason.
            await _terminalize_as_stopped(repo, meeting_id, raced_status)
            raise MeetingStopped(_stopped_spawn_detail(meeting_id))

    # The response lists the meeting's sessions (P3c) — all session_uids that ran against this row.
    sessions = await repo.list_sessions(meeting_id=meeting_id)

    # USER-facing: a bot was requested for this user.
    log_event(
        "bot_join_requested", audience="user", span="bots.create",
        user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
        fields={
            "platform": platform, "status": row.get("status", "requested"),
            "continued": reused_row is not None, "session_count": len(sessions),
        },
    )
    return _meeting_response(row, sessions=sessions)
