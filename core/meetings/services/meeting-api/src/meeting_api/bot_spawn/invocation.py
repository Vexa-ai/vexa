"""Build the bot's invocation (BOT_CONFIG) + the runtime workload spec, conforming to the sealed
``invocation.v1`` + ``runtime.v1`` contracts (validated AT THE SEAM, P8 — loaded by path).

The parent ``meetings.request_bot`` assembled a ``BOT_CONFIG`` dict, minted a stateless
``MeetingToken`` (HS256 JWT) into it, and POSTed a spawn request to the runtime API. This carve
ports the CORE of that:

  * ``mint_meeting_token(...)`` — the parent's hand-rolled HS256 MeetingToken (``ADMIN_TOKEN``-signed;
    claims: meeting_id/user_id/platform/native_meeting_id/scope/iss/aud/iat/exp/jti). The bot carries
    it and the recording-upload endpoint re-verifies it.
  * ``build_invocation(...)`` — the parent's ``BOT_CONFIG`` as an ``invocation.v1`` ``Invocation``
    (camelCase fields, ``None`` stripped). Validated against the sealed schema before it ships.
  * ``build_workload_spec(...)`` — wrap the invocation as the ONE env var the bot reads
    (``BOT_CONFIG``) inside a ``runtime.v1`` ``WorkloadSpec`` (``profile="meeting-bot"``), validated
    against the sealed schema.

continue_meeting / max-bots / join-retry are P3 — NOT here; ``request_bot`` leaves the seam.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import jsonschema
from referencing import Registry, Resource

# ── sealed-schema loaders (the seam, P8 — by path, not import) ──────────────────────────────────


def _load_schema(rel: Path) -> dict:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"sealed contract not found by path: {rel}")


_INVOCATION_SCHEMA = _load_schema(
    Path("meetings") / "contracts" / "invocation.v1" / "invocation.schema.json"
)
_RUNTIME_SCHEMA = _load_schema(
    Path("runtime") / "contracts" / "runtime.v1" / "runtime.schema.json"
)
_INV_REGISTRY = Registry().with_resource(
    _INVOCATION_SCHEMA["$id"], Resource.from_contents(_INVOCATION_SCHEMA)
)
_RT_REGISTRY = Registry().with_resource(
    _RUNTIME_SCHEMA["$id"], Resource.from_contents(_RUNTIME_SCHEMA)
)


def _conforms(obj: dict, schema: dict, registry: Registry, shape: str) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{shape}"}, registry=registry
    ).validate(obj)


def conforms_invocation(obj: dict) -> None:
    """Validate ``obj`` against ``invocation.v1#/$defs/Invocation`` (raises on non-conformance)."""
    _conforms(obj, _INVOCATION_SCHEMA, _INV_REGISTRY, "Invocation")


def conforms_workload_spec(obj: dict) -> None:
    """Validate ``obj`` against ``runtime.v1#/$defs/WorkloadSpec`` (raises on non-conformance)."""
    _conforms(obj, _RUNTIME_SCHEMA, _RT_REGISTRY, "WorkloadSpec")


# ── MeetingToken (HS256 JWT) — ported verbatim from parent meetings.mint_meeting_token ──────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_meeting_token(
    meeting_id: int,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    *,
    ttl_seconds: int = 7200,
    secret: Optional[str] = None,
) -> str:
    """Mint a stateless MeetingToken (HS256 JWT), signed with ``ADMIN_TOKEN`` (or ``secret``).

    No token table — minted on demand, embedded in the invocation, re-verified at recording upload.
    """
    secret = secret if secret is not None else os.environ.get("ADMIN_TOKEN")
    if not secret:
        raise ValueError("ADMIN_TOKEN not configured; cannot mint MeetingToken")
    now = int(datetime.now(timezone.utc).timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "meeting_id": meeting_id,
        "user_id": user_id,
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "scope": "transcribe:write",
        "iss": "meeting-api",
        "aud": "transcription-collector",
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, digestmod="sha256").digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


# ── invocation + workload-spec builders ─────────────────────────────────────────────────────────


def build_invocation(
    *,
    meeting_id: int,
    platform: str,
    meeting_url: Optional[str],
    bot_name: str,
    passcode: Optional[str] = None,
    token: str,
    native_meeting_id: Optional[str],
    connection_id: str,
    language: Optional[str] = None,
    task: Optional[str] = None,
    transcription_tier: str = "realtime",
    redis_url: str,
    automatic_leave: Optional[dict] = None,
    meeting_api_callback_url: Optional[str] = None,
    internal_secret: Optional[str] = None,
    transcribe_enabled: bool = True,
    recording_enabled: bool = False,
    capture_modes: Optional[list[str]] = None,
    recording_upload_url: Optional[str] = None,
    transcription_service_url: Optional[str] = None,
    transcription_service_token: Optional[str] = None,
    transcription_model: Optional[str] = None,
) -> dict:
    """Assemble the bot's ``invocation.v1`` Invocation (the parent's ``BOT_CONFIG``).

    ``None`` values are stripped (the parent strips them before serializing). The result is
    validated against the sealed schema — a malformed invocation never ships.
    """
    invocation: dict[str, Any] = {
        "platform": platform,
        "meetingUrl": meeting_url,
        "botName": bot_name,
        "passcode": passcode,
        "nativeMeetingId": native_meeting_id,
        "token": token,
        "connectionId": connection_id,
        "meeting_id": meeting_id,
        "redisUrl": redis_url,
        "language": language,
        "task": task,
        "transcriptionTier": transcription_tier,
        "transcribeEnabled": transcribe_enabled,
        "transcriptionServiceUrl": transcription_service_url,
        "transcriptionServiceToken": transcription_service_token,
        "transcriptionModel": transcription_model,
        "recordingEnabled": recording_enabled,
        "captureModes": capture_modes,
        "recordingUploadUrl": recording_upload_url,
        "meetingApiCallbackUrl": meeting_api_callback_url,
        "internalSecret": internal_secret,
        "automaticLeave": automatic_leave,
    }
    invocation = {k: v for k, v in invocation.items() if v is not None}
    conforms_invocation(invocation)
    return invocation


def build_workload_spec(
    *,
    workload_id: str,
    invocation: dict,
    callback_url: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
) -> dict:
    """Wrap ``invocation`` as the bot's ONE config env var (``VEXA_BOT_CONFIG``) inside a ``runtime.v1``
    ``WorkloadSpec`` (``profile="meeting-bot"``). The bot image resolves from the kernel's profile
    registry — NOT carried in the spec. Validated against the sealed schema.

    The sealed ``invocation.v1`` contract (ADR-0002) names this env var ``VEXA_BOT_CONFIG`` — what the
    carved v0.12 bot (``config.ts``) and the runtime profile read. We ALSO emit the legacy ``BOT_CONFIG``
    alias so the 0.11-derived published image (``vexaai/vexa-bot:dev``) still boots; ``VEXA_BOT_CONFIG``
    is authoritative. (The mock-bot L3 lane surfaced this: the carved bot got no config under ``BOT_CONFIG``.)"""
    payload = json.dumps(invocation, separators=(",", ":"))
    env: dict[str, str] = {"VEXA_BOT_CONFIG": payload, "BOT_CONFIG": payload}
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})
    spec: dict[str, Any] = {
        "workloadId": workload_id,
        "profile": "meeting-bot",
        "env": env,
    }
    if callback_url:
        spec["callbackUrl"] = callback_url
    conforms_workload_spec(spec)
    return spec
