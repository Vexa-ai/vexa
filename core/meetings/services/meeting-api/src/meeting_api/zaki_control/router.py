"""Sealed Hub-BFF control routes for ZAKI Minutes.

This router intentionally has no browser-facing identity path.  It accepts only a short-lived,
per-request HMAC token minted by the Hub server and repeats its tenant/user binding in headers,
route and body before an engine side effect can occur.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import urlparse
from typing import Callable

import jsonschema
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ..bot_spawn.ports import DuplicateMeeting, SpawnFailed, TranscriptionNotConfigured
from ..bot_spawn.url_validation import UnsafeMeetingUrl, canonical_meeting_identity
from ..capture import (
    ZAKI_NOTETAKER_NAME,
    CaptureAuthority,
    CaptureDenied,
    CaptureDenial,
    request_capture,
    withdraw_capture,
)
from ..retention import ErasureFailed, ScopeExpiries, erase_meeting
from .callbacks import ControlCallbackDispatcher
from .ports import Capture, ControlStore, ErasureTarget, Policy, Subject
from .schema import conforms


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_USER_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_CONTROL_AUDIENCE = "zaki-control.v1"
_MIN_CAPTURE_SECONDS = 60
_MAX_CAPTURE_SECONDS = 4 * 60 * 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ControlConfig:
    """Configuration held only by meeting-api, never returned by a control response."""

    enabled: bool
    operator_enabled: bool
    signing_secret: str
    max_capture_seconds: int = 60 * 60
    # Operator-declared Jitsi hosts arrive here as validated configuration and are passed
    # explicitly into the sealed URL predicate.  The contract forbids ambient process environment
    # from changing conformance, so the predicate itself never reads os.environ.
    jitsi_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ControlConfig | None":
        import os

        values = os.environ if env is None else env
        enabled = values.get("ZAKI_MINUTES_CONTROL_ENABLED", "false").lower() == "true"
        if not enabled:
            return None
        secret = values.get("MINUTES_ENGINE_CONTROL_TOKEN", "")
        if len(secret) < 32:
            raise RuntimeError("ZAKI Minutes control is enabled without its signing secret")
        try:
            max_capture_seconds = int(values.get("MINUTES_CONTROL_MAX_CAPTURE_SECONDS", "3600"))
        except (TypeError, ValueError):
            raise RuntimeError("ZAKI Minutes control has an invalid maximum capture duration") from None
        if not _MIN_CAPTURE_SECONDS <= max_capture_seconds <= _MAX_CAPTURE_SECONDS:
            raise RuntimeError("ZAKI Minutes control maximum capture duration is outside its safe range")
        hosts = tuple(
            host.strip().lower().rstrip(".")
            for host in values.get("ZAKI_MINUTES_JITSI_HOSTS", "").split(",")
            if host.strip()
        )
        if any(len(host) > 253 or not _HOSTNAME.fullmatch(host) for host in hosts):
            raise RuntimeError("ZAKI Minutes control has an invalid operator Jitsi host list")
        return cls(
            enabled=True,
            operator_enabled=values.get("ZAKI_MINUTES_OPERATOR_ENABLED", "false").lower() == "true",
            signing_secret=secret,
            max_capture_seconds=max_capture_seconds,
            jitsi_hosts=hosts,
        )


def _control_error(status: int, code: str, *, request_id: str | None = None, operation_id: str | None = None) -> JSONResponse:
    content = {
        "api_version": "zaki-control.v1",
        "request_id": request_id if _valid_identifier(request_id) else "unknown-request",
        "code": code,
        "retryable": code in {"upstream_unavailable", "internal_error"},
    }
    if _valid_identifier(operation_id):
        content["operation_id"] = operation_id
    return JSONResponse(status_code=status, content=content, headers={"Cache-Control": "no-store"})


def _response(content: dict, *, request_id: str) -> JSONResponse:
    payload = dict(content)
    payload["request_id"] = request_id
    return JSONResponse(status_code=200, content=payload, headers={"Cache-Control": "no-store"})


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


_GMEET_CODE = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
_GMEET_ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{3,38}[a-z0-9]$")
_ZOOM_PATH = re.compile(r"^/(?:j|w)/\d{9,11}/?$")
_ZOOM_WC_PATH = re.compile(r"^/wc/join/\d{9,11}/?$")
_TEAMS_MEET_PATH = re.compile(r"^/meet/\d{10,15}/?$")


def meeting_url_matches_platform(
    platform: object, raw_url: object, configured_jitsi_hosts: tuple[str, ...] = ()
) -> bool:
    """Port of the sealed `meetingUrlMatchesPlatform` predicate (zaki-control.v1 validate.mjs).

    The shared `bot_spawn` validator only approves a host; the sealed contract also pins the
    provider path shape, so a control-plane capture is checked against THIS predicate. Operator
    Jitsi hosts arrive as a validated argument — never from ambient process environment — so the
    conformance vectors stay reproducible.
    """
    if not isinstance(raw_url, str):
        return False
    try:
        url = urlparse(raw_url)
    except ValueError:
        return False
    if url.scheme != "https" or url.username or url.password:
        return False
    try:
        host = (url.hostname or "").lower()
    except ValueError:
        return False
    path = url.path

    if platform == "google_meet":
        if host != "meet.google.com" or path.startswith("/lookup/"):
            return False
        segments = [segment for segment in path.split("/") if segment]
        code = segments[0] if segments else ""
        return bool(_GMEET_CODE.match(code) or _GMEET_ALIAS.match(code))
    if platform == "zoom":
        if not (host == "zoom.us" or host.endswith(".zoom.us")
                or host == "zoomgov.com" or host.endswith(".zoomgov.com")):
            return False
        return bool(_ZOOM_PATH.match(path) or _ZOOM_WC_PATH.match(path))
    if platform == "teams":
        if not (host == "teams.live.com" or host.endswith(".teams.live.com")
                or host == "teams.microsoft.com" or host.endswith(".teams.microsoft.com")
                or host == "gov.teams.microsoft.us" or host == "dod.teams.microsoft.us"
                or host.endswith(".teams.microsoft.us")):
            return False
        fragment_path = urlparse(f"https://x{url.fragment}").path if url.fragment else ""
        return bool(
            _TEAMS_MEET_PATH.match(path)
            or "/l/meetup-join/" in path
            or (path.rstrip("/") == "/v2" and _TEAMS_MEET_PATH.match(fragment_path))
        )
    if platform == "jitsi":
        known = host == "meet.jit.si" or host in {
            value.lower() for value in configured_jitsi_hosts
        }
        room = path.strip("/")
        return known and bool(room) and not re.search(r"[/?#\s]", room)
    return False


def _decode_b64url(value: str) -> bytes | None:
    if not _B64URL.fullmatch(value) or "=" in value:
        return None
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        return None


def _token_subject(token: str | None, secret: str, *, now: datetime) -> Subject | None:
    """Verify the frozen Hub token: ``b64url(JSON claims).b64url(HMAC(secret, p))``."""
    if not isinstance(token, str) or token.count(".") != 1:
        return None
    payload_b64, signature_b64 = token.split(".", 1)
    payload_raw = _decode_b64url(payload_b64)
    signature = _decode_b64url(signature_b64)
    if payload_raw is None or signature is None or len(signature) != 32:
        return None
    expected = hmac.new(secret.encode(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        claims = json.loads(payload_raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict) or set(claims) != {"aud", "exp", "iat", "tenant_id", "user_id", "v"}:
        return None
    tenant_id, user_id = claims.get("tenant_id"), claims.get("user_id")
    iat, exp = claims.get("iat"), claims.get("exp")
    if (
        claims.get("v") != 1
        or claims.get("aud") != _CONTROL_AUDIENCE
        or not _valid_identifier(tenant_id)
        or not isinstance(user_id, str)
        or not _USER_ID.fullmatch(user_id)
        or isinstance(iat, bool)
        or not isinstance(iat, int)
        or isinstance(exp, bool)
        or not isinstance(exp, int)
        or exp <= iat
        or not 30 <= exp - iat <= 300
    ):
        return None
    current = int(now.timestamp())
    if iat > current or current > exp:
        return None
    return Subject(tenant_id, user_id)


def _canonical_sha256(body: dict) -> str:
    semantic = {key: value for key, value in body.items() if key not in {"request_id", "idempotency_key"}}
    raw = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _operation_id() -> str:
    return f"op-{uuid.uuid4().hex}"


def _capture_id() -> str:
    return f"capture-{uuid.uuid4().hex}"


def _receipt_id() -> str:
    return f"erase-{uuid.uuid4().hex}"


def _capture_seconds_at(capture: Capture, current: datetime) -> int:
    """Bound an erasure-side terminal settlement by the engine's enforced capture cap."""
    seconds = max(0, capture.captured_seconds_total)
    if capture.started_at is not None:
        seconds = max(seconds, int(max(timedelta(0), current - capture.started_at).total_seconds()))
    if capture.max_capture_seconds:
        seconds = min(seconds, capture.max_capture_seconds)
    return seconds


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _failure_from_capture(error: CaptureDenied) -> tuple[int, str]:
    if error.code == CaptureDenial.QUOTA_EXHAUSTED:
        return 429, "quota_exhausted"
    if error.code in {CaptureDenial.OPERATOR_DISABLED, CaptureDenial.TENANT_DISABLED}:
        return 409, "minutes_disabled"
    if error.code in {CaptureDenial.AUTHORITY_EXPIRED, CaptureDenial.AUTHORITY_REPLAYED}:
        return 409, "invalid_state"
    return 422, "invalid_request"


