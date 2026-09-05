"""The admin-api FastAPI surface — v0.12 carve of `services/admin-api/app/main.py`.

Derived (re-read, reimplemented clean) — the load-bearing identity surface that O-STACK-3
exercises:

  3 auth tiers (parent §):
    - admin   : `X-Admin-API-Key` == ADMIN_API_TOKEN (hmac.compare_digest)  → user/token CRUD
    - user    : `X-API-Key` resolves to an APIToken with a valid scope       → /user/* self-serve
    - internal: `X-Internal-Secret` == INTERNAL_API_SECRET, FAIL-CLOSED      → /internal/validate

  /internal/validate (the gateway's authz oracle): returns user_id + scopes + max_concurrent +
  email, plus webhook_url/secret/events from user.data; rejects expired tokens; bumps
  last_used_at; FAILS CLOSED when INTERNAL_API_SECRET is unset (503) and on a bad secret (403).

  Token mint: scoped {bot,tx,browser}. Scopes via JSON body `{"scopes":["bot","tx"]}` or
  query `?scopes=bot,tx` / `?scope=bot` (body wins when present). Optional `name` /
  `expires_in` in body or query; an invalid scope → 422. A JSON body with unknown fields
  is refused (422) — never silently dropped (#922).
"""
import hmac
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_serializer, model_validator
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import APIToken, PlatformSetting, User
from ..token_scope import VALID_SCOPES, generate_prefixed_token
from .db import get_db
from . import events as events_mod
from . import person_settings as person_settings_mod

ADMIN_KEY_HEADER = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)
USER_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _admin_token() -> Optional[str]:
    return os.getenv("ADMIN_API_TOKEN")


def _internal_secret() -> str:
    return os.environ.get("INTERNAL_API_SECRET", "")


def _dev_mode() -> bool:
    return os.getenv("DEV_MODE", "false").lower() == "true"


