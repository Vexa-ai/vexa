"""The person's OWN settings and workspace regime, and identity's MCP tool manifest (Vexa-ai/vexa#1783).

The doors the one MCP binds `settings`, `settings_set`, `workspace_regime` and
`workspace_regime_set` to. The caller's own credential names the person — there is no user id in the
path to type — so these are the public twins of the internal settings doors in `main.py`, which
flows reads by id with the internal secret. The dogfood control rig used to call those internal
doors on the caller's behalf, and kept the regime in a store of its own.

`build_router` takes main's two credential dependencies rather than importing them, so this module
never imports `main` (which includes it).
"""

import json as _json
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import delegation as delegation_mod
from ..schema.models import User
from . import person_settings as person_settings_mod
from . import workspace_regime as workspace_regime_mod
from .db import get_db

#: Identity's MCP tool manifest (ADR-0037), served at `/.well-known/mcp-tools.json`. It sits inside
#: the package so the image, whose build context is this service, carries it.
_MCP_MANIFEST = Path(__file__).resolve().parents[1] / "mcp.tools.v1.json"


class UserSettingChange(BaseModel):
    """One of the person's settings, changed — the body behind `settings_set`."""
    model_config = ConfigDict(extra="forbid")
    setting: str = Field(..., description="The setting: timezone, mail_minutes, mail_join, mail_rsvp, mail_prep or presence.")
    value: str = Field(..., description="Its new value — an IANA zone for timezone; on/off for the rest.")


class WorkspaceRegimeChange(BaseModel):
    """Where the person's own workspace lives — the body behind `workspace_regime_set`."""
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(..., description="`local` (a directory on their machine) or `cloud` (server-side).")
    local_path: str = Field("", description="For `local`: the ABSOLUTE directory on their machine.")


#: What a worker is told when it tries to change a setting only the person changes themselves.
PERSON_ONLY_REFUSAL = ("whether colleagues see you is the person's own choice — they switch it in "
                       "their own settings; a worker acting for them does not change it")


def _delegated(request: Request) -> bool:
    key = request.headers.get("X-API-Key") or ""
    return bool(key) and delegation_mod.is_delegation_token(key)


def build_router(get_current_user: Callable, get_current_user_for_update: Callable) -> APIRouter:
    """The person-scoped doors, bound to main's credential dependencies."""
    router = APIRouter()

    async def _save_user_data(user: User, data: dict, db: AsyncSession) -> None:
        from sqlalchemy.orm import attributes
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()

    @router.get("/user/settings")
    async def get_user_settings(user: User = Depends(get_current_user)):
        """How Vexa behaves for THIS person — every setting, and what each one means. Per-person,
        effective from the next meeting; changing one never touches anyone else."""
        return {
            "settings": person_settings_mod.read_person_facts(
                user.data if isinstance(user.data, dict) else {}),
            "what_each_means": {k: v[2] for k, v in person_settings_mod.VOCAB.items()},
            "to_change": "settings_set(setting=..., value=...) — one at a time",
        }

    @router.put("/user/settings")
    async def set_user_setting(change: UserSettingChange, request: Request,
                               user: User = Depends(get_current_user_for_update),
                               db: AsyncSession = Depends(get_db)):
        """CHANGE ONE of this person's settings when they ask for something that is one. Set it
        rather than explaining it cannot be done, and never edit a flow to achieve it. on/off
        settings accept on/off, true/false, yes/no. An unknown setting is refused WITH the list."""
        if change.setting == person_settings_mod.BOT_NAME_KEY:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "refused": "bot_name is not a person setting",
                "why": "a bot default is a fact about the bot; update_bot_config and the bot's own "
                       "settings carry it",
                "the_settings_that_exist": person_settings_mod.read_person_facts({}),
            })
        if change.setting in person_settings_mod.PERSON_ONLY and _delegated(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={
                "refused": f"{change.setting} is changed by the person only",
                "why": PERSON_ONLY_REFUSAL})
        try:
            data = person_settings_mod.apply(
                user.data if isinstance(user.data, dict) else {}, {change.setting: change.value})
        except person_settings_mod.Refused as refused:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.detail)
        await _save_user_data(user, data, db)
        after = person_settings_mod.read_person_facts(data)
        meaning = person_settings_mod.MEANINGS.get(change.setting, change.setting)
        return {
            "changed": {change.setting: after.get(change.setting)}, "settings": after,
            "tell_your_person": f"Done — {meaning}: now {after.get(change.setting)!r}. It applies from "
                                f"the next meeting.",
            "scope": "this is theirs alone; nobody else's Vexa changed",
        }

    @router.get("/user/workspace-regime")
    async def get_user_workspace_regime(user: User = Depends(get_current_user)):
        """Where this person's OWN workspace lives: `cloud` (server-side, the default) or `local` (a
        directory on their machine their agent manages with its native file tools)."""
        return {"regime": workspace_regime_mod.read(user.data if isinstance(user.data, dict) else {})}

    @router.put("/user/workspace-regime")
    async def set_user_workspace_regime(change: WorkspaceRegimeChange,
                                        user: User = Depends(get_current_user_for_update),
                                        db: AsyncSession = Depends(get_db)):
        """MOVE this person's own workspace: `local` with an ABSOLUTE `local_path` on their machine,
        or back to `cloud`. Group workspaces, company claims and flow outputs stay server-side in
        either mode."""
        try:
            data = workspace_regime_mod.apply(
                user.data if isinstance(user.data, dict) else {}, change.mode, change.local_path)
        except workspace_regime_mod.Refused as refused:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.detail)
        await _save_user_data(user, data, db)
        rec = workspace_regime_mod.read(data)
        return {"regime": rec, **workspace_regime_mod.guidance(rec)}

    @router.get("/.well-known/mcp-tools.json", include_in_schema=False)
    async def mcp_tools_manifest():
        """Identity's MCP tool manifest — what the assembled MCP edge
        discovers and binds against this service's own OpenAPI. OPEN, like `/health`: it names
        routes and argument names, never data and never a credential."""
        try:
            return _json.loads(_MCP_MANIFEST.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=f"this build carries no tool manifest: {e}") from e

    return router
