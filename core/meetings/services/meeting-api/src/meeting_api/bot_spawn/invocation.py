"""Build the bot's invocation (BOT_CONFIG) + the runtime workload spec, conforming to the sealed
``invocation.v1`` + ``runtime.v1`` contracts (validated AT THE SEAM, P8 — loaded by path).

The parent ``meetings.request_bot`` assembled a ``BOT_CONFIG`` dict, minted a stateless
``MeetingToken`` (HS256 JWT) into it, and POSTed a spawn request to the runtime API. This carve
ports the CORE of that:

  * ``mint_meeting_token(...)`` — re-exported from ``meeting_token``: the session-bound HS256
    MeetingToken the bot carries; the lifecycle callback and the uploads admit it for that session.
  * ``build_invocation(...)`` — the parent's ``BOT_CONFIG`` as an ``invocation.v1`` ``Invocation``
    (camelCase fields, ``None`` stripped). Validated against the sealed schema before it ships.
  * ``build_workload_spec(...)`` — wrap the invocation as the ONE env var the bot reads
    (``BOT_CONFIG``) inside a ``runtime.v1`` ``WorkloadSpec`` (``profile="meeting-bot"``), validated
    against the sealed schema.

continue_meeting / max-bots / join-retry are P3 — NOT here; ``request_bot`` leaves the seam.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
from referencing import Registry, Resource

from ..meeting_token import mint_meeting_token  # noqa: F401 — re-exported for the spawn flow

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


# The MeetingToken is minted by ``meeting_token.mint_meeting_token`` (re-exported here for the
# spawn flow and the bot_spawn front door).


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
    allowed_languages: Optional[list[str]] = None,
    task: Optional[str] = None,
    transcription_tier: str = "realtime",
    redis_url: str,
    automatic_leave: Optional[dict] = None,
    meeting_api_callback_url: Optional[str] = None,
    transcribe_enabled: bool = True,
    recording_enabled: bool = False,
    capture_modes: Optional[list[str]] = None,
    capture_signal_enabled: Optional[bool] = None,
    recording_upload_url: Optional[str] = None,
    transcription_service_url: Optional[str] = None,
    transcription_service_token: Optional[str] = None,
    transcription_model: Optional[str] = None,
    transcription_service_owner: Optional[str] = None,
    authenticated: Optional[bool] = None,
    userdata_s3_path: Optional[str] = None,
    s3_endpoint: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    s3_access_key: Optional[str] = None,
    s3_secret_key: Optional[str] = None,
    session_writeback_url: Optional[str] = None,
) -> dict:
    """Assemble the bot's ``invocation.v1`` Invocation (the parent's ``BOT_CONFIG``).

    ``None`` values are stripped (the parent strips them before serializing). The result is
    validated against the sealed schema — a malformed invocation never ships.

    ``session_writeback_url`` rides only beside ``authenticated``: it is where the bot PUTs its
    session-profile.v1 write-back, and a bot older than v0.13.2 refuses an invocation carrying it.
    """
    if session_writeback_url is not None and not authenticated:
        raise ValueError("sessionWritebackUrl is sent only in authenticated mode")
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
        # transcription-language.v1's list (restricted detection). Empty is the same as absent, so
        # it is stripped with the Nones and an auto or forced bot carries no list at all.
        "allowedLanguages": list(allowed_languages) if allowed_languages else None,
        "task": task,
        "transcriptionTier": transcription_tier,
        "transcribeEnabled": transcribe_enabled,
        "transcriptionServiceUrl": transcription_service_url,
        "transcriptionServiceToken": transcription_service_token,
        "transcriptionModel": transcription_model,
        # `customer` only: the bot then holds every request to the endpoint to its outbound URL
        # guard. The deployment's own endpoint is left unstated (an older bot refuses the field).
        "transcriptionServiceOwner": "customer" if transcription_service_owner == "customer" else None,
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
        # The authenticated bot's session write-back sink (session-profile.v1's route, this session's
        # uid in the path). The bot reads it from here and derives no URL from the others.
        "sessionWritebackUrl": session_writeback_url,
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
