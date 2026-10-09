"""The platform-wide settings — `GET/PUT /internal/settings/{key}` — and the field rulebook.

The DB layer under per-user prefs: rows of `platform_settings`, written by the terminal's ADMIN-GATED
settings editor over the internal tier and read by agent-api and meeting-api. This module owns:

  * `SETTINGS`, the table of keys the door serves. Each row says the key's fields, the rulebook that
    cleans a write, and — for a key the deployment ALSO sets from its environment — the reader of
    that env half. The door itself names no key: a rule for one key is a row here, not a branch there.
  * `validate_config_fields` / `apply_config_update`, the one rulebook for config fields whichever
    tier writes them (the per-user prefs in `main.py` use it too).
  * `read_platform_setting`, the stored value of one key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import PlatformSetting
from . import signin_allow
from .db import get_db
from .internal_tier import check_internal

# ── model + transcription config (per-user prefs and the platform-wide defaults) ──
# One vocabulary everywhere: a MODELS config is {mode, model, base_url, api_key}
# (mode "subscription" = the deployment's brokered credential — the mounted Claude Code
# subscription or a deployment API key; mode "custom" = a user/operator-supplied
# Anthropic-/OpenAI-compatible endpoint + key, e.g. a LiteLLM/OpenRouter gateway in front of an
# open-source model). A TRANSCRIPTION config is {url, token} — the STT service the bot invocation
# rides. Per-user copies live in users.data["model_prefs"] / ["transcription_prefs"]; the
# platform defaults live in platform_settings rows "models" / "transcription". Effective config
# resolves FIELD-BY-FIELD user > platform; the process env stays the bottom fallback downstream
# (dispatch/bot_spawn only override what is set here).
MODEL_MODES = ("subscription", "custom")
# extra_body: server-specific request fields the OpenAI dialect cannot express, as a JSON string.
# Load-bearing for self-hosted vLLM/Qwen, which returns NO valid JSON unless thinking is disabled
# via {"chat_template_kwargs": {"enable_thinking": false}} — without this field such an endpoint
# could only be configured deployment-wide, never through BYOT.
# effort: the claude-code reasoning-effort pin (low|medium|high|xhigh) — see ModelPrefsUpdate.
# runner: WHICH HARNESS runs this subject's workspace turns (PRD decision 37). Stored here as an
# opaque slug and never validated against a list — agent-api's `llm/registry.HARNESS_RUNNERS`
# is the one authority on what a runner name means, and it drops an unknown one back to the
# deployment default the way a non-allowlisted model is dropped. A second copy of that
# vocabulary in this service would be a second thing to keep in step, and the copy that goes
# stale is always the one furthest from the code that uses it.
# The copilot's second model dial is deliberately absent — it went with the in-product
# inference pipeline (PRD decision 34).
MODELS_FIELDS = ("mode", "model", "base_url", "api_key", "extra_body", "effort", "runner")
TRANSCRIPTION_FIELDS = ("url", "token")
# "setup" tracks the admin first-run wizard: per-step state ("done" / "skipped") + overall
# completion — the terminal re-surfaces the wizard until it reads completed. Plain strings,
# no secrets, admin-gated like the other keys.
# "global" is the HAND-OFF marker: the admin has left the wizard for the setup chat, and a reload
# must resume there rather than throwing them back to step 1. It was missing from this tuple, and
# the omission cost a live blocker on 2026-09-02 — see the write guard below for the whole story.
SETUP_FIELDS = ("models", "transcription", "completed", "global")
# "diagnostics" carries the operator kill switches for capture-side telemetry. Today one field:
# capture_signal — whether a spawned bot tees its raw captured-signal.v1 stream to durable storage
# (the offline-replay fixture tape). It is the ONLY control-plane knob on fixture collection, and it
# is a KILL switch, not an enable switch: absence means ON everywhere (see _resolve_capture_signal).
# Written as a STRING like every other settings field ("false" to disable, "" to clear back to the
# default) because validate_config_fields' one rulebook is string-only.
DIAGNOSTICS_FIELDS = ("capture_signal",)


def validate_config_fields(update: dict) -> dict:
    """Shared field validation for both the per-user prefs and the platform settings writers
    (one rulebook, whichever tier writes). Returns the cleaned update dict."""
    cleaned: dict = {}
    for field, raw in update.items():
        value = (raw or "").strip() if isinstance(raw, str) else raw
        if value in (None, ""):
            cleaned[field] = ""  # explicit clear
            continue
        if not isinstance(value, str) or len(value) > 2048:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"{field} must be a string under 2048 chars")
        if field == "mode" and value not in MODEL_MODES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"mode must be one of {sorted(MODEL_MODES)}")
        if field in ("base_url", "url"):
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail=f"{field} must be an http(s) URL")
        cleaned[field] = value
    return cleaned


def apply_config_update(stored: dict, cleaned: dict) -> dict:
    """Overlay a cleaned partial update onto a stored config: set non-empty, drop cleared."""
    out = dict(stored or {})
    for field, value in cleaned.items():
        if value == "":
            out.pop(field, None)
        else:
            out[field] = value
    return out


# ── "signin": the admin-edited half of the sign-in allow-list ─────────────────────────────────────
# `allow` holds exact addresses and @domain entries. The other half is the deployment's
# VEXA_SIGNIN_ALLOW; the effective list is the union, and the one reader of it is
# POST /internal/signin-admission. Validated by its own rulebook (app/signin_allow.py), not
# validate_config_fields: an entry is not a free string, and a list of colleagues outgrows the
# 2048-character bound the other fields carry.

def _clean_signin(update: dict) -> dict:
    try:
        return {f: signin_allow.normalize_setting(v) for f, v in update.items()}
    except signin_allow.InvalidAllowList as bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(bad.problems))


def _signin_env_half() -> Tuple[Dict[str, str], List[str]]:
    """The deployment's half of the allow-list, read-only, so the admin editing the settings half
    sees the whole effective list — and any env entry that can never match because it is
    malformed, rather than finding out from a refused colleague."""
    env_valid, env_problems = signin_allow.env_entries()
    return {signin_allow.SETTING_FIELD: ", ".join(env_valid)}, env_problems


@dataclass(frozen=True)
class SettingKind:
    fields: Tuple[str, ...]
    #: cleans a partial write of `fields` (raises HTTPException 422 naming what is wrong)
    clean: Callable[[dict], dict] = validate_config_fields
    #: for a key the deployment also sets from its environment: (that half, its problems)
    env_half: Optional[Callable[[], Tuple[Dict[str, str], List[str]]]] = None


SETTINGS: Dict[str, SettingKind] = {
    "models": SettingKind(MODELS_FIELDS),
    "transcription": SettingKind(TRANSCRIPTION_FIELDS),
    "setup": SettingKind(SETUP_FIELDS),
    "diagnostics": SettingKind(DIAGNOSTICS_FIELDS),
    signin_allow.SETTING_KEY: SettingKind(signin_allow.SETTING_FIELDS, clean=_clean_signin,
                                          env_half=_signin_env_half),
}


class PlatformSettingResponse(BaseModel):
    """GET and PUT /internal/settings/{key}. `env` and `env_problems` are present only on a GET of a
    key the deployment also sets from its environment (today `signin`, whose env half is
    VEXA_SIGNIN_ALLOW): `env` is that half in the setting's own field names, read-only here, and
    `env_problems` names every env entry that can never match."""
    key: str
    value: Dict[str, Any]
    env: Optional[Dict[str, str]] = None
    env_problems: Optional[List[str]] = None


async def read_platform_setting(key: str, db: AsyncSession) -> dict:
    row = await db.get(PlatformSetting, key)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


def _kind(key: str) -> SettingKind:
    kind = SETTINGS.get(key)
    if kind is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail=f"Unknown setting key. Known: {sorted(SETTINGS)}")
    return kind


router = APIRouter()


@router.get("/internal/settings/{key}", include_in_schema=False,
            response_model=PlatformSettingResponse, response_model_exclude_unset=True)
async def get_platform_setting(key: str, request: Request, db: AsyncSession = Depends(get_db)):
    check_internal(request)
    kind = _kind(key)
    body: Dict[str, Any] = {"key": key, "value": await read_platform_setting(key, db)}
    if kind.env_half is not None:
        body["env"], body["env_problems"] = kind.env_half()
    return PlatformSettingResponse.model_validate(body)


@router.put("/internal/settings/{key}", include_in_schema=False,
            response_model=PlatformSettingResponse, response_model_exclude_unset=True)
async def put_platform_setting(key: str, payload: dict, request: Request,
                               db: AsyncSession = Depends(get_db)):
    """Partial update, same field rules + clear semantics as the user-tier writers."""
    check_internal(request)
    kind = _kind(key)
    update = {f: payload.get(f) for f in kind.fields if f in payload}
    # A WRITE THAT RECOGNISED NOTHING IS AN ERROR, not a no-op with a 200 on it.
    #
    # This filter silently drops any field not in `fields`. On 2026-09-02 the first-run wizard
    # sent {"global": "handoff"} to record that the admin had left the wizard for the setup
    # chat; "global" was not in SETUP_FIELDS, so the write stored NOTHING and answered 200.
    # The client had no way to know. On the next load the marker was absent, the wizard decided
    # it was still at step 1, rendered its full-screen overlay INSTEAD of the workbench — so the
    # chat it had just handed off to could never mount — and the admin was returned to the
    # beginning. From the outside the button "did nothing"; underneath, every layer reported
    # success. It cost the founder a live rehearsal.
    #
    # The lesson generalises past the missing tuple entry: an API that accepts a write, changes
    # nothing, and says 200 is indistinguishable from one that worked, and no amount of care at
    # the caller can detect it. So refuse. A partially-recognised write still succeeds (a client
    # sending a known field plus noise is not the failure this catches); only a write where
    # NOTHING was understood is refused, and the message names the keys and the vocabulary.
    if payload and not update:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(f"none of {sorted(payload)} is a field of '{key}'. "
                    f"Known fields: {list(kind.fields)}"))
    cleaned = kind.clean(update)
    row = await db.get(PlatformSetting, key)
    merged = apply_config_update(dict(row.value) if row is not None else {}, cleaned)
    if row is None:
        row = PlatformSetting(key=key, value=merged)
    else:
        row.value = merged
    db.add(row)
    await db.commit()
    return PlatformSettingResponse.model_validate({"key": key, "value": merged})
