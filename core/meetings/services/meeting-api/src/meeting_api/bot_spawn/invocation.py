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
    (``BOT_CONFIG``) inside a ``runtime.v1`` ``WorkloadSpec``. ``profile`` is derived from the
    invocation's ``platform`` (``PLATFORM_PROFILES`` below) — ``"meeting-bot"`` for every browser
    platform, ``"discord-bot"`` for discord. Validated against the sealed schema.

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

#: The platforms the MEETING-BOT spawn flow can actually invoke — the sealed invocation.v1
#: Platform enum, read from the schema itself so this set can never drift from what
#: ``build_invocation`` will accept. NB: api.v1's Platform enum is WIDER (it also seals
#: ``browser_session``, whose runtime path is a distinct non-meeting workload — #816); the
#: router refuses the difference with a typed 422 BEFORE any DB write, instead of writing a
#: meeting row and then dying inside the schema validation with a 500 that orphans the row.
SPAWNABLE_PLATFORMS = frozenset(_INVOCATION_SCHEMA["$defs"]["Platform"]["enum"])
_RT_REGISTRY = Registry().with_resource(
    _RUNTIME_SCHEMA["$id"], Resource.from_contents(_RUNTIME_SCHEMA)
)

# Platforms with NO meeting URL at all — a Discord voice channel has no join-by-URL concept: the
# bot is OAuth2-invited to the guild once, then joins a channel by its snowflake id over the
# gateway (``native_meeting_id`` alone is sufficient and authoritative);
# ``https://discord.com/channels/{guild}/{channel}`` is a human deep link the bot never parses
# (#875 A1). Lives here (not service.py, which imports FROM this module) so both the router's
# URL-required gate and ``build_invocation``'s meetingUrl null-survival (below) read the SAME set —
# re-exported from ``service`` for callers that already do ``from .service import
# NO_MEETING_URL_PLATFORMS`` (router.py).
NO_MEETING_URL_PLATFORMS = frozenset({"discord"})

# Platform → runtime profile (core/runtime/src/runtime_kernel/profiles.py's ProfileRegistry keys).
# Every platform not listed here resolves to the shared "meeting-bot" Playwright browser profile —
# only a platform that ships its OWN runtime image (discord's DAVE receive service, not a browser)
# needs an entry. THE single place this mapping lives: runtime.v1 keeps ``profile`` opaque to the
# kernel (it just resolves whatever name arrives in the spec), so platform-awareness belongs here,
# on the meeting-api side, not in the kernel.
PLATFORM_PROFILES = {"discord": "discord-bot"}
_DEFAULT_PROFILE = "meeting-bot"


def profile_for_platform(platform: str) -> str:
    """The ``runtime.v1`` profile a ``platform``'s bot spawns under (``PLATFORM_PROFILES``, default
    ``"meeting-bot"``)."""
    return PLATFORM_PROFILES.get(platform, _DEFAULT_PROFILE)


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
    capture_signal_enabled: Optional[bool] = None,
    recording_upload_url: Optional[str] = None,
    transcription_service_url: Optional[str] = None,
    transcription_service_token: Optional[str] = None,
    transcription_model: Optional[str] = None,
    authenticated: Optional[bool] = None,
    userdata_s3_path: Optional[str] = None,
    s3_endpoint: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    s3_access_key: Optional[str] = None,
    s3_secret_key: Optional[str] = None,
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
        # O-TEL-1 (sealed invocation.v1 field): tee the raw captured-signal.v1 stream to durable
        # storage for offline replay. Orthogonal to recordingEnabled — the transcript and recording
        # paths are unaffected either way. None is STRIPPED below, which leaves the bot on its own
        # VEXA_CAPTURE_SIGNAL env default (the local hot-loop path); the spawn path always passes an
        # explicit boolean so a prod bot never has to guess.
        "captureSignalEnabled": capture_signal_enabled,
        "recordingUploadUrl": recording_upload_url,
        "meetingApiCallbackUrl": meeting_api_callback_url,
        "internalSecret": internal_secret,
        "automaticLeave": automatic_leave,
        # Authenticated-bot mode (sealed invocation.v1 auth block): the bot restores the stored
        # browser session from the userdata store before launch and joins signed-in. Deployment-
        # scoped — set by the BOT_AUTHENTICATED knob in ``request_bot``; None-stripped otherwise
        # so anonymous invocations carry no auth fields at all.
        "authenticated": authenticated,
        "userdataS3Path": userdata_s3_path,
        "s3Endpoint": s3_endpoint,
        "s3Bucket": s3_bucket,
        "s3AccessKey": s3_access_key,
        "s3SecretKey": s3_secret_key,
    }
    # meetingUrl is REQUIRED by the sealed schema but its type is ["string", "null"]. For a platform
    # in NO_MEETING_URL_PLATFORMS (discord — no join-by-URL concept at all) None is a legitimate,
    # PERMANENT null, so the key must survive as JSON null rather than being stripped like every
    # other unset field below (a missing "meetingUrl" fails the schema's required check). Scoped to
    # exactly that set, not every platform: an unscoped strip would also swallow a None meetingUrl
    # for a platform that DOES need one, reaching this function only if router.py's URL-required
    # gate was bypassed (e.g. bot_spawn/auto_join.py calls request_bot directly with
    # ``meeting_url=data.get("constructed_meeting_url")``, which can be None) — scoping it means
    # that bypass still fails LOUD here (the required-field check below), instead of silently
    # spawning a bot with no way to find its meeting.
    invocation = {
        k: v for k, v in invocation.items()
        if v is not None or (k == "meetingUrl" and platform in NO_MEETING_URL_PLATFORMS)
    }
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
    ``WorkloadSpec``. ``profile`` is resolved from ``invocation["platform"]`` via
    ``profile_for_platform`` — ``"meeting-bot"`` for every browser platform, ``"discord-bot"`` for
    discord (PLATFORM_PROFILES, the one place this mapping lives). The bot IMAGE resolves from the
    kernel's profile registry from THAT name — NOT carried in the spec; runtime.v1 keeps ``profile``
    opaque to the kernel (P11), so platform-awareness stays here, on the meeting-api side. Validated
    against the sealed schema.

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
        "profile": profile_for_platform(invocation["platform"]),
        "env": env,
    }
    if callback_url:
        spec["callbackUrl"] = callback_url
    conforms_workload_spec(spec)
    return spec