async def verify_admin_token(admin_api_key: str = Security(ADMIN_KEY_HEADER)):
    token = _admin_token()
    if not token:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Admin authentication is not configured on the server.")
    if not admin_api_key or not hmac.compare_digest(admin_api_key, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid or missing admin token.")


async def get_current_user(api_key: str = Security(USER_KEY_HEADER),
                           db: AsyncSession = Depends(get_db)) -> User:
    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing API Key")
    row = (await db.execute(select(APIToken).where(APIToken.token == api_key))).scalars().first()
    if not row:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid API Key")
    token_scopes = set(row.scopes) if row.scopes else set()
    if not token_scopes & VALID_SCOPES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Token scope not authorized for this endpoint")
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalars().first()
    if not user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid API Key")
    return user


async def get_current_user_for_update(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    return (
        await db.execute(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


# --- request/response models ---
class UserCreate(BaseModel):
    email: str
    name: Optional[str] = None
    max_concurrent_bots: int = 3


class PlatformBillingDataPatch(BaseModel):
    updated_by_webhook: Optional[int] = Field(default=None, ge=0)
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    stripe_tx_subscription_id: Optional[str] = None
    stripe_payment_method_id: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_tier: Optional[str] = None
    subscription_cancel_at_period_end: Optional[bool] = None
    subscription_cancellation_date: Optional[int] = None
    subscription_current_period_start: Optional[int] = None
    subscription_current_period_end: Optional[int] = None
    tx_subscription_status: Optional[str] = None
    tx_subscription_tier: Optional[str] = None
    tx_subscription_cancel_at_period_end: Optional[bool] = None
    tx_subscription_cancellation_date: Optional[int] = None
    tx_subscription_current_period_start: Optional[int] = None
    tx_subscription_current_period_end: Optional[int] = None
    transcription_enabled: Optional[bool] = None
    billing_contract_version: Optional[int] = Field(default=None, ge=1)
    billing_catalog_version: Optional[str] = None
    pending_commitment_tier: Optional[str] = None
    pending_commitment_effective_at: Optional[str] = None

    model_config = {"extra": "forbid"}


class UserAdminPatch(BaseModel):
    max_concurrent_bots: Optional[int] = Field(default=None, ge=0)
    data: Optional[PlatformBillingDataPatch] = None

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def require_change(self):
        has_data = self.data is not None and bool(self.data.model_fields_set)
        if self.max_concurrent_bots is None and not has_data:
            raise ValueError("at least one user field must be supplied")
        return self


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    max_concurrent_bots: int
    data: Dict[str, Any] = Field(default_factory=dict)

    @field_serializer("data")
    def omit_webhook_secret(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in data.items()
            if key != "webhook_secret"
        }

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    id: int
    token: str
    user_id: int
    scopes: List[str]

    model_config = {"from_attributes": True}


class TokenCreate(BaseModel):
    """Mint request body — scopes/name/expires_in may also arrive as query params (compat).

    ``extra='forbid'`` so a caller who sends an unsupported field gets a loud 422 instead of
    a silent drop that mints the wrong token (#922).
    """
    scopes: Optional[List[str]] = None
    name: Optional[str] = None
    expires_in: Optional[int] = Field(default=None, gt=0)

    model_config = {"extra": "forbid"}


class TokenInfo(BaseModel):
    """A token as listed — metadata only, NEVER the secret value (mint is the only place it crosses)."""
    id: int
    user_id: int
    scopes: List[str]
    name: Optional[str] = None
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WebhookUpdate(BaseModel):
    webhook_url: str
    webhook_secret: Optional[str] = None
    webhook_events: Optional[Dict[str, bool]] = None


class CalendarUpdate(BaseModel):
    """The user's calendar-sync self-serve config: a secret ICS feed URL (``null`` disconnects)
    + the GLOBAL auto-join defaults used by imported meetings."""
    ics_url: Optional[str] = None
    auto_join: Optional[bool] = None
    bot_name: Optional[str] = None


class CalendarCreate(BaseModel):
    name: str
    ics_url: str
    auto_join: bool = True
    bot_name: Optional[str] = None

    model_config = {"extra": "forbid"}


class CalendarPatch(BaseModel):
    name: Optional[str] = None
    ics_url: Optional[str] = None
    auto_join: Optional[bool] = None
    bot_name: Optional[str] = None
    enabled: Optional[bool] = None

    model_config = {"extra": "forbid"}


# ── model + transcription config (per-user prefs and the platform-wide defaults) ──
# One vocabulary everywhere: a MODELS config is {mode, model, meeting_model, base_url, api_key}
# (mode "subscription" = the deployment's brokered credential — the mounted Claude Code
# subscription or a deployment API key; mode "custom" = a user/operator-supplied
# Anthropic-/OpenAI-compatible endpoint + key, e.g. a LiteLLM/OpenRouter gateway in front of an
# open-source model). A TRANSCRIPTION config is {url, token} — the STT service the bot invocation
# rides. Per-user copies live in users.data["model_prefs"] / ["transcription_prefs"]; the
# platform defaults live in platform_settings rows "models" / "transcription". Effective config
# resolves FIELD-BY-FIELD user > platform; the process env stays the bottom fallback downstream
# (dispatch/bot_spawn only override what is set here).
MODEL_MODES = ("subscription", "custom")
_MODELS_FIELDS = ("mode", "model", "meeting_model", "base_url", "api_key", "effort")
_TRANSCRIPTION_FIELDS = ("url", "token")
# "setup" tracks the admin first-run wizard: per-step state ("done" / "skipped") + overall
# completion — the terminal re-surfaces the wizard until it reads completed. Plain strings,
# no secrets, admin-gated like the other keys.
_SETUP_FIELDS = ("models", "transcription", "completed")
# "diagnostics" carries the operator kill switches for capture-side telemetry. Today one field:
# capture_signal — whether a spawned bot tees its raw captured-signal.v1 stream to durable storage
# (the offline-replay fixture tape). It is the ONLY control-plane knob on fixture collection, and it
# is a KILL switch, not an enable switch: absence means ON everywhere (see _resolve_capture_signal).
# Written as a STRING like every other settings field ("false" to disable, "" to clear back to the
# default) because _validate_config_fields' one rulebook is string-only.
_DIAGNOSTICS_FIELDS = ("capture_signal",)
# "global_setup" is THE INSTANCE GATE (PRD S9 decision 17; founder 2026-09-02: "global needs to be
# setup by admin, it just should not let him start the service before that"). `state` is "completed"
# once an admin has written and committed the thin company layer into `_global`; ABSENT-OR-ANYTHING-
# ELSE means missing, because this value is read FAIL-CLOSED by everything that can SEND. A fresh
# instance, a cleared row and a half-written value therefore all mean the same thing: this Vexa
# serves nobody yet. `company` is the company name the layer opens with -- evidence of WHAT was
# accepted, never a second source of truth -- and `completed_at` is when. The only writer is
# agent-api's verifier (POST /api/global/ready), which reads the files and the commit before it
# flips anything: nothing may mark itself ready.
_GLOBAL_SETUP_FIELDS = ("state", "company", "completed_at")
SETTING_KEYS = {"models": _MODELS_FIELDS, "transcription": _TRANSCRIPTION_FIELDS,
                "setup": _SETUP_FIELDS, "diagnostics": _DIAGNOSTICS_FIELDS,
                "global_setup": _GLOBAL_SETUP_FIELDS}

# One vocabulary for the gate, so no caller invents its own spelling of "not ready".
GLOBAL_SETUP_COMPLETED = "completed"
GLOBAL_SETUP_MISSING = "missing"

# The one sentence a refused visitor sees, spelled once. Every service that refuses on this gate
# quotes THIS wording; a paraphrase in one client is how a person learns to distrust the product.
GATE_SENTENCE = "This Vexa is being set up by its administrator."


def global_setup_state(value: dict) -> str:
    """Read the gate out of the stored `global_setup` row -- FAIL-CLOSED.

    Anything that is not exactly "completed" is "missing": an absent row, a cleared field, a typo,
    a value half-written by a crashed run. The expensive direction of this decision is a flow
    mailing strangers on behalf of a company nobody has described yet; the cheap direction is
    showing an admin a wizard they have already finished."""
    if isinstance(value, dict) and str(value.get("state", "")).strip() == GLOBAL_SETUP_COMPLETED:
        return GLOBAL_SETUP_COMPLETED
    return GLOBAL_SETUP_MISSING


class ModelPrefsUpdate(BaseModel):
    """Partial update — only fields the caller SENDS change; an empty string clears a field."""
    mode: Optional[str] = None
    model: Optional[str] = None
    meeting_model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    effort: Optional[str] = None  # claude-code reasoning-effort pin (low|medium|high|xhigh); empty = unset


class TranscriptionPrefsUpdate(BaseModel):
    url: Optional[str] = None
    token: Optional[str] = None


def _mask_secret(secret: Optional[str]) -> Optional[str]:
    """The webhook-secret masking rule: never echo a stored secret in the clear — last 4 chars
    behind asterisks, enough to recognize WHICH secret is set."""
    if not secret:
        return None
    return "********" + (secret[-4:] if len(secret) > 8 else "")


def _validate_config_fields(update: dict, *, kind: str) -> dict:
    """Shared field validation for both the per-user prefs and the platform settings writers
    (one rulebook, whichever tier writes). Returns the cleaned update dict."""
    from urllib.parse import urlparse

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


def _apply_config_update(stored: dict, cleaned: dict) -> dict:
    """Overlay a cleaned partial update onto a stored config: set non-empty, drop cleared."""
    out = dict(stored or {})
    for field, value in cleaned.items():
        if value == "":
            out.pop(field, None)
        else:
            out[field] = value
    return out


def _resolve_effective(user_cfg: dict, platform_cfg: dict, fields: tuple) -> dict:
    """FIELD-BY-FIELD user > platform. Only set fields appear — env fallback stays downstream."""
    out: dict = {}
    for field in fields:
        value = user_cfg.get(field) or platform_cfg.get(field)
        if value:
            out[field] = value
    return out


_FLAG_FALSE = ("false", "0", "no", "off")
_FLAG_TRUE = ("true", "1", "yes", "on")


def _as_flag(value) -> Optional[bool]:
    """TRI-STATE read of a stored boolean-ish setting: ``True``/``False`` when the field carries a
    recognized value, ``None`` when it is absent, empty, or unrecognized.

    Tri-state is load-bearing here, unlike ``_resolve_effective``'s truthiness fold: a stored
    ``"false"`` is exactly what a kill switch is FOR, and ``if value:`` would discard it and fall
    through to the next tier. An unrecognized value resolves to ``None`` (fall through) rather than
    to a guess — same discipline as meeting-api's ``env_flag``: a typo is not an explicit opt-out.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _FLAG_TRUE:
            return True
        if v in _FLAG_FALSE:
            return False
    return None


def _resolve_capture_signal(user_data: dict, platform_diagnostics: dict) -> bool:
    """Whether this user's bots tee the captured-signal tape: user > platform_settings > DEFAULT ON.

    DEFAULT ON is the product decision, not an accident of config: prod meetings are the fixture
    source, so absence of any flag means capture. The flag exists to STOP collection fleet-wide with
    no redeploy (``PUT /internal/settings/diagnostics {"capture_signal": "false"}``), and per-user
    (``users.data["diagnostics"]["capture_signal"]``) for an account that must not be taped.
    """
    for source in (user_data.get("diagnostics") or {}, platform_diagnostics or {}):
        flag = _as_flag(source.get("capture_signal") if isinstance(source, dict) else None)
        if flag is not None:
            return flag
    return True


def create_app() -> FastAPI:
    app = FastAPI(title="Vexa Admin API (v0.12)")

    # --- liveness probe (gate:health): process-up, no DB dependency. Readiness (DB reachable)
    # is a separate concern — keeping /health a pure liveness check makes it green without a
    # live Postgres, matching the long-running-service health contract {status:"ok", service}.
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "admin-api"}

    # --- admin tier: user + token CRUD ---
    @app.post("/admin/users", response_model=UserResponse,
              dependencies=[Depends(verify_admin_token)])
    async def create_user(user_in: UserCreate, response: Response,
                          db: AsyncSession = Depends(get_db)):
        # CASE-FOLDED, like the sign-in lookup two hundred lines down (R-B08). An exact match
        # here means `Anna.Smith@acme.com` does not find the account `anna.smith@acme.com`, so
        # this route CREATES A SECOND ONE — a ghost with an empty desk that then receives the
        # meeting report while the real account gets nothing. Email is case-insensitive in its
        # domain and, in every provider we meet, in its local part too; one half of this service
        # already knew that.
        existing = (await db.execute(
            select(User).where(func.lower(User.email) == user_in.email.lower())
        )).scalars().first()
        if existing:
            response.status_code = status.HTTP_200_OK
            return UserResponse.model_validate(existing)
        # ── the one point a person enters ────────────────────────────────────────────────────
        # FIVE independent paths onboard somebody — the control MCP's sign-in verbs, its OAuth door,
        # its shared account_for helper, the terminal's own auth, and the flows mail door when an
        # invite arrives from a stranger. They look like five places to publish `onboarding.completed`
        # and they are not: all five create the account HERE. The single point they already share is
        # where the fact belongs, which is why nothing else had to be refactored to make it true.
        #
        # The STAMP is written in the same transaction as the account, so the record that this person
        # was onboarded survives a publish that never lands — a later sweep can replay from it. That
        # ordering is the whole exactly-once guarantee: it holds against a replay, a restore, and a
        # second producer somebody adds later without reading this comment.
        u = User(email=user_in.email, name=user_in.name,
                 max_concurrent_bots=user_in.max_concurrent_bots)
        u.data = {**(u.data or {}), "onboarding_completed_at": time.time()}
        db.add(u)
        await db.commit()
        await db.refresh(u)
        # FIRE-AND-FORGET. Identity tells flows; it does not ask it. A deployment with no flows
        # domain still onboards people, and so does one where flows is down — the publisher swallows
        # everything and the person is already committed above.
        # Guarded HERE as well as inside the publisher, and neither one alone is load-bearing: the
        # publisher swallows transport failures, this swallows a publisher that changes shape. The
        # thing being protected is a person's sign-in, and it must not depend on anyone remembering.
        #
        # `org` IS EMPTY, AND IT IS PRESENT. Identity holds no organisation for a person — there is
        # no org column, no org field on the create body, and no org anywhere in this service — so
        # the honest value is the empty one. It is emitted rather than omitted because a consumer
        # that finds the key missing cannot tell "identity has no org for them" from "identity did
        # not look", and would go and infer one from the email domain: a second place the answer
        # lives, which is what stating every ref exists to prevent. The earlier shape here read
        # `u.data.get("org")` on the dict assigned two lines above, so it was never anything but
        # None while LOOKING like a lookup — the worst version of this, because it reads as though
        # somebody checked.
        try:
            await events_mod.publish(
                events_mod.EVENT_ONBOARDING_COMPLETED,
                events_mod.onboarding_source_id(u.id),
                events_mod.onboarding_refs(u.id, events_mod.NO_ORG, events_mod.DEFAULT_SEAT))
        except Exception:  # noqa: BLE001 — a publish edge is not a dependency
            pass
        response.status_code = status.HTTP_201_CREATED
        return UserResponse.model_validate(u)

    # --- GET /admin/users/email/{email} → resolve an existing user by email (api.v1). The dashboard
    # login (send-magic-link → findUserByEmail) calls this to find an existing account before minting a
    # session token, so a returning user resolves to their own identity (and meetings) rather than a new
    # one. Mirrors create_user's lookup.
    @app.get("/admin/users/email/{email}", response_model=UserResponse,
             dependencies=[Depends(verify_admin_token)])
    async def get_user_by_email(email: str, db: AsyncSession = Depends(get_db)):
        # Case-folded (R-B08) — see `create_user`. This is the ASKING half of the same question,
        # and the two disagreeing is what mints the ghost: flows asks here, is told "no such
        # user", and creates one.
        user = (await db.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )).scalars().first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserResponse.model_validate(user)

    @app.get("/admin/users/{user_id}", response_model=UserResponse,
             dependencies=[Depends(verify_admin_token)])
    async def get_user_by_id(user_id: int, db: AsyncSession = Depends(get_db)):
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserResponse.model_validate(user)

    @app.patch("/admin/users/{user_id}", response_model=UserResponse,
               dependencies=[Depends(verify_admin_token)])
    async def patch_user_by_id(user_id: int, patch: UserAdminPatch,
                               db: AsyncSession = Depends(get_db)):
        user = (
            await db.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        if patch.max_concurrent_bots is not None:
            user.max_concurrent_bots = patch.max_concurrent_bots
        if patch.data:
            user.data = {
                **(user.data or {}),
                **patch.data.model_dump(exclude_unset=True),
            }
        await db.commit()
        await db.refresh(user)
        return UserResponse.model_validate(user)

    @app.post("/admin/users/{user_id}/tokens", response_model=TokenResponse,
              status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_admin_token)])
    async def create_token_for_user(
        user_id: int,
        body: TokenCreate = Body(default_factory=TokenCreate),
        scope: str = Query("bot"),
        scopes: Optional[str] = Query(None),
        name: Optional[str] = Query(None),
        expires_in: Optional[int] = Query(None),
        db: AsyncSession = Depends(get_db),
    ):
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        # Body scopes win when present — a JSON mint must not silently fall through to ["bot"] (#922).
        if body.scopes is not None:
            scope_list = [s.strip() for s in body.scopes if s and s.strip()]
        elif scopes is not None:
            scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
        else:
            scope_list = [scope]
        if not scope_list:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="scopes must not be empty")
        invalid = [s for s in scope_list if s not in VALID_SCOPES]
        if invalid:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Invalid scope(s): {invalid}. Valid: {sorted(VALID_SCOPES)}")
        token_name = body.name if body.name is not None else name
        token_expires_in = body.expires_in if body.expires_in is not None else expires_in
        token_value = generate_prefixed_token(scope_list[0])
        expires_at = None
        if token_expires_in is not None and token_expires_in > 0:
            expires_at = datetime.utcnow() + timedelta(seconds=token_expires_in)
        tok = APIToken(token=token_value, user_id=user_id, scopes=scope_list,
                       name=token_name, created_at=datetime.utcnow(), expires_at=expires_at)
        db.add(tok)
        await db.commit()
        await db.refresh(tok)
        return TokenResponse.model_validate(tok)

    # --- GET /admin/users/{user_id}/tokens → the user's tokens, metadata only (no secret values).
    # Added for the terminal's token self-serve surface: it lists on the user's behalf (admin tier,
    # scoped server-side to the logged-in user) and verifies ownership before forwarding a revoke.
    @app.get("/admin/users/{user_id}/tokens", response_model=List[TokenInfo],
             dependencies=[Depends(verify_admin_token)])
    async def list_tokens_for_user(user_id: int, db: AsyncSession = Depends(get_db)):
        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        rows = (await db.execute(
            select(APIToken).where(APIToken.user_id == user_id).order_by(APIToken.id)
        )).scalars().all()
        return [TokenInfo.model_validate(t) for t in rows]

    @app.delete("/admin/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(verify_admin_token)])
    async def delete_token(token_id: int, db: AsyncSession = Depends(get_db)):
        tok = await db.get(APIToken, token_id)
        if not tok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Token not found")
        await db.delete(tok)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # --- user tier: webhook self-serve (writes to user.data JSONB) ---
    @app.put("/user/webhook", response_model=UserResponse)
    async def set_user_webhook(webhook_update: WebhookUpdate,
                               user: User = Depends(get_current_user_for_update),
                               db: AsyncSession = Depends(get_db)):
        from sqlalchemy.orm import attributes
        data = dict(user.data or {})
        data["webhook_url"] = webhook_update.webhook_url
        if webhook_update.webhook_secret:
            data["webhook_secret"] = webhook_update.webhook_secret
        if webhook_update.webhook_events is not None:
            data["webhook_events"] = webhook_update.webhook_events
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return UserResponse.model_validate(user)

    @app.get("/user/webhook")
    async def get_user_webhook(user: User = Depends(get_current_user)):
        """Read back the caller's webhook config. The secret NEVER leaves in the clear —
        it is masked to its last 4 chars (`********abcd`), enough to recognize which secret
        is set without disclosing it."""
        data = user.data if isinstance(user.data, dict) else {}
        secret = data.get("webhook_secret")
        masked = None
        if secret:
            masked = "********" + (secret[-4:] if len(secret) > 8 else "")
        return {
            "webhook_url": data.get("webhook_url"),
            "webhook_secret_set": bool(secret),
            "webhook_secret": masked,
            "webhook_events": data.get("webhook_events"),
        }

    # --- user tier: calendar-sync self-serve (writes to user.data JSONB, like webhook) ---
    from .calendars import (MAX_CALENDAR_CONNECTIONS, connections_from_data,
                            masked_connection, new_connection, store_connections,
                            validate_bot_name, validate_ics_url)

    async def _save_calendar_connections(user: User, db: AsyncSession,
                                         connections: list[dict]) -> None:
        from sqlalchemy.orm import attributes
        user.data = store_connections(dict(user.data or {}), connections)
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()

    @app.get("/user/calendars")
    async def list_user_calendars(user: User = Depends(get_current_user)):
        connections = connections_from_data(dict(user.data or {}), user.id)
        return {"calendars": [masked_connection(c) for c in connections]}

    @app.post("/user/calendars", status_code=status.HTTP_201_CREATED)
    async def create_user_calendar(calendar: CalendarCreate,
                                   user: User = Depends(get_current_user_for_update),
                                   db: AsyncSession = Depends(get_db)):
        connections = connections_from_data(dict(user.data or {}), user.id,
                                            include_deleted=True)
        if len([c for c in connections if not c.get("deleted")]) >= MAX_CALENDAR_CONNECTIONS:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail=f"at most {MAX_CALENDAR_CONNECTIONS} calendars can be connected")
        data = dict(user.data or {})
        created = new_connection(
            name=calendar.name,
            ics_url=calendar.ics_url,
            auto_join=calendar.auto_join,
            bot_name=calendar.bot_name or data.get("calendar_bot_name") or "Vexa",
        )
        connections.append(created)
        await _save_calendar_connections(user, db, connections)
        return masked_connection(created)

    @app.patch("/user/calendars/{calendar_id}")
    async def update_user_calendar(calendar_id: str, patch: CalendarPatch,
                                   user: User = Depends(get_current_user_for_update),
                                   db: AsyncSession = Depends(get_db)):
        connections = connections_from_data(dict(user.data or {}), user.id,
                                            include_deleted=True)
        target = next((c for c in connections if c["id"] == calendar_id and not c.get("deleted")), None)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="calendar not found")
        if patch.name is not None:
            name = patch.name.strip()
            if not name or len(name) > 100:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid name")
            target["name"] = name
        if patch.ics_url is not None:
            target["ics_url"] = validate_ics_url(patch.ics_url)
        if patch.auto_join is not None:
            target["auto_join"] = bool(patch.auto_join)
        if patch.bot_name is not None:
            target["bot_name"] = validate_bot_name(patch.bot_name)
        if patch.enabled is not None:
            target["enabled"] = bool(patch.enabled)
        await _save_calendar_connections(user, db, connections)
        return masked_connection(target)

    @app.delete("/user/calendars/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_user_calendar(calendar_id: str,
                                   user: User = Depends(get_current_user_for_update),
                                   db: AsyncSession = Depends(get_db)):
        connections = connections_from_data(dict(user.data or {}), user.id,
                                            include_deleted=True)
        target = next((c for c in connections if c["id"] == calendar_id and not c.get("deleted")), None)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="calendar not found")
        target.pop("ics_url", None)
        target["enabled"] = False
        target["deleted"] = True
        await _save_calendar_connections(user, db, connections)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.put("/user/calendar")
    async def set_user_calendar(calendar_update: CalendarUpdate,
                                user: User = Depends(get_current_user_for_update),
                                db: AsyncSession = Depends(get_db)):
        """Set/clear the caller's secret ICS feed URL (+ the global auto-join default for
        imported meetings). ``ics_url: null`` disconnects the calendar. The URL is a SECRET
        (Google/Outlook secret-address feeds) — it is stored, never echoed in the clear."""
        data = dict(user.data or {})
        updated_bot_name = None
        if "bot_name" in calendar_update.model_fields_set:
            bot_name = (calendar_update.bot_name or "").strip()
            if not bot_name:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail="bot_name is required")
            if len(bot_name) > 100:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail="bot_name too long")
            data["calendar_bot_name"] = bot_name
            updated_bot_name = bot_name
            from sqlalchemy.orm import attributes
            user.data = data
            attributes.flag_modified(user, "data")
        connections = connections_from_data(data, user.id, include_deleted=True)
        current = next((c for c in connections if not c.get("deleted")), None)
        if updated_bot_name is not None and current is not None:
            current["bot_name"] = updated_bot_name
        if "ics_url" in calendar_update.model_fields_set:
            url = (calendar_update.ics_url or "").strip()
            if url:
                if current is None:
                    current = new_connection(name="Calendar", ics_url=url,
                                             auto_join=calendar_update.auto_join
                                             if calendar_update.auto_join is not None else True,
                                             bot_name=data.get("calendar_bot_name") or "Vexa")
                    connections.append(current)
                else:
                    current["ics_url"] = validate_ics_url(url)
            else:
                if current is not None:
                    current.pop("ics_url", None)
                    current["enabled"] = False
                    current["deleted"] = True
        if calendar_update.auto_join is not None and current is not None:
            current["auto_join"] = bool(calendar_update.auto_join)
        await _save_calendar_connections(user, db, connections)
        return await get_user_calendar(user)  # the masked read-back shape

    @app.get("/user/calendar")
    async def get_user_calendar(user: User = Depends(get_current_user)):
        """Read back the caller's calendar config. The ICS URL is a secret — masked to its host
        + last 4 chars, enough to recognize WHICH feed is connected without disclosing it."""
        data = user.data if isinstance(user.data, dict) else {}
        current = next(iter(connections_from_data(data, user.id)), None)
        masked = masked_connection(current) if current else None
        return {
            "ics_url_set": bool(masked and masked["ics_url_set"]),
            "ics_url_masked": masked["ics_url_masked"] if masked else None,
            "auto_join": masked["auto_join"] if masked else True,
            "bot_name": data.get("calendar_bot_name") or "Vexa",
        }

    # --- user tier: model + transcription self-serve prefs (users.data JSONB, like webhook) ---
    async def _put_user_prefs(update_fields: dict, data_key: str, user: User,
                              db: AsyncSession) -> dict:
        from sqlalchemy.orm import attributes
        cleaned = _validate_config_fields(update_fields, kind=data_key)
        data = dict(user.data or {})
        data[data_key] = _apply_config_update(data.get(data_key) or {}, cleaned)
        if not data[data_key]:
            data.pop(data_key, None)  # fully cleared → back to platform/env defaults
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        return data.get(data_key) or {}


    # ── person facts (settings-to-identity) ───────────────────────────────────────────────────
    # `timezone` and the mail preferences moved here out of `.settings.json`, a file in a workspace
    # in the AGENT domain. That made flows and the control MCP depend on a third domain for a fact
    # about a PERSON — so a deployment without agents had people with no clock and no way to stop
    # the mail. Identity is the only domain everyone may depend on; these are its kind of fact.
    #
    # `bot_name` is NOT here on purpose: a bot default is a fact about the bot, and meetings already
    # resolves one through /internal/users/{id}/bot-context.

    @app.put("/user/models")
    async def set_user_models(update: ModelPrefsUpdate,
                              user: User = Depends(get_current_user_for_update),
                              db: AsyncSession = Depends(get_db)):
        """Set the caller's model config (partial; empty string clears a field). ``api_key``
        is a SECRET — stored, never echoed in the clear."""
        await _put_user_prefs(update.model_dump(exclude_unset=True), "model_prefs", user, db)
        return await get_user_models(user)

    @app.get("/user/models")
    async def get_user_models(user: User = Depends(get_current_user)):
        data = user.data if isinstance(user.data, dict) else {}
        prefs = data.get("model_prefs") or {}
        return {
            "mode": prefs.get("mode"),
            "model": prefs.get("model"),
            "meeting_model": prefs.get("meeting_model"),
            "base_url": prefs.get("base_url"),
            "effort": prefs.get("effort"),
            "api_key_set": bool(prefs.get("api_key")),
            "api_key": _mask_secret(prefs.get("api_key")),
        }

    @app.put("/user/transcription")
    async def set_user_transcription(update: TranscriptionPrefsUpdate,
                                     user: User = Depends(get_current_user_for_update),
                                     db: AsyncSession = Depends(get_db)):
        """Set the caller's transcription backend override. ``token`` is a SECRET — masked on read."""
        await _put_user_prefs(update.model_dump(exclude_unset=True), "transcription_prefs", user, db)
        return await get_user_transcription(user)

    @app.get("/user/transcription")
    async def get_user_transcription(user: User = Depends(get_current_user)):
        data = user.data if isinstance(user.data, dict) else {}
        prefs = data.get("transcription_prefs") or {}
        return {
            "url": prefs.get("url"),
            "token_set": bool(prefs.get("token")),
            "token": _mask_secret(prefs.get("token")),
        }

    # --- internal tier: the gateway's authz oracle (FAIL-CLOSED) ---
    @app.post("/internal/validate", include_in_schema=False)
    async def validate_token(request: Request, payload: dict, db: AsyncSession = Depends(get_db)):
        secret = _internal_secret()
        # Fail closed: no secret configured → reject unless dev mode.
        if not _dev_mode() and not secret:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="INTERNAL_API_SECRET not configured")
        if secret:
            provided = request.headers.get("X-Internal-Secret", "")
            if not hmac.compare_digest(provided, secret):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid internal secret")

        token = payload.get("token", "")
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing token")

        row = (await db.execute(
            select(APIToken, User).join(User, APIToken.user_id == User.id)
            .where(APIToken.token == token)
        )).first()
        if not row:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        api_token, user = row

        if api_token.expires_at is not None and api_token.expires_at < datetime.utcnow():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired")

        api_token.last_used_at = datetime.utcnow()
        await db.commit()

        scopes = list(api_token.scopes) if api_token.scopes else ["legacy"]
        resp = {
            "user_id": user.id,
            "scopes": scopes,
            "max_concurrent": user.max_concurrent_bots,
            "email": user.email,
            # DB-backed admin role (bootstrap-claimed on a fresh instance) — the terminal's
            # admin gate reads THIS, with its VEXA_ADMIN_EMAILS allowlist kept as an override.
            "is_admin": (user.data or {}).get("is_admin") is True if isinstance(user.data, dict) else False,
        }
        data_blob = user.data if isinstance(user.data, dict) else {}
        if data_blob.get("webhook_url"):
            resp["webhook_url"] = data_blob["webhook_url"]
            if data_blob.get("webhook_secret"):
                resp["webhook_secret"] = data_blob["webhook_secret"]
            if data_blob.get("webhook_events"):
                resp["webhook_events"] = data_blob["webhook_events"]
        # Lane A: the caller's shared-workspace membership ids (from the derived users.data.memberships[]),
        # so the gateway can inject x-user-workspaces → meeting-api authorizes a member's transcript subscribe.
        memberships = data_blob.get("memberships")
        if isinstance(memberships, list):
            resp["workspaces"] = [m["workspace_id"] for m in memberships
                                  if isinstance(m, dict) and m.get("workspace_id")]
        return resp

    # --- internal tier: workspace membership index (Lane M) — the DERIVED users.data.memberships[]
    #     mirror of the authoritative policy/members.json in each shared workspace's git repo. agent-api
    #     (no DB) POSTs mirror updates here over the same X-Internal-Secret internal edge as /internal/
    #     validate. The git file is the source of truth (Q6): this index is a rebuildable listing cache.
    def _check_internal(request: Request) -> None:
        secret = _internal_secret()
        if not _dev_mode() and not secret:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="INTERNAL_API_SECRET not configured")
        if secret:
            provided = request.headers.get("X-Internal-Secret", "")
            if not hmac.compare_digest(provided, secret):
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid internal secret")

    def _check_internal_no_dev_bypass(request: Request) -> None:
        """The internal check WITHOUT the dev-mode escape — for a door that reads or writes ONE
        NAMED PERSON'S data by path id.

        `_check_internal` lets `DEV_MODE=true` with no `INTERNAL_API_SECRET` through unauthenticated.
        For the doors it was written for — `/internal/validate`, the membership index — that is a
        local-development convenience over data the caller could get anyway. For a route shaped
        `/internal/users/{id}/…` it is not the same thing: the id is supplied by the CALLER, so the
        bypass is a cross-user read (or write) of somebody's private preferences with no credential
        at all. The two cases have opposite blast radii and had one check, which is how the weaker
        one ended up guarding the stronger door.

        Dev mode still works; it simply has to name a secret first — a one-line change to a compose
        file against a route that otherwise answers for any person on the instance."""
        secret = _internal_secret()
        if not secret:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=("INTERNAL_API_SECRET not configured — this door reads/writes one named "
                        "person's settings and is never open, dev mode included"))
        provided = request.headers.get("X-Internal-Secret", "")
        if not hmac.compare_digest(provided, secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid internal secret")

    async def _load_user(
        user_id: str,
        db: AsyncSession,
        *,
        for_update: bool = False,
    ) -> User:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown user")
        statement = select(User).where(User.id == uid)
        if for_update:
            statement = statement.with_for_update()
        user = (await db.execute(statement)).scalar_one_or_none()
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown user")
        return user

    # --- internal tier: instance identity — admin existence + the first-sign-in admin claim.
    #     A fresh install has NO admin; the login surface (via the terminal, which fronts this
    #     edge) shows a one-time "set up your instance" claim screen, and the first successful
    #     sign-in becomes the admin. The claim is race-safe: a pg advisory xact lock serializes
    #     concurrent first sign-ins so exactly ONE claims the role. ---
    _BOOTSTRAP_ADMIN_LOCK = 0x5EC4_AD31  # arbitrary app-wide advisory-lock key for the claim

    async def _admin_exists(db: AsyncSession) -> bool:
        row = (await db.execute(
            select(User.id).where(User.data["is_admin"].astext == "true").limit(1)
        )).first()
        return row is not None

    async def _instance_state(db: AsyncSession) -> dict:
        """THE INSTANCE GATE, computed in exactly ONE place.

        Every other service (the terminal, agent-api, the flows engine) reads the gate through one
        of the two doors below -- never by reaching into platform_settings itself. One source of
        truth, one reader function per service, is the whole design: a surface with two readers of
        a lifecycle value does not error when they disagree, it just behaves differently in two
        places and nobody can say which is right."""
        row = await db.get(PlatformSetting, "global_setup")
        value = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
        return {
            "admin_exists": await _admin_exists(db),
            "global_setup": global_setup_state(value),
            "company": value.get("company") or None,
        }

    @app.get("/internal/instance", include_in_schema=False)
    async def instance_status(request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        return {"admin_exists": await _admin_exists(db)}

    @app.get("/admin/instance", include_in_schema=False,
             dependencies=[Depends(verify_admin_token)])
    async def instance_status_admin(db: AsyncSession = Depends(get_db)):
        """The SAME instance state over the admin-key door. The flows engine holds an admin key and
        no internal secret (see flows_steps/common.py), so without this door it would have to infer
        the gate from something else -- and a service that infers the gate IS a second source of
        truth. Same body, same computation, different transport."""
        return await _instance_state(db)

    @app.post("/internal/bootstrap-admin", include_in_schema=False)
    async def bootstrap_admin(payload: dict, request: Request,
                              db: AsyncSession = Depends(get_db)):
        """Claim the admin role for `user_id` IF no admin exists yet. Idempotent and race-safe:
        under the advisory lock the first caller claims, every later caller gets claimed=False.
        A user who already IS the admin re-claims harmlessly (claimed=False, admin_exists=True)."""
        from sqlalchemy import text as sa_text
        from sqlalchemy.orm import attributes

        _check_internal(request)
        user = await _load_user(
            str(payload.get("user_id", "")),
            db,
            for_update=True,
        )
        await db.execute(sa_text("SELECT pg_advisory_xact_lock(:key)"),
                         {"key": _BOOTSTRAP_ADMIN_LOCK})
        if await _admin_exists(db):
            return {"claimed": False, "admin_exists": True}
        data = dict(user.data or {})
        data["is_admin"] = True
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        return {"claimed": True, "admin_exists": True}

    @app.get("/internal/users/{user_id}/memberships", include_in_schema=False)
    async def list_memberships(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        user = await _load_user(user_id, db)
        data = user.data if isinstance(user.data, dict) else {}
        return {"memberships": data.get("memberships", [])}

    @app.post("/internal/users/{user_id}/memberships", include_in_schema=False)
    async def upsert_membership(user_id: str, payload: dict, request: Request,
                                db: AsyncSession = Depends(get_db)):
        """Upsert {workspace_id, role, added_at} into the user's memberships[] (idempotent per ws)."""
        _check_internal(request)
        from sqlalchemy.orm import attributes
        user = await _load_user(user_id, db, for_update=True)
        ws_id = payload.get("workspace_id")
        if not ws_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="workspace_id required")
        entry = {"workspace_id": ws_id, "role": payload.get("role", "viewer"),
                 "added_at": payload.get("added_at")}
        data = dict(user.data or {})
        memberships = [m for m in (data.get("memberships") or []) if m.get("workspace_id") != ws_id]
        memberships.append(entry)
        data["memberships"] = memberships
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        return {"memberships": memberships}

    @app.delete("/internal/users/{user_id}/memberships/{workspace_id}", include_in_schema=False)
    async def remove_membership(user_id: str, workspace_id: str, request: Request,
                                db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        from sqlalchemy.orm import attributes
        user = await _load_user(user_id, db, for_update=True)
        data = dict(user.data or {})
        memberships = [m for m in (data.get("memberships") or []) if m.get("workspace_id") != workspace_id]
        data["memberships"] = memberships
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        return {"memberships": memberships}

    # --- internal tier: calendar-sync configs — meeting-api's ICS poller discovers every user
    #     with a connected feed over the same X-Internal-Secret edge as /internal/validate. The
    #     secret URL crosses ONLY this internal hop (never a user-facing response). ---
    @app.get("/internal/calendar-configs", include_in_schema=False)
    async def list_calendar_configs(request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        from sqlalchemy import or_
        from .calendars import internal_connections
        rows = (await db.execute(select(User).where(or_(
            User.data["calendar_ics_url"].astext.isnot(None),
            User.data["calendar_connections"].astext.isnot(None),
        )))).scalars().all()
        configs = []
        for u in rows:
            data = u.data if isinstance(u.data, dict) else {}
            configs.extend(internal_connections(data, u.id))
        return {"configs": configs}

    # --- internal tier: per-user spawn context — the auto-join sweep's stand-in for the headers
    #     the gateway injects on POST /bots (X-User-Limits + webhook config from /internal/validate).
    #     Same shape /internal/validate returns for those fields, keyed by user id. ---
    async def _platform_setting(key: str, db: AsyncSession) -> dict:
        row = await db.get(PlatformSetting, key)
        return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


    @app.get("/internal/users/{user_id}/settings", include_in_schema=False)
    async def get_user_settings_internal(user_id: str, request: Request,
                                         db: AsyncSession = Depends(get_db)):
        """This person's settings, for flows. An allowed door: flows may call identity, and reading
        `.settings.json` off agent-api — which is what this replaces — was not.

        An unknown user is a 404 and never a defaulted answer: "defaults for somebody who exists"
        and "defaults for somebody who does not" are opposite facts, and the second one means a flow
        is about to mail a person who is not there.

        NO DEV-MODE BYPASS (see `_check_internal_no_dev_bypass`): the person is named in the PATH by
        the caller, so an unauthenticated dev-mode answer here is a cross-user read of somebody's
        private preferences."""
        _check_internal_no_dev_bypass(request)
        user = await _load_user(user_id, db)
        return person_settings_mod.read_person_facts(
            user.data if isinstance(user.data, dict) else {})

    @app.put("/internal/users/{user_id}/settings", include_in_schema=False)
    async def put_user_settings_internal(user_id: str, payload: dict, request: Request,
                                         db: AsyncSession = Depends(get_db)):
        """SET this person's settings — the write half of the door above.

        WHY IT HAD TO EXIST. The read door shipped alone: `person_settings.apply` had no caller
        anywhere, so identity could only ever answer DEFAULTS. Every person who had turned their
        minutes off, or who lives outside UTC, silently reverted on upgrade — mail resumed, in the
        wrong clock — and the vocabulary that was moved here to end "mail everybody everything, in
        UTC" produced exactly that. A read-only settings store is not a settings store.

        Partial: only the keys sent are changed. VALIDATED ALL-OR-NOTHING by `apply` — a
        half-applied change is a person who believes they turned two things off and turned one.
        Refusals name the vocabulary (422) rather than ignoring the key, because a setting that
        silently does nothing is worse than an error.

        `bot_name` IS REFUSED HERE, deliberately. It is a fact about the BOT, the meetings domain
        owns it, and it already has a door (`/internal/users/{id}/bot-context`, backed by the same
        `users.data.calendar_bot_name` this service stores). Accepting it on the PERSON's settings
        door would be a second name for one fact. The one-shot importer below still carries it into
        that store, which is what a migration off the old file has to do."""
        _check_internal_no_dev_bypass(request)
        if not isinstance(payload, dict):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="body must be an object of settings to change")
        if person_settings_mod.BOT_NAME_KEY in payload:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "refused": "bot_name is not a person setting",
                "why": ("a bot default is a fact about the bot; meetings owns it and resolves it "
                        "on every spawn path through /internal/users/{id}/bot-context"),
                "the_settings_that_exist": person_settings_mod.read_person_facts({}),
            })
        from sqlalchemy.orm import attributes

        user = await _load_user(user_id, db, for_update=True)
        try:
            user.data = person_settings_mod.apply(
                user.data if isinstance(user.data, dict) else {}, payload)
        except person_settings_mod.Refused as refused:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.detail)
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return person_settings_mod.read_person_facts(
            user.data if isinstance(user.data, dict) else {})

    @app.post("/admin/users/{user_id}/settings/import", include_in_schema=False,
              dependencies=[Depends(verify_admin_token)])
    async def import_user_settings(user_id: str, payload: dict,
                                   db: AsyncSession = Depends(get_db)):
        """THE ONE-SHOT MIGRATION off `.settings.json`, driven by an operator.

        The body is that file's own shape — a flat object, e.g.
        ``{"timezone": "Europe/Lisbon", "mail_minutes": false, "bot_name": "Notes"}``. An operator
        who still has those files (they lived in each person's workspace in the AGENT domain) POSTs
        each one here; `plan_import` decides, and its three rules are the migration's whole
        contract: a key the person has ALREADY set through the write door is KEPT (so the sweep is
        re-runnable across an estate where somebody has since changed a preference), `bot_name` goes
        into the BOT's own store and only when that store is empty (nobody's bot changes name in
        either direction), and an unknown key is DROPPED rather than refused (a migration that stops
        on one odd key leaves half the estate on the old store, and there is no second run that
        fixes that).

        ADMIN-TIER, not internal: it is an operator act on a named person, and the operator token is
        the credential an operator has. The response says what happened to every key — imported,
        kept, dropped — because a migration whose result you cannot read is a migration nobody can
        confirm ran."""
        if not isinstance(payload, dict):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="body must be the old .settings.json object")
        from sqlalchemy.orm import attributes

        user = await _load_user(user_id, db, for_update=True)
        new_data, imported, kept, dropped = person_settings_mod.plan_import(
            user.data if isinstance(user.data, dict) else {}, payload)
        user.data = new_data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return {
            "imported": sorted(imported),
            "kept": kept,
            "dropped": dropped,
            "settings": person_settings_mod.read(
                user.data if isinstance(user.data, dict) else {}),
        }


    @app.get("/internal/users/{user_id}/bot-context", include_in_schema=False)
    async def get_bot_context(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        user = await _load_user(user_id, db)
        data = user.data if isinstance(user.data, dict) else {}
        resp: dict = {
            "max_concurrent": user.max_concurrent_bots,
            "bot_name": data.get("calendar_bot_name") or "Vexa",
        }
        # Fixture collection (O-TEL-1): whether this spawn tapes its raw captured-signal stream.
        # ALWAYS present in the response — a missing key downstream is indistinguishable from an
        # unreachable identity, and bot_spawn must default ON in BOTH cases, so it is stated here
        # rather than inferred there.
        resp["capture_signal"] = _resolve_capture_signal(
            data, await _platform_setting("diagnostics", db)
        )
        if data.get("webhook_url"):
            resp["webhook_url"] = data["webhook_url"]
            if data.get("webhook_secret"):
                resp["webhook_secret"] = data["webhook_secret"]
            if data.get("webhook_events"):
                resp["webhook_events"] = data["webhook_events"]
        # The effective transcription backend (user pref > platform setting) — bot_spawn overrides
        # its env-derived TRANSCRIPTION_SERVICE_URL/TOKEN with this when present. The token crosses
        # ONLY this internal hop.
        user_transcription = data.get("transcription_prefs") or {}
        platform_transcription = await _platform_setting("transcription", db)
        if user_transcription.get("url"):
            # Selecting a customer endpoint changes the credential owner too. Never fill a
            # missing customer token/model from the platform record: that would disclose a Vexa
            # provider credential to an arbitrary customer-controlled host.
            transcription = {
                key: user_transcription[key]
                for key in _TRANSCRIPTION_FIELDS
                if user_transcription.get(key) not in (None, "")
            }
        else:
            transcription = _resolve_effective(
                user_transcription,
                platform_transcription,
                _TRANSCRIPTION_FIELDS,
            )
        if transcription:
            # Ownership follows the URL that will actually serve this spawn. This non-secret
            # discriminator crosses only the internal bot-context edge; the URL/token remain
            # internal and never enter the public completion provenance.
            transcription["provider"] = (
                "customer" if user_transcription.get("url") else "vexa"
            )
            resp["transcription"] = transcription
        return resp

    # --- internal tier: platform-wide settings (the DB layer under per-user prefs) — written by
    #     the terminal's ADMIN-GATED settings editor over this edge, read by agent-api/meeting-api.
    @app.get("/internal/settings/{key}", include_in_schema=False)
    async def get_platform_setting(key: str, request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        if key not in SETTING_KEYS:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail=f"Unknown setting key. Known: {sorted(SETTING_KEYS)}")
        return {"key": key, "value": await _platform_setting(key, db)}

    @app.put("/internal/settings/{key}", include_in_schema=False)
    async def put_platform_setting(key: str, payload: dict, request: Request,
                                   db: AsyncSession = Depends(get_db)):
        """Partial update, same field rules + clear semantics as the user-tier writers."""
        _check_internal(request)
        fields = SETTING_KEYS.get(key)
        if fields is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail=f"Unknown setting key. Known: {sorted(SETTING_KEYS)}")
        update = {f: payload.get(f) for f in fields if f in payload}
        cleaned = _validate_config_fields(update, kind=key)
        row = await db.get(PlatformSetting, key)
        merged = _apply_config_update(dict(row.value) if row is not None else {}, cleaned)
        if row is None:
            row = PlatformSetting(key=key, value=merged)
        else:
            row.value = merged
        db.add(row)
        await db.commit()
        return {"key": key, "value": merged}

    # --- internal tier: the dispatch-time model config — agent-api resolves the subject's
    #     effective model setup (user pref > platform setting) in ONE call. Secrets (api_key)
    #     cross ONLY this internal hop, straight into the worker's brokered env.
    @app.get("/internal/users/{user_id}/model-config", include_in_schema=False)
    async def get_model_config(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        user = await _load_user(user_id, db)
        data = user.data if isinstance(user.data, dict) else {}
        return {"models": _resolve_effective(
            data.get("model_prefs") or {},
            await _platform_setting("models", db),
            _MODELS_FIELDS,
        )}

    @app.get("/")
    async def root():
        return {"message": "Vexa Admin API (v0.12)"}

    return app