def _policy(body: dict) -> Policy:
    raw = body["policy"]
    retention = raw["retention"]
    return Policy(
        capture_enabled=raw["capture_enabled"],
        agent_read_enabled=raw["agent_read_enabled"],
        policy_version=raw["capture_notice_policy_version"],
        audio_days=retention["audio_days"],
        transcript_days=retention["transcript_days"],
        summary_days=retention["summary_days"],
    )


def _status_payload(capture: Capture, *, request_id: str) -> dict:
    terminal = capture.state in {"completed", "failed"}
    payload = {
        "api_version": "zaki-control.v1",
        "request_id": request_id,
        "subject": {"tenant_id": capture.subject.tenant_id, "user_id": capture.subject.user_id},
        "capture_id": capture.capture_id,
        "state": capture.state,
        "metering": {
            "reservation_id": capture.reservation_id,
            "captured_seconds_total": max(0, capture.captured_seconds_total),
            "terminal": terminal,
        },
    }
    if capture.meeting_id is not None:
        payload["meeting_id"] = capture.meeting_id
    if capture.state == "failed":
        payload["failure_code"] = capture.failure_code or "internal_failure"
    return payload


def _binding(
    *,
    token: str | None,
    config: ControlConfig,
    path_user_id: str,
    tenant_header: str | None,
    user_header: str | None,
    body: dict | None,
    now: datetime,
) -> tuple[Subject | None, str | None]:
    subject = _token_subject(token, config.signing_secret, now=now)
    if subject is None:
        return None, "auth_failed"
    body_subject = body.get("subject") if isinstance(body, dict) else None
    if (
        not isinstance(tenant_header, str)
        or not isinstance(user_header, str)
        or subject.user_id != path_user_id
        or subject.tenant_id != tenant_header
        or subject.user_id != user_header
        or (body is not None and (
            not isinstance(body_subject, dict)
            or body_subject.get("tenant_id") != subject.tenant_id
            or body_subject.get("user_id") != subject.user_id
        ))
    ):
        return None, "subject_mismatch"
    return subject, None


