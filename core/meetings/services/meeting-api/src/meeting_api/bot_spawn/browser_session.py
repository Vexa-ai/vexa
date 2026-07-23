"""The browser-session spawn seam — ``POST /bots {"platform":"browser_session"}`` (#816).

A browser session is NOT a meeting bot: it has no meeting to address, no join lifecycle, no
transcription. It is a persistent, human-driven Chromium the user reaches over noVNC + Playwright
reaches over CDP. api.v1 seals ``browser_session`` into the Platform enum with ``native_meeting_id``
and ``meeting_url`` both optional, so ``{"platform":"browser_session"}`` is a contractually valid
request the server completes by MINTING the address the caller could not supply.

This flow is DELIBERATELY built AROUND ``build_invocation`` (the invocation.v1 Invocation whose
sealed Platform enum is narrower — google_meet/zoom/teams/jitsi only — and which requires
``meetingUrl``). It builds a ``runtime.v1`` ``WorkloadSpec`` directly, with ``profile =
"browser-session"`` (the kernel resolves it to ``${BROWSER_IMAGE}``) and a session config carried in
one env var, mirroring 0.10's meeting-api browser-session path. No sealed contract is touched:
``profile`` is an open string in runtime.v1, and the session config is not an invocation.v1 object.

The mechanism (0.10 ``meetings.py`` browser_session branch, restored):
  1. mint an unguessable ``session_token`` + a ``bs-<hex>`` native id (the router mints the id;
     this mints the token),
  2. insert the ``Meeting`` row and flip it ACTIVE immediately (no join lifecycle to wait on),
  3. build the browser-session ``WorkloadSpec`` (per-user S3 prefix ``users/{uid}/browser-userdata``),
  4. spawn it over runtime.v1,
  5. write the kernel workload id back, return the api.v1 ``MeetingResponse``.
"""
from __future__ import annotations

import json
import os
import secrets
import uuid
from typing import Any, Optional

from ..obs import log_event
from .invocation import conforms_workload_spec
from .ports import MeetingRepo, QuotaExceeded, RuntimeClient, SpawnFailed
from .service import _meeting_response


def build_browser_session_spec(
    *,
    workload_id: str,
    meeting_id: int,
    user_id: int,
    session_token: str,
    redis_url: str,
    meeting_api_callback_url: str,
    internal_secret: Optional[str] = None,
    s3_config: Optional[dict] = None,
    callback_url: Optional[str] = None,
) -> dict:
    """Assemble the browser-session ``runtime.v1`` ``WorkloadSpec`` (``profile="browser-session"``).

    The session config rides ONE env var (``VEXA_BOT_CONFIG``, with the legacy ``BOT_CONFIG`` alias so
    the published image still boots) and ``BOT_MODE=browser_session`` selects the image's session
    entrypoint. Validated against the sealed runtime.v1 schema — a malformed spec never ships.

    ``s3_config`` (per-user userdata persistence) is merged into the config when present; when the
    deployment has no object store configured it is omitted and the session's userdata lives only in
    the container filesystem (local-only mode), exactly as 0.10 behaved.
    """
    bot_config: dict[str, Any] = {
        "mode": "browser_session",
        "meeting_id": meeting_id,
        "session_token": session_token,
        "redisUrl": redis_url,
        "meetingApiCallbackUrl": meeting_api_callback_url,
        "internalSecret": internal_secret or "",
    }
    if s3_config:
        bot_config.update(s3_config)
    payload = json.dumps(bot_config, separators=(",", ":"))
    spec: dict[str, Any] = {
        "workloadId": workload_id,
        "profile": "browser-session",
        "env": {
            "VEXA_BOT_CONFIG": payload,
            "BOT_CONFIG": payload,
            "BOT_MODE": "browser_session",
        },
    }
    if callback_url:
        spec["callbackUrl"] = callback_url
    conforms_workload_spec(spec)
    return spec


def _s3_config_for(user_id: int) -> dict:
    """Per-user browser-userdata S3 prefix + object-store creds (0.10's MINIO_* env vocabulary).

    Always carries the per-user prefix ``users/{uid}/browser-userdata`` — this is the isolation
    boundary (one user's session never restores another's cookies), and also what fixes the 0.10
    one-bot-per-deployment serialization for the authenticated path (#790). The S3 endpoint/creds are
    added only when an object store is configured; without them the prefix is inert and userdata is
    container-local."""
    userdata_path = f"users/{user_id}/browser-userdata"
    endpoint = (os.environ.get("MINIO_ENDPOINT") or "").strip()
    if not endpoint:
        return {"userdataS3Path": userdata_path}
    secure = (os.environ.get("MINIO_SECURE", "false").lower() == "true")
    return {
        "userdataS3Path": userdata_path,
        "s3Endpoint": f"{'https' if secure else 'http'}://{endpoint}",
        "s3Bucket": os.environ.get("MINIO_BUCKET", "vexa-recordings"),
        "s3AccessKey": os.environ.get("MINIO_ACCESS_KEY", ""),
        "s3SecretKey": os.environ.get("MINIO_SECRET_KEY", ""),
    }


