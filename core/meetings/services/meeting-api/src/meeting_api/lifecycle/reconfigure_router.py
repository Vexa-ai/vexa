"""The live language change — ``PUT /bots/{platform}/{native_meeting_id}/config`` (api.v1 path,
transcription-language.v1 ``ConfigUpdate`` → ``ConfigAccepted``).

  1. Validate the body (``bot_spawn.transcription_language.config_update``): it REPLACES the running
     setting, so an omitted or null field is cleared. 422 names the field and the fix.
  2. Find the meeting's running bot rows the caller may change:
       * OWNER — the caller's own non-terminal rows for the room;
       * EDITOR — rows bound (``data.workspace_id``) to a workspace the caller is a member of
         (gateway-injected ``x-user-workspaces``) AND in which identity says the caller is an
         ``owner`` or ``contributor``. A ``reader``, or any other role, is refused 403. A role
         identity cannot answer for is 503 — never a guess in either direction.
     Nothing the caller can see → 404, the same answer for "no such meeting" and "not yours", so the
     route does not confirm that somebody else's meeting exists.
  3. PUBLISH an acts.v1 ``reconfigure`` (both fields, always) on each row's own
     ``bot_commands:meeting:{id}``. Redis answers with how many subscribers received it.
  4. Store the setting as ``data.transcription_language`` (source ``meeting``) on every row whose
     bot received it, and answer 202 with it. When no bot received it — still booting, or gone — the
     answer is 409 and NOTHING is stored: the stored setting is what the bot runs with, never what
     somebody asked for.

meeting-api is the one writer of ``data.transcription_language``: at spawn (``bot_spawn.service``)
and here.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol, runtime_checkable

from fastapi import APIRouter, Body, Header, HTTPException

from ..bot_spawn import transcription_language as tlang
from ..obs import log_event
from .stop import leave_command_channel
from .stop_router import CommandPublisher, _resolve_user_id

#: Rows with a bot that may be listening. `stopping` is on its way out and planned rows have no bot.
_RECONFIGURABLE = ("requested", "joining", "awaiting_admission", "active", "needs_human_help")

#: The workspace roles that may change a meeting bound to the workspace.
EDITOR_ROLES = frozenset({"owner", "contributor"})

_SUPPORTED_PLATFORMS = frozenset({"google_meet", "zoom", "teams", "jitsi", "browser_session"})


class RoleUnavailable(Exception):
    """Identity could not say what role the caller holds."""


@runtime_checkable
class WorkspaceRoles(Protocol):
    """The caller's role in a workspace, as identity records it (``users.data.memberships[]``)."""

    async def role_of(self, user_id: int, workspace_id: str) -> Optional[str]:
        """The role, or None when the caller is not a member. Raises ``RoleUnavailable``."""
        ...


class IdentityWorkspaceRoles:
    """Reads ``GET /internal/users/{id}/memberships`` on identity's admin-api over the internal tier."""

    def __init__(self, admin_api_url: str, internal_secret: str, timeout_s: float = 5.0) -> None:
        self._url = admin_api_url.rstrip("/")
        self._secret = internal_secret
        self._timeout = timeout_s

    async def role_of(self, user_id: int, workspace_id: str) -> Optional[str]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(f"{self._url}/internal/users/{user_id}/memberships",
                                     headers={"X-Internal-Secret": self._secret})
        except Exception as e:  # noqa: BLE001 — any transport fault is "unknown", never a role
            raise RoleUnavailable(type(e).__name__) from e
        if r.status_code != 200:
            raise RoleUnavailable(f"identity answered {r.status_code}")
        memberships = (r.json() or {}).get("memberships") or []
        for m in memberships:
            if isinstance(m, dict) and str(m.get("workspace_id")) == workspace_id:
                role = m.get("role")
                return role if isinstance(role, str) else None
        return None


class NoWorkspaceRoles:
    """A deployment without an identity edge configured: no member's role can be confirmed."""

    async def role_of(self, user_id: int, workspace_id: str) -> Optional[str]:
        raise RoleUnavailable("ADMIN_API_URL / INTERNAL_API_SECRET are not set")