async def _body(request: Request, shape: str, *, request_id: str | None) -> tuple[dict | None, JSONResponse | None]:
    try:
        body = await request.json()
    except Exception:
        return None, _control_error(422, "invalid_request", request_id=request_id)
    if not isinstance(body, dict):
        return None, _control_error(422, "invalid_request", request_id=request_id)
    try:
        conforms(body, shape)
    except jsonschema.ValidationError:
        return None, _control_error(422, "invalid_request", request_id=request_id)
    return body, None


def _mutation_headers_match(body: dict, request_id: str | None, idempotency_key: str | None) -> bool:
    return (
        _valid_identifier(request_id)
        and _valid_identifier(idempotency_key)
        and body.get("request_id") == request_id
        and body.get("idempotency_key") == idempotency_key
    )


def build_router(
    *,
    store: ControlStore,
    config: ControlConfig,
    meeting_repo,
    runtime,
    command_publisher,
    retention_repo,
    retention_storage,
    callback_dispatcher: ControlCallbackDispatcher | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> APIRouter:
    """Build the private authenticated control surface; composition decides whether to mount it."""
    router = APIRouter()

    async def _claim(subject: Subject, operation: str, body: dict):
        return await store.claim_operation(
            subject=subject,
            operation=operation,
            idempotency_key=body["idempotency_key"],
            request_sha256=_canonical_sha256(body),
            operation_id=_operation_id(),
        )

    async def _existing(subject: Subject, operation: str, body: dict, request_id: str):
        """Replay a completed operation before mutable admission can invalidate its receipt.

        An idempotent response is a durable fact.  In particular, a completed capture/erasure
        must still replay after policy changes or raw data removal rather than be turned into a
        new policy error by a retry from the Hub.
        """
        claim = await store.lookup_operation(
            subject=subject,
            operation=operation,
            idempotency_key=body["idempotency_key"],
            request_sha256=_canonical_sha256(body),
        )
        if claim is None or claim.state == "pending":
            # Claiming below can either observe a live owner or reclaim an expired lease.  Treating
            # every pending lookup as a permanent 503 would make crash recovery impossible.
            return None
        return _replay_or_error(claim, request_id)

    def _replay_or_error(claim, request_id: str):
        if claim.state == "replay":
            return _response(claim.response or {}, request_id=request_id)
        if claim.state == "conflict":
            return _control_error(409, "idempotency_conflict", request_id=request_id)
        if claim.state == "pending":
            return _control_error(503, "upstream_unavailable", request_id=request_id)
        # ``retry`` owns an expired execution lease.  Its capture mapping is consulted below
        # before any spawn, so it can safely complete an interrupted operation.
        return None

    def _capture_result(capture: Capture, *, meeting_id: str, operation_id: str) -> dict:
        """Return the sealed initial-capture receipt after a fresh or recovered spawn."""
        return {
            "api_version": "zaki-control.v1",
            "operation_id": operation_id,
            "subject": {
                "tenant_id": capture.subject.tenant_id,
                "user_id": capture.subject.user_id,
            },
            "capture_id": capture.capture_id,
            # The CaptureResponse contract intentionally represents acceptance, not the current
            # bot state.  Hub follows this with the status surface for lifecycle state.
            "state": "requested",
            "meeting_id": meeting_id,
            "metering": {"reservation_id": capture.reservation_id},
        }

    async def _recover_spawned_meeting(capture: Capture) -> dict | None:
        """Find the one meeting created before a crash could bind its control mapping.

        The capture ID is the one-time grant and is stored only as its SHA-256 in meeting data.
        Matching it before another spawn turns a lease retry into a completion, never a duplicate
        visible bot.
        """
        expected_grant = hashlib.sha256(capture.capture_id.encode()).hexdigest()

        def matches(candidate: object) -> dict | None:
            if not isinstance(candidate, dict) or candidate.get("id") is None:
                return None
            data = candidate.get("data")
            zaki_capture = data.get("zaki_capture") if isinstance(data, dict) else None
            grant = zaki_capture.get("grant_id_sha256") if isinstance(zaki_capture, dict) else None
            if (
                isinstance(grant, str)
                and hmac.compare_digest(grant, expected_grant)
                and zaki_capture.get("tenant_id") == capture.subject.tenant_id
            ):
                return candidate
            return None

        active = await meeting_repo.find_active(
            int(capture.subject.user_id), capture.platform, capture.native_meeting_id
        )
        recovered = matches(active)
        if recovered is not None:
            return recovered
        latest = await meeting_repo.find_latest(
            int(capture.subject.user_id), capture.platform, capture.native_meeting_id
        )
        return matches(latest)

    async def _complete_capture(
        *,
        subject: Subject,
        capture: Capture,
        meeting_id: str,
        body: dict,
        operation_id: str,
        fence: int,
        meeting_row: dict | None = None,
    ) -> dict:
        """Bind a capture exactly once, then seal its initial receipt.

        Hub persists the receipt as ``requested``.  The first engine callback therefore starts at
        the next lifecycle transition, avoiding a delayed/replayed ``requested`` event that could
        regress an already-active capture after crash recovery.
        """
        # A reclaimed operation may no longer make an irreversible bind or publish a receipt.
        # Fence immediately before the first durable effect, not only at completion time.
        await store.assert_operation_fence(
            subject=subject, operation="capture", idempotency_key=body["idempotency_key"], fence=fence
        )
        bound_capture = capture
        if capture.meeting_id != meeting_id:
            await store.bind_capture_meeting(capture_id=capture.capture_id, meeting_id=meeting_id)
            bound_capture = replace(capture, meeting_id=meeting_id, state="requested")
        # A lifecycle callback can race between bot creation and this mapping.  Reconciliation is
        # therefore mandatory for both the normal spawn path and crash recovery, not just an
        # optional recovery branch.  Event IDs are deterministic, so repeating it is harmless.
        if callback_dispatcher is not None:
            candidate = meeting_row or await _recover_spawned_meeting(bound_capture)
            if candidate is not None:
                await callback_dispatcher.reconcile_capture_lifecycle(candidate)
        result = _capture_result(bound_capture, meeting_id=meeting_id, operation_id=operation_id)
        await store.complete_operation(
            subject=subject,
            operation="capture",
            idempotency_key=body["idempotency_key"],
            response=result,
            fence=fence,
        )
        return result

    @router.post("/api/zaki/control/v1/{user_id}/ensure")
    async def ensure(
        user_id: str,
        request: Request,
        x_zaki_control_token: str | None = Header(default=None),
        x_zaki_tenant_id: str | None = Header(default=None),
        x_zaki_user_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        body, error = await _body(request, "EnsureRequest", request_id=x_request_id)
        if error:
            return error
        if not _mutation_headers_match(body, x_request_id, idempotency_key):
            return _control_error(422, "invalid_request", request_id=x_request_id)
        subject, code = _binding(
            token=x_zaki_control_token, config=config, path_user_id=user_id,
            tenant_header=x_zaki_tenant_id, user_header=x_zaki_user_id, body=body, now=now(),
        )
        if code:
            return _control_error(401 if code == "auth_failed" else 403, code, request_id=x_request_id)
        existing = await _existing(subject, "ensure", body, x_request_id)
        if existing:
            return existing
        claim = await _claim(subject, "ensure", body)
        existing = _replay_or_error(claim, x_request_id)
        if existing:
            return existing
        policy = _policy(body)
        try:
            if not await store.put_policy(subject, policy):
                return _control_error(409, "minutes_disabled", request_id=x_request_id)
            result = {
                "api_version": "zaki-control.v1",
                "operation_id": claim.operation_id,
                "subject": {"tenant_id": subject.tenant_id, "user_id": subject.user_id},
                "state": "ready" if config.enabled else "disabled",
                "policy_version": policy.policy_version,
            }
            await store.complete_operation(
                subject=subject,
                operation="ensure",
                idempotency_key=body["idempotency_key"],
                response=result,
                fence=claim.fence,
            )
        except Exception:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        return _response(result, request_id=x_request_id)

    @router.post("/api/zaki/control/v1/{user_id}/captures")
    async def create_capture(
        user_id: str,
        request: Request,
        x_zaki_control_token: str | None = Header(default=None),
        x_zaki_tenant_id: str | None = Header(default=None),
        x_zaki_user_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        body, error = await _body(request, "CaptureRequest", request_id=x_request_id)
        if error:
            return error
        # ZAKI's staging network contract currently permits only Google Meet
        # browser dependencies. Keep the server-side admission aligned with
        # that egress boundary even if a compromised/older Hub bypasses its
        # Google-only browser parser.
        # `invalid_request` is the only ErrorResponse code the sealed vocabulary offers for a
        # well-formed request this deployment refuses to serve; an out-of-vocabulary code would
        # itself fail the contract.
        if body.get("platform") != "google_meet":
            return _control_error(422, "invalid_request", request_id=x_request_id)
        if not _mutation_headers_match(body, x_request_id, idempotency_key):
            return _control_error(422, "invalid_request", request_id=x_request_id)
        current = now()
        subject, code = _binding(
            token=x_zaki_control_token, config=config, path_user_id=user_id,
            tenant_header=x_zaki_tenant_id, user_header=x_zaki_user_id, body=body, now=current,
        )
        if code:
            return _control_error(401 if code == "auth_failed" else 403, code, request_id=x_request_id)
        existing = await _existing(subject, "capture", body, x_request_id)
        if existing:
            return existing
        if await store.subject_is_erasing(subject):
            return _control_error(409, "minutes_disabled", request_id=x_request_id)
        policy = await store.get_policy(subject)
        if not config.operator_enabled or policy is None or not policy.capture_enabled:
            return _control_error(409, "minutes_disabled", request_id=x_request_id)
        attestation = body["capture_attestation"]
        attested_at = _parse_datetime(attestation.get("attested_at"))
        reserved_units = body["metering"]["reserved_units"]
        max_reserved_units = (config.max_capture_seconds + 59) // 60
        if (
            attestation.get("attested_by_user_id") != subject.user_id
            or attestation.get("policy_version") != policy.policy_version
            or attestation.get("bot_visible") is not True
            or attestation.get("bot_display_name") != ZAKI_NOTETAKER_NAME
            or attested_at is None
            or attested_at > current
            or policy.summary_days > policy.transcript_days
            or policy.audio_days < 0
            or isinstance(reserved_units, bool)
            or not isinstance(reserved_units, int)
            or not 1 <= reserved_units <= max_reserved_units
        ):
            return _control_error(422, "invalid_request", request_id=x_request_id)
        # Two independent gates, both required: the sealed contract predicate pins the provider
        # path shape, and the shared bot_spawn validator additionally refuses IP literals, localhost
        # and non-default ports before any URL reaches a browser.
        if not meeting_url_matches_platform(
            body["platform"], body["meeting_url"], config.jitsi_hosts
        ):
            return _control_error(422, "invalid_request", request_id=x_request_id)
        try:
            meeting_url, meeting_identity = canonical_meeting_identity(
                body["meeting_url"], platform=body["platform"]
            )
        except UnsafeMeetingUrl:
            return _control_error(422, "invalid_request", request_id=x_request_id)
        claim = await _claim(subject, "capture", body)
        existing = _replay_or_error(claim, x_request_id)
        if existing:
            return existing
        # The existing capture service binds a grant to a native identity.  The sealed control API
        # deliberately accepts only a full URL, so derive a canonical non-reversible identity and
        # scope it to the tenant.  Legacy meetings are keyed only by numeric user ID, and omitting
        # the tenant here could make equal URLs in two tenants collide or reveal each other.
        native_meeting_id = "zaki-" + hashlib.sha256(
            f"{subject.tenant_id}\0{meeting_identity}".encode()
        ).hexdigest()
        meeting_url_sha256 = hashlib.sha256(meeting_identity.encode()).hexdigest()
        try:
            # A lease retry first recovers the durable mapping.  In particular, an earlier worker
            # may have spawned the bot but died before persisting ``meeting_id``; do not mint a
            # second grant or invoke the runtime until that one has been reconciled.
            capture = await store.get_capture_by_operation(
                subject=subject, operation_id=claim.operation_id
            )
            if capture is None:
                capture = Capture(
                    capture_id=_capture_id(), subject=subject, operation_id=claim.operation_id,
                    reservation_id=body["metering"]["reservation_id"], platform=body["platform"],
                    native_meeting_id=native_meeting_id, meeting_id=None, state="requested",
                    max_capture_seconds=min(config.max_capture_seconds, reserved_units * 60),
                )
                await store.create_capture(capture)
            if capture.meeting_id is not None:
                recovered_meeting = await _recover_spawned_meeting(capture)
                result = await _complete_capture(
                    subject=subject, capture=capture, meeting_id=capture.meeting_id, body=body,
                    operation_id=claim.operation_id, fence=claim.fence, meeting_row=recovered_meeting,
                )
                return _response(result, request_id=x_request_id)

            recovered_meeting = await _recover_spawned_meeting(capture)
            if recovered_meeting is not None:
                result = await _complete_capture(
                    subject=subject, capture=capture, meeting_id=str(recovered_meeting["id"]), body=body,
                    operation_id=claim.operation_id, fence=claim.fence, meeting_row=recovered_meeting,
                )
                return _response(result, request_id=x_request_id)

            expiries = ScopeExpiries(
                audio=current + timedelta(days=policy.audio_days),
                transcript=current + timedelta(days=policy.transcript_days),
                summary=current + timedelta(days=policy.summary_days),
            )
            authority = CaptureAuthority(
                operator_enabled=config.operator_enabled,
                tenant_enabled=policy.capture_enabled,
                tenant_attested=True,
                tenant_policy_version=policy.policy_version,
                tenant_attested_at=attested_at,
                user_requested=True,
                quota_permitted=True,
                subject_user_id=int(subject.user_id),
                tenant_id=subject.tenant_id,
                meeting_platform=body["platform"],
                native_meeting_id=native_meeting_id,
                authorized_at=current,
                valid_until=current + timedelta(minutes=5),
                scope_expiries=expiries,
                grant_id=capture.capture_id,
                meeting_url_sha256=meeting_url_sha256,
            )
            await store.assert_operation_fence(
                subject=subject, operation="capture", idempotency_key=body["idempotency_key"], fence=claim.fence
            )
            meeting = await request_capture(
                meeting_repo,
                runtime,
                authority=authority,
                tenant_id=subject.tenant_id,
                user_id=int(subject.user_id),
                platform=body["platform"],
                native_meeting_id=native_meeting_id,
                meeting_url=meeting_url,
                recording_enabled=policy.audio_days > 0,
                agent_read_enabled=policy.agent_read_enabled,
                max_lifetime_sec=min(config.max_capture_seconds, reserved_units * 60),
                evaluated_at=current,
            )
            meeting_id = str(meeting["id"])
            result = await _complete_capture(
                subject=subject, capture=capture, meeting_id=meeting_id, body=body,
                operation_id=claim.operation_id, fence=claim.fence, meeting_row=meeting,
            )
        except CaptureDenied as exc:
            status, code = _failure_from_capture(exc)
            return _control_error(status, code, request_id=x_request_id)
        except (TranscriptionNotConfigured, SpawnFailed):
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        except DuplicateMeeting:
            return _control_error(409, "invalid_state", request_id=x_request_id)
        except Exception:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        return _response(result, request_id=x_request_id)

    @router.get("/api/zaki/control/v1/{user_id}/captures/{capture_id}")
    async def capture_status(
        user_id: str,
        capture_id: str,
        x_zaki_control_token: str | None = Header(default=None),
        x_zaki_tenant_id: str | None = Header(default=None),
        x_zaki_user_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
    ):
        request_id = x_request_id if _valid_identifier(x_request_id) else "status-request"
        subject, code = _binding(
            token=x_zaki_control_token, config=config, path_user_id=user_id,
            tenant_header=x_zaki_tenant_id, user_header=x_zaki_user_id, body=None, now=now(),
        )
        if code:
            return _control_error(401 if code == "auth_failed" else 403, code, request_id=request_id)
        if not _valid_identifier(capture_id):
            return _control_error(404, "invalid_state", request_id=request_id)
        capture = await store.get_capture(subject=subject, capture_id=capture_id)
        if capture is None:
            return _control_error(404, "invalid_state", request_id=request_id)
        return JSONResponse(
            status_code=200, content=_status_payload(capture, request_id=request_id),
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/zaki/control/v1/{user_id}/captures/{capture_id}/stop")
    async def stop_capture(
        user_id: str,
        capture_id: str,
        request: Request,
        x_zaki_control_token: str | None = Header(default=None),
        x_zaki_tenant_id: str | None = Header(default=None),
        x_zaki_user_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        body, error = await _body(request, "StopCaptureRequest", request_id=x_request_id)
        if error:
            return error
        if not _mutation_headers_match(body, x_request_id, idempotency_key) or body.get("capture_id") != capture_id:
            return _control_error(422, "invalid_request", request_id=x_request_id)
        subject, code = _binding(
            token=x_zaki_control_token, config=config, path_user_id=user_id,
            tenant_header=x_zaki_tenant_id, user_header=x_zaki_user_id, body=body, now=now(),
        )
        if code:
            return _control_error(401 if code == "auth_failed" else 403, code, request_id=x_request_id)
        existing = await _existing(subject, "stop_capture", body, x_request_id)
        if existing:
            return existing
        # Validate ownership before writing a new idempotency receipt. A completed receipt above
        # deliberately wins over this lookup after a later account erasure has removed the map.
        capture = await store.get_capture(subject=subject, capture_id=capture_id)
        if capture is None or capture.meeting_id is None:
            return _control_error(404, "invalid_state", request_id=x_request_id)
        claim = await _claim(subject, "stop_capture", body)
        existing = _replay_or_error(claim, x_request_id)
        if existing:
            return existing
        try:
            # Re-read after ownership of the operation lease is known; a prior callback can have
            # completed it while this request was waiting on the idempotency row.
            capture = await store.get_capture(subject=subject, capture_id=capture_id)
            if capture is None or capture.meeting_id is None:
                return _control_error(404, "invalid_state", request_id=x_request_id)
            if capture.state in {"completed", "failed"}:
                result = _status_payload(capture, request_id="placeholder")
                result.pop("request_id")
                await store.complete_operation(
                    subject=subject,
                    operation="stop_capture",
                    idempotency_key=body["idempotency_key"],
                    response=result,
                    fence=claim.fence,
                )
                return _response(result, request_id=x_request_id)
            await store.assert_operation_fence(
                subject=subject, operation="stop_capture", idempotency_key=body["idempotency_key"], fence=claim.fence
            )
            await withdraw_capture(
                meeting_repo, command_publisher, tenant_id=subject.tenant_id, user_id=int(subject.user_id),
                platform=capture.platform, native_meeting_id=capture.native_meeting_id, runtime=runtime,
            )
            # Hand over the capture as it actually is: the adjacency guard compares against
            # `capture.state`, so a pre-advanced copy would disable it. The status is then read back
            # from the store rather than assumed, so a transition the sealed graph refuses is never
            # reported to the Hub as though it had been recorded.
            if callback_dispatcher is not None:
                await callback_dispatcher.record_capture_status(capture, state="stopping")
                stopped = await store.get_capture(subject=subject, capture_id=capture_id) or capture
            else:
                await store.mark_capture_state(capture_id=capture.capture_id, state="stopping")
                stopped = replace(capture, state="stopping")
            result = _status_payload(stopped, request_id="placeholder")
            result.pop("request_id")
            await store.complete_operation(
                subject=subject,
                operation="stop_capture",
                idempotency_key=body["idempotency_key"],
                response=result,
                fence=claim.fence,
            )
        except CaptureDenied:
            return _control_error(409, "invalid_state", request_id=x_request_id)
        except Exception:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        return _response(result, request_id=x_request_id)

    def _empty_counts() -> dict[str, int]:
        return {"meeting_rows": 0, "transcript_rows": 0, "summary_rows": 0, "recording_objects": 0}

    def _planned_counts(plan) -> dict[str, int]:
        return {
            "meeting_rows": 1,
            "transcript_rows": max(0, int(plan.transcript_rows)),
            "summary_rows": max(0, int(plan.summary_documents)),
            "recording_objects": max(0, int(plan.recording_objects or 0)),
        }

    async def _withdraw_for_erasure(
        target: ErasureTarget,
        *,
        operation: str,
        body: dict,
        fence: int,
    ) -> None:
        """Close the writer barrier and settle terminal usage before raw erasure.

        The capture mapping and its terminal callback outbox remain durable until Hub has
        acknowledged the terminal status/usage pair.  That ordering is what prevents erasure from
        deleting the only evidence needed to release a hold or settle actual usage.
        """
        if target.state not in {"completed", "failed"}:
            await store.assert_operation_fence(
                subject=target.subject,
                operation=operation,
                idempotency_key=body["idempotency_key"],
                fence=fence,
            )
            await withdraw_capture(
                meeting_repo,
                command_publisher,
                tenant_id=target.subject.tenant_id,
                user_id=int(target.subject.user_id),
                platform=target.platform,
                native_meeting_id=target.native_meeting_id,
                runtime=runtime,
            )
        if target.capture_id is None:
            return
        capture = await store.get_capture(subject=target.subject, capture_id=target.capture_id)
        if capture is None:
            return
        if callback_dispatcher is None:
            # Production composition forbids this, but keep the router safe if a test or a future
            # integration tries to mount the destructive control surface without settlement.
            raise ErasureFailed("terminal settlement dispatcher is unavailable")
        # The dispatcher walks from the capture's CURRENT state, so it must be handed the capture as
        # it actually is. Handing it a copy that is already advanced to the target makes every walk
        # a silent no-op: no settlement is queued, `drain_capture_terminal` never sees a terminal
        # event, and erasure then fails closed on every retry instead of converging.
        if capture.state in {"completed", "failed"}:
            # Already terminal — re-assert settlement idempotently (event IDs are deterministic) so
            # a crash between the transition and its outbox row still converges on retry.
            settled = replace(capture, captured_seconds_total=_capture_seconds_at(capture, now()))
            await callback_dispatcher.record_capture_status(
                settled, state=capture.state, failure_code=capture.failure_code
            )
        else:
            await callback_dispatcher.record_capture_timeline(capture, state="stopping")
            stopping = replace(
                capture,
                state="stopping",
                captured_seconds_total=_capture_seconds_at(capture, now()),
            )
            await callback_dispatcher.record_capture_timeline(stopping, state="completed")
            capture = replace(stopping, state="completed")
        if not await callback_dispatcher.drain_capture_terminal(capture.capture_id):
            raise ErasureFailed("terminal settlement requires retry")

    async def _erase_one(
        subject: Subject,
        meeting_id: str,
        policy_version: str,
        erased_at: datetime,
        *,
        before_delete: Callable[[object], object] | None = None,
    ):
        receipt = await erase_meeting(
            retention_repo, retention_storage, user_id=subject.user_id, meeting_id=meeting_id,
            erased_at=erased_at, policy_version=policy_version, before_delete=before_delete,
        )
        if receipt is None:
            return "already_absent", _empty_counts()
        return "completed", {
            "meeting_rows": receipt.meeting_rows,
            "transcript_rows": receipt.transcript_rows,
            "summary_rows": receipt.summary_documents,
            "recording_objects": receipt.recording_objects,
        }

    def _operation_progress(claim, erased_at: datetime) -> dict:
        """Return the immutable erasure receipt journal, retaining only non-content metadata."""
        existing = claim.progress if isinstance(claim.progress, dict) else {}
        progress = json.loads(json.dumps(existing)) if existing else {}
        progress.setdefault("receipt_id", _receipt_id())
        progress.setdefault("erased_at", erased_at.isoformat())
        progress.setdefault("targets", {})
        if not isinstance(progress["targets"], dict):
            raise RuntimeError("invalid durable erasure progress")
        return progress

    def _target_progress(progress: dict, meeting_id: str) -> dict:
        value = progress["targets"].get(str(meeting_id))
        return dict(value) if isinstance(value, dict) else {}

    async def _save_progress(
        subject: Subject, operation: str, body: dict, claim, progress: dict
    ) -> None:
        await store.save_operation_progress(
            subject=subject,
            operation=operation,
            idempotency_key=body["idempotency_key"],
            fence=claim.fence,
            progress=progress,
        )

    async def _erase_target(
        *,
        subject: Subject,
        target: ErasureTarget | None,
        meeting_id: str,
        policy_version: str,
        erased_at: datetime,
        operation: str,
        body: dict,
        claim,
        progress: dict,
    ) -> tuple[str, dict[str, int]]:
        """Run one resumable erase target, retaining only its stable receipt counts.

        ``prepared`` is durably written after retention pins its plan/census and before object
        deletion.  A retry can therefore distinguish a genuinely absent meeting from a meeting
        deleted just before a crash, while the capture mapping stays available for terminal
        settlement cleanup.
        """
        prior = _target_progress(progress, meeting_id)
        prior_state = prior.get("state")
        prior_counts = prior.get("counts") if isinstance(prior.get("counts"), dict) else None
        capture_id = target.capture_id if target is not None else prior.get("capture_id")

        # All irreversible work and control finalization were checkpointed. This fast path is
        # essential for account-erasure recovery after subject-level cleanup has already removed
        # the now-delivered outbox rows but before the operation receipt itself was completed.
        if prior_state == "finalized":
            counts = dict(prior_counts or _empty_counts())
            return ("already_absent" if counts == _empty_counts() else "completed"), counts

        if target is None and prior_state is None:
            # A numeric legacy meeting ID is not proof that it belongs to this tenant's Minutes
            # control domain. Never pass an unowned/foreign row to raw retention just because its
            # user ID happens to match; preserve the intentional non-enumerating absent response.
            details = {"state": "finalized", "capture_id": None, "counts": _empty_counts()}
            progress["targets"][str(meeting_id)] = details
            await _save_progress(subject, operation, body, claim, progress)
            return "already_absent", details["counts"]

        if prior_state not in {"raw_erased", "finalized"}:
            if target is not None:
                await _withdraw_for_erasure(
                    target, operation=operation, body=body, fence=claim.fence
                )

            async def remember_plan(plan) -> None:
                details = _target_progress(progress, meeting_id)
                details.update(
                    {
                        "state": "prepared",
                        "capture_id": capture_id,
                        "counts": _planned_counts(plan),
                    }
                )
                progress["targets"][str(meeting_id)] = details
                await _save_progress(subject, operation, body, claim, progress)

            status, counts = await _erase_one(
                subject,
                meeting_id,
                policy_version,
                erased_at,
                before_delete=remember_plan,
            )
            prepared = _target_progress(progress, meeting_id)
            prepared_counts = prepared.get("counts") if isinstance(prepared.get("counts"), dict) else None
            if status == "already_absent" and prepared_counts is not None:
                # Retention committed before a previous executor could checkpoint completion.
                status, counts = "completed", dict(prepared_counts)
            details = _target_progress(progress, meeting_id)
            details.update(
                {
                    "state": "raw_erased" if status == "completed" else "already_absent",
                    "capture_id": capture_id,
                    "counts": dict(prepared_counts or counts),
                }
            )
            progress["targets"][str(meeting_id)] = details
            await _save_progress(subject, operation, body, claim, progress)
            prior = details
            prior_state = details["state"]
            prior_counts = details["counts"]

        # A mapped capture is removed only after its terminal callbacks were durable and Hub
        # acknowledged them. If raw data vanished during a crash, the mapping fallback above
        # lets this retry finish exactly that cleanup.
        if capture_id:
            if not await store.terminal_callbacks_delivered(str(capture_id)):
                raise ErasureFailed("terminal settlement requires retry")
            await store.finalize_erased_capture(subject=subject, meeting_id=meeting_id)
        details = _target_progress(progress, meeting_id)
        details["state"] = "finalized"
        details["capture_id"] = capture_id
        details["counts"] = dict(prior_counts or _empty_counts())
        progress["targets"][str(meeting_id)] = details
        await _save_progress(subject, operation, body, claim, progress)
        if details["counts"] == _empty_counts() and prior_state == "already_absent":
            return "already_absent", details["counts"]
        return "completed", details["counts"]

    @router.post("/api/zaki/control/v1/{user_id}/meetings/{meeting_id}/erase")
    async def erase_one(
        user_id: str, meeting_id: str, request: Request,
        x_zaki_control_token: str | None = Header(default=None),
        x_zaki_tenant_id: str | None = Header(default=None), x_zaki_user_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None),
    ):
        body, error = await _body(request, "EraseMeetingRequest", request_id=x_request_id)
        if error:
            return error
        if not _mutation_headers_match(body, x_request_id, idempotency_key) or body.get("meeting_id") != meeting_id:
            return _control_error(422, "invalid_request", request_id=x_request_id)
        subject, code = _binding(
            token=x_zaki_control_token, config=config, path_user_id=user_id,
            tenant_header=x_zaki_tenant_id, user_header=x_zaki_user_id, body=body, now=now(),
        )
        if code:
            return _control_error(401 if code == "auth_failed" else 403, code, request_id=x_request_id)
        existing = await _existing(subject, "erase_meeting", body, x_request_id)
        if existing:
            return existing
        claim = await _claim(subject, "erase_meeting", body)
        existing = _replay_or_error(claim, x_request_id)
        if existing:
            return existing
        policy = await store.get_policy(subject)
        try:
            erased_at = now()
            progress = _operation_progress(claim, erased_at)
            await _save_progress(subject, "erase_meeting", body, claim, progress)
            # Tenant-scoped target lookup includes the metadata-only pre-mapping recovery path;
            # an absent/foreign target stays deliberately indistinguishable as already absent.
            target = await store.get_erasure_target(subject=subject, meeting_id=meeting_id)
            status, counts = await _erase_target(
                subject=subject,
                target=target,
                meeting_id=meeting_id,
                policy_version=policy.policy_version if policy else "zaki-control.v1",
                erased_at=erased_at,
                operation="erase_meeting",
                body=body,
                claim=claim,
                progress=progress,
            )
            result = {
                "api_version": "zaki-control.v1", "operation_id": claim.operation_id,
                "subject": {"tenant_id": subject.tenant_id, "user_id": subject.user_id},
                "scope": "meeting", "target_id": meeting_id, "status": status,
                "receipt": {
                    "receipt_id": progress["receipt_id"],
                    "erased_at": progress["erased_at"],
                    "counts": counts,
                },
            }
            await store.complete_operation(
                subject=subject,
                operation="erase_meeting",
                idempotency_key=body["idempotency_key"],
                response=result,
                fence=claim.fence,
            )
        except ErasureFailed:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        except Exception:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        return _response(result, request_id=x_request_id)

    @router.post("/api/zaki/control/v1/{user_id}/erase")
    async def erase_account(
        user_id: str, request: Request,
        x_zaki_control_token: str | None = Header(default=None),
        x_zaki_tenant_id: str | None = Header(default=None), x_zaki_user_id: str | None = Header(default=None),
        x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None),
    ):
        body, error = await _body(request, "EraseAccountRequest", request_id=x_request_id)
        if error:
            return error
        if not _mutation_headers_match(body, x_request_id, idempotency_key):
            return _control_error(422, "invalid_request", request_id=x_request_id)
        subject, code = _binding(
            token=x_zaki_control_token, config=config, path_user_id=user_id,
            tenant_header=x_zaki_tenant_id, user_header=x_zaki_user_id, body=body, now=now(),
        )
        if code:
            return _control_error(401 if code == "auth_failed" else 403, code, request_id=x_request_id)
        existing = await _existing(subject, "erase_account", body, x_request_id)
        if existing:
            return existing
        claim = await _claim(subject, "erase_account", body)
        existing = _replay_or_error(claim, x_request_id)
        if existing:
            return existing
        policy = await store.get_policy(subject)
        try:
            erased_at = now()
            progress = _operation_progress(claim, erased_at)
            await _save_progress(subject, "erase_account", body, claim, progress)
            # The subject barrier is set before enumerating targets. Capture creation uses the
            # same advisory-lock boundary, so a capture cannot slip in after this point and evade
            # account erasure.
            if not await store.begin_subject_erasure(
                subject=subject, operation_id=claim.operation_id, fence=claim.fence
            ):
                return _control_error(409, "minutes_disabled", request_id=x_request_id)
            targets = await store.list_owned_erasure_targets(subject)
            target_by_id = {target.meeting_id: target for target in targets}
            # A crash after raw deletion leaves only progress plus the mapping fallback. Include
            # those IDs on retry even though `meetings` no longer contains a row.
            target_ids = sorted(set(target_by_id) | set(progress["targets"]), key=lambda value: int(value))
            for target in targets:
                target_by_id[target.meeting_id] = target
            for target_id in target_ids:
                target = target_by_id.get(target_id)
                if target is None:
                    target = await store.get_erasure_target(subject=subject, meeting_id=target_id)
                await _erase_target(
                    subject=subject,
                    target=target,
                    meeting_id=target_id,
                    policy_version=policy.policy_version if policy else "zaki-control.v1",
                    erased_at=erased_at,
                    operation="erase_account",
                    body=body,
                    claim=claim,
                    progress=progress,
                )
            totals = _empty_counts()
            statuses = []
            for details in progress["targets"].values():
                if not isinstance(details, dict):
                    continue
                counts = details.get("counts")
                if not isinstance(counts, dict):
                    continue
                status = "completed" if details.get("state") == "finalized" else "already_absent"
                statuses.append(status)
                for key in totals:
                    totals[key] += max(0, int(counts.get(key, 0)))
            await store.erase_subject_control_data(subject)
            await store.finish_subject_erasure(
                subject=subject, operation_id=claim.operation_id, fence=claim.fence
            )
            result = {
                "api_version": "zaki-control.v1", "operation_id": claim.operation_id,
                "subject": {"tenant_id": subject.tenant_id, "user_id": subject.user_id},
                "scope": "account", "status": "completed" if any(status == "completed" for status in statuses) else "already_absent",
                "receipt": {
                    "receipt_id": progress["receipt_id"],
                    "erased_at": progress["erased_at"],
                    "counts": totals,
                },
            }
            await store.complete_operation(
                subject=subject,
                operation="erase_account",
                idempotency_key=body["idempotency_key"],
                response=result,
                fence=claim.fence,
            )
        except ErasureFailed:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        except Exception:
            return _control_error(503, "upstream_unavailable", request_id=x_request_id)
        return _response(result, request_id=x_request_id)

    return router