async def request_browser_session(
    repo: MeetingRepo,
    runtime: RuntimeClient,
    *,
    user_id: int,
    native_meeting_id: str,
    max_concurrent: Optional[int] = None,
    redis_url: Optional[str] = None,
    meeting_api_url: Optional[str] = None,
    internal_secret: Optional[str] = None,
) -> dict:
    """Run the browser-session spawn and return a MeetingResponse-shaped dict.

    ``native_meeting_id`` is the ``bs-<hex>`` id the router minted. Raises ``QuotaExceeded`` (429) or
    ``SpawnFailed`` (502) like the meeting-bot flow. The row is ACTIVE on return (no join to wait on).

    NB: the per-user concurrency cap counts browser sessions like any workload here — a browser
    session IS an active workload the deployment provisions (unlike the meeting-bot ``count_active_bots``
    path, which excludes them from the meeting quota). ``max_concurrent=None`` skips the gate.
    """
    redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
    meeting_api_url = meeting_api_url or os.getenv("MEETING_API_URL", "http://meeting-api:8080")
    internal_secret = internal_secret if internal_secret is not None else os.getenv("INTERNAL_API_SECRET")

    session_token = secrets.token_urlsafe(24)

    # 1+2. Insert the row (dedup+cap guarded, TOCTOU-safe) then flip it ACTIVE immediately: a browser
    #      session has no join lifecycle, so it is live the moment its workload is asked for. The
    #      session_token is the unguessable capability the gateway /b/{token} routes resolve; it is
    #      persisted to meeting.data so the dashboard can build the noVNC URL.
    row = await repo.create_meeting_guarded(
        user_id=user_id,
        platform="browser_session",
        native_meeting_id=native_meeting_id,
        data={"mode": "browser_session", "session_token": session_token},
        max_concurrent=max_concurrent,
    )
    meeting_id = row["id"]

    # connection_id == session_uid: the browser session's single session, so a lifecycle callback
    # (exit) and the ACTIVE flip both resolve the meeting via meeting_sessions like a meeting bot does.
    connection_id = str(uuid.uuid4())
    await repo.create_session(meeting_id=meeting_id, session_uid=connection_id)
    active_row = await repo.update_meeting_status(session_uid=connection_id, status="active")
    if active_row is not None:
        row = active_row

    # 3. Build + 4. spawn the browser-session workload (profile="browser-session").
    spec = build_browser_session_spec(
        workload_id=f"bs-{meeting_id}-{connection_id[:8]}",
        meeting_id=meeting_id,
        user_id=user_id,
        session_token=session_token,
        redis_url=redis_url,
        meeting_api_callback_url=f"{meeting_api_url}/bots/internal/callback/lifecycle",
        internal_secret=internal_secret,
        s3_config=_s3_config_for(user_id),
        callback_url=f"{meeting_api_url}/runtime/callback",
    )
    try:
        result = await runtime.create_workload(spec)
    except QuotaExceeded:
        log_event(
            "browser_session_quota_exceeded", audience="user", level="warning",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise
    except SpawnFailed:
        log_event(
            "browser_session_spawn_failed", audience="system", level="error",
            span="bots.create", user_id=user_id, meeting_id=str(meeting_id),
        )
        raise

    workload_id = result.get("workloadId") or result.get("name") or spec["workloadId"]

    # 5. Write the kernel workload id back. On a post-spawn DB failure, tear the workload down (ROB3)
    #    so a live session is never orphaned with no row to resolve it.
    try:
        row = await repo.set_bot_container(meeting_id=meeting_id, bot_container_id=workload_id)
    except Exception as e:  # noqa: BLE001
        try:
            await runtime.delete_workload(workload_id)
        except Exception:  # noqa: BLE001 — teardown is best-effort, never masks the cause
            pass
        raise SpawnFailed(
            f"post-spawn DB write failed; browser-session workload {workload_id} torn down"
        ) from e

    sessions = await repo.list_sessions(meeting_id=meeting_id)
    log_event(
        "browser_session_requested", audience="user", span="bots.create",
        user_id=user_id, meeting_id=f"browser_session/{native_meeting_id}",
        fields={"session_count": len(sessions)},
    )
    return _meeting_response(row, sessions=sessions)