def workspace_roles_from_env() -> WorkspaceRoles:
    url = (os.getenv("ADMIN_API_URL") or "").strip()
    secret = os.getenv("INTERNAL_API_SECRET") or ""
    return IdentityWorkspaceRoles(url, secret) if url and secret else NoWorkspaceRoles()


def build_reconfigure_router(repo, publisher: CommandPublisher, roles: WorkspaceRoles) -> APIRouter:
    router = APIRouter()

    @router.put("/bots/{platform}/{native_meeting_id}/config", status_code=202)
    async def update_bot_config(
        platform: str,
        native_meeting_id: str,
        body: Any = Body(default=None),
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        if platform not in _SUPPORTED_PLATFORMS:
            raise HTTPException(status_code=422, detail=(
                f"unsupported platform '{platform}' — must be one of: "
                f"{', '.join(sorted(_SUPPORTED_PLATFORMS))}"))
        try:
            setting, task = tlang.config_update(body)
        except tlang.LanguageRefused as e:
            raise HTTPException(status_code=422, detail=str(e))

        rows = [r for r in await repo.find_active_rows(user_id, platform, native_meeting_id)
                if r.get("status") in _RECONFIGURABLE]
        if not rows:
            rows = await _editable_member_rows(repo, roles, user_id, platform, native_meeting_id,
                                               x_user_workspaces)
        if not rows:
            raise HTTPException(status_code=404, detail="No active bot for this meeting")

        act = json.dumps(tlang.reconfigure_act(setting, task))
        delivered: list[dict] = []
        try:
            for row in rows:
                receivers = await publisher.publish(leave_command_channel(row["id"]), act)
                if isinstance(receivers, int) and receivers > 0:
                    delivered.append(row)
        except Exception as e:  # noqa: BLE001 — the command bus is down: narrow, retryable
            raise HTTPException(status_code=503, detail=(
                "bot command bus (redis) unavailable; nothing was changed — retry")) from e
        if not delivered:
            raise HTTPException(status_code=409, detail=(
                "the bot is not listening yet (still starting) or has left; nothing was changed — "
                "retry once it is in the meeting"))

        effective = {**setting.as_dict(), "source": "meeting"}
        for row in delivered:
            await repo.merge_meeting_data(row["id"], {"transcription_language": effective})
        log_event("bot_language_changed", audience="user", span="bots.config",
                  user_id=user_id, meeting_id=str(delivered[0]["id"]),
                  fields={"language": setting.language,
                          "allowed_languages": list(setting.allowed_languages),
                          "bots": len(delivered)})
        return {
            "meeting_id": delivered[0]["id"],
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "transcription_language": effective,
        }

    return router


async def _editable_member_rows(repo, roles: WorkspaceRoles, user_id: int, platform: str,
                                native_meeting_id: str, x_user_workspaces: Optional[str]) -> list:
    """The running rows bound to a workspace the caller edits, or [] when there are none to see.

    403 when the caller is a member but not an editor of every bound workspace that has one; 503
    when identity cannot say."""
    member_of = {w.strip() for w in (x_user_workspaces or "").split(",") if w.strip()}
    if not member_of:
        return []
    rows = [r for r in await repo.find_active_rows_bound(platform, native_meeting_id, sorted(member_of))
            if r.get("status") in _RECONFIGURABLE]
    if not rows:
        return []
    editable: list[dict] = []
    refused_role: Optional[str] = None
    for row in rows:
        workspace_id = str((row.get("data") or {}).get("workspace_id") or "")
        try:
            role = await roles.role_of(user_id, workspace_id)
        except RoleUnavailable as e:
            raise HTTPException(status_code=503, detail=(
                "your role in this meeting's workspace could not be confirmed; nothing was changed "
                "— retry")) from e
        if role in EDITOR_ROLES:
            editable.append(row)
        else:
            refused_role = role or "none"
    if not editable:
        raise HTTPException(status_code=403, detail=(
            "only the meeting's owner or an owner or contributor of its workspace can change the "
            f"transcription language (your role: {refused_role})"))
    return editable
