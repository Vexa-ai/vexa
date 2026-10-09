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
import logging
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import (APIToken, Meeting, MeetingSession, PlatformSetting,
                             Transcription, User)
from .. import delegation as delegation_mod
from ..token_scope import VALID_SCOPES, generate_prefixed_token
from .db import get_db
from . import events as events_mod
from . import person_settings as person_settings_mod
from . import claim_code
from . import signin_allow
from . import signin_wire
from .internal_tier import check_internal, check_internal_no_dev_bypass

claim_log = logging.getLogger("admin_api.claim")

ADMIN_KEY_HEADER = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)
#: What a worker's delegation token may do at the edge: act in the two service domains for the
#: person it names — the same reach a person's own bot+tx key has, and nothing operator-shaped.
DELEGATED_SCOPES = ("bot", "tx")
USER_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _admin_token() -> Optional[str]:
    return os.getenv("ADMIN_API_TOKEN")


def normalise_email(email: str) -> str:
    """The address as this service STORES it, for a row it is creating now.

    An address is one account whatever case it was typed in (R-B08), and every lookup here already
    folds case. Folding on the READ side alone leaves two holes the folding cannot close:

      * two concurrent `POST /admin/users` with `Anna@x` and `anna@x` both miss the lookup and both
        insert — the read fold has no way to serialise them. Stored folded, the SECOND one collides
        with the `users.email` UNIQUE index that already exists, and `create_user` re-resolves it to
        the first row. The race closes on a constraint rather than on timing.
      * `lower(email)` cannot be unique while the stored values disagree in case, so the functional
        index that would enforce one-address-one-account for good stays non-unique until an operator
        reconciles the rows an instance already holds (schema/MIGRATION-0007-users-email-lower.md).

    NEW ROWS ONLY. Nothing here rewrites an address already stored: an existing row's case is the
    case its person typed, mail already goes there, and a migration that rewrote every address to
    chase an index would be changing data to suit a query plan."""
    return (email or "").strip().lower()


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
_MODELS_FIELDS = ("mode", "model", "base_url", "api_key", "extra_body", "effort", "runner")
_TRANSCRIPTION_FIELDS = ("url", "token")
# "setup" tracks the admin first-run wizard: per-step state ("done" / "skipped") + overall
# completion — the terminal re-surfaces the wizard until it reads completed. Plain strings,
# no secrets, admin-gated like the other keys.
# "global" is the HAND-OFF marker: the admin has left the wizard for the setup chat, and a reload
# must resume there rather than throwing them back to step 1. It was missing from this tuple, and
# the omission cost a live blocker on 2026-09-02 — see the write guard below for the whole story.
_SETUP_FIELDS = ("models", "transcription", "completed", "global")
# "diagnostics" carries the operator kill switches for capture-side telemetry. Today one field:
# capture_signal — whether a spawned bot tees its raw captured-signal.v1 stream to durable storage
# (the offline-replay fixture tape). It is the ONLY control-plane knob on fixture collection, and it
# is a KILL switch, not an enable switch: absence means ON everywhere (see _resolve_capture_signal).
# Written as a STRING like every other settings field ("false" to disable, "" to clear back to the
# default) because _validate_config_fields' one rulebook is string-only.
_DIAGNOSTICS_FIELDS = ("capture_signal",)
# "global_setup" was the company-layer instance gate (founder 2026-09-02). Founder ruling
# 2026-10-08 removed that gate ("let's remove global setup at all so that there is no need to setup
# global at all - let it be empty with no data - it's fine"); the key stays writable so older
# writers and existing rows do not 400, and NOTHING READS IT.
_GLOBAL_SETUP_FIELDS = ("state", "company", "completed_at")
# "signin" is the admin-edited half of the sign-in allow-list (`allow`: exact addresses and
# @domain entries). The other half is the deployment's VEXA_SIGNIN_ALLOW; the effective list is the
# union, and the one reader of it is POST /internal/signin-admission. Validated by its own rulebook
# (app/signin_allow.py), not _validate_config_fields: an entry is not a free string, and a list of
# colleagues outgrows the 2048-character bound the other fields carry.
SETTING_KEYS = {"models": _MODELS_FIELDS, "transcription": _TRANSCRIPTION_FIELDS,
                "setup": _SETUP_FIELDS, "diagnostics": _DIAGNOSTICS_FIELDS,
                "global_setup": _GLOBAL_SETUP_FIELDS,
                signin_allow.SETTING_KEY: signin_allow.SETTING_FIELDS}

# ── the sign-in admission wire, sealed as core/identity/contracts/signin.v1 ─────────────────────────
# The reason vocabularies are GENERATED from that schema (`signin_wire.py`, the terminal's
# `signinWire.ts`), so a reason this service returns is one the terminal knows, and one the schema
# does not know fails the response here instead of reading as a refusal there.

class SigninAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=signin_wire.EMAIL_MAX_LENGTH)
    claim_code: Optional[str] = Field(default=None, max_length=signin_wire.CLAIM_CODE_MAX_LENGTH)


class SigninAdmissionResponse(BaseModel):
    admitted: bool
    why: signin_wire.SigninReason

    @model_validator(mode="after")
    def _reason_matches_verdict(self):
        if self.admitted != (self.why in signin_wire.ADMITTED_REASONS):
            raise ValueError(f"admitted={self.admitted} cannot carry why={self.why!r}")
        return self


class AdminClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int | str
    claim_code: Optional[str] = Field(default=None, max_length=signin_wire.CLAIM_CODE_MAX_LENGTH)


class AdminClaimResponse(BaseModel):
    claimed: bool
    admin_exists: bool
    why: signin_wire.ClaimReason


class ClaimCodeCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_code: str = Field(max_length=signin_wire.CLAIM_CODE_MAX_LENGTH)


class ClaimCodeCheckResponse(BaseModel):
    valid: bool


class InstanceState(BaseModel):
    admin_exists: bool


class ModelPrefsUpdate(BaseModel):
    """Partial update — only fields the caller SENDS change; an empty string clears a field."""
    mode: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    # extra_body was already in `_MODELS_FIELDS` — so the platform setting carried it and the
    # effective-config resolution returned it — but it was NOT in this model, so no per-USER
    # write could ever set it. A field that resolves and cannot be written is a field only the
    # deployment has, silently. It is load-bearing for exactly the case per-user config exists
    # for: a self-hosted vLLM/Qwen endpoint returns no valid JSON at all without
    # {"chat_template_kwargs": {"enable_thinking": false}}.
    extra_body: Optional[str] = None
    effort: Optional[str] = None  # claude-code reasoning-effort pin (low|medium|high|xhigh); empty = unset
    runner: Optional[str] = None  # the harness that runs workspace turns; empty = the deployment's


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


# ── the admin claim, at module level so the boot hook can issue a code before any request ─────────
#: The app-wide advisory-lock key every admin-claim write serializes under.
ADMIN_CLAIM_LOCK = 0x5EC4_AD31


async def admin_claimed(db: AsyncSession) -> bool:
    """Has the admin ROLE been claimed on a user row? (VEXA_ADMIN_EMAILS is the other half of "is
    there an admin"; the instance state joins them.)"""
    row = (await db.execute(
        select(User.id).where(User.data["is_admin"].astext == "true").limit(1)
    )).first()
    return row is not None


async def _claim_record(db: AsyncSession) -> dict:
    row = await db.get(PlatformSetting, claim_code.ROW_KEY)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


async def _write_claim_record(db: AsyncSession, value: dict) -> None:
    """Stage the claim row (the caller commits). Clearing writes `{}` — a consumed code."""
    row = await db.get(PlatformSetting, claim_code.ROW_KEY)
    if row is None:
        row = PlatformSetting(key=claim_code.ROW_KEY, value=value)
    else:
        row.value = value
    db.add(row)


async def issue_admin_claim_code(db: AsyncSession, *, host: str, now: datetime,
                                 force: bool = False) -> "tuple[Optional[str], str]":
    """Issue the one-time admin claim code for an unclaimed instance — (code, note), code None when
    none was issued and the note says why. Under the claim lock, so a boot never races a claim.

    No code while VEXA_ADMIN_EMAILS names the admins or a user holds the role (any stale code is
    retired). A code another replica issued moments ago is kept unless `force` (an explicit release
    of the role asks for a new one)."""
    from sqlalchemy import text as sa_text
    await db.execute(sa_text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMIN_CLAIM_LOCK})
    admins, _ = signin_allow.admin_emails()
    rec = await _claim_record(db)
    if admins or await admin_claimed(db):
        if claim_code.is_live(rec):
            await _write_claim_record(db, {})
        await db.commit()
        return None, ("VEXA_ADMIN_EMAILS names the administrators" if admins
                      else "an administrator has claimed this instance")
    holder = None if force else claim_code.held_elsewhere(rec, host=host, now=now)
    if holder:
        await db.commit()
        return None, f"replica {holder} issued the claim code moments ago — it is in that replica's log"
    code = claim_code.generate()
    await _write_claim_record(db, claim_code.record(code, host=host, now=now))
    await db.commit()
    return code, "issued"


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
        #
        # ORDER BY id — OLDEST WINS, on both halves of this question. An instance that already
        # holds case-variant duplicates (which is precisely the estate this fold exists for) has
        # more than one row matching, and `.first()` without an ORDER BY returns whichever row the
        # PLAN happened to reach first. That is not a stable answer: it can differ between this
        # route and `GET /admin/users/email/{email}`, and it can differ between two calls to the
        # same route after a vacuum. "Which of these accounts is the person" then has two answers,
        # and the desk, the meetings and the mail follow different ones. The oldest row is the one
        # that has the history.
        existing = (await db.execute(
            select(User).where(func.lower(User.email) == user_in.email.lower())
            .order_by(User.id)
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
        #
        # STORED FOLDED — see `normalise_email`. New rows only; nothing rewrites an address already
        # in the table. The read fold above cannot serialise two concurrent creates in different
        # cases (both miss, both insert); folded on write, the second one hits the `users.email`
        # UNIQUE index that has always been there, and the handler below re-resolves it to the row
        # the first one made. The race closes on a constraint instead of on timing.
        u = User(email=normalise_email(user_in.email), name=user_in.name,
                 max_concurrent_bots=user_in.max_concurrent_bots)
        u.data = {**(u.data or {}), "onboarding_completed_at": time.time()}
        db.add(u)
        try:
            await db.commit()
        except IntegrityError:
            # The other half of the race committed first. This is a 200 on THEIR row, exactly as if
            # our lookup had seen it — never a 500, and never a second account.
            await db.rollback()
            winner = (await db.execute(
                select(User).where(func.lower(User.email) == user_in.email.lower())
                .order_by(User.id)
            )).scalars().first()
            if winner is None:
                raise
            response.status_code = status.HTTP_200_OK
            return UserResponse.model_validate(winner)
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
        # ORDER BY id — the same oldest-wins rule as `create_user`, and it has to be the SAME rule:
        # two case-folding lookups that disagree about which duplicate row is the person put the
        # desk on one account and the meetings on another.
        user = (await db.execute(
            select(User).where(func.lower(User.email) == email.lower())
            .order_by(User.id)
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

    @app.put("/admin/users/{user_id}/models", dependencies=[Depends(verify_admin_token)])
    async def set_user_models_as_admin(user_id: int, update: ModelPrefsUpdate,
                                       db: AsyncSession = Depends(get_db)):
        """Set ANOTHER user's model config — the admin-tier twin of ``PUT /user/models``.

        The self-serve route takes the caller's own identity, which is exactly right for a person
        editing their own Settings and exactly wrong for the one caller that has to bind a config
        to somebody else: the rehearsal harness (PRD decision 38) pins a scratch subject under the
        test domain to a runner and an endpoint, and it must be able to do that WITHOUT holding
        that subject's credential and WITHOUT touching the deployment-wide platform setting, which
        would change the model for every person on the instance.

        Same validation, same partial semantics, same masking as the self-serve route — one
        rulebook, two tiers. An empty string clears a field.
        """
        user = (await db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )).scalar_one_or_none()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        await _put_user_prefs(update.model_dump(exclude_unset=True), "model_prefs", user, db)
        prefs = (user.data or {}).get("model_prefs") or {}
        return {"mode": prefs.get("mode"), "model": prefs.get("model"),
                "base_url": prefs.get("base_url"),
                "effort": prefs.get("effort"), "runner": prefs.get("runner"),
                "extra_body": prefs.get("extra_body"),
                "api_key_set": bool(prefs.get("api_key")),
                "api_key": _mask_secret(prefs.get("api_key"))}

    @app.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
                dependencies=[Depends(verify_admin_token)])
    async def delete_user_by_id(user_id: int, db: AsyncSession = Depends(get_db)):
        """DELETE one person and everything keyed to them. Irreversible.

        WHY IT HAS TO EXIST. There was no per-user delete anywhere in the product, so the only way
        to remove somebody was `blank-instance.sh`, which deletes EVERY person on the stack — the
        instrument for "reset one test subject" was a wipe of the whole instance. PRD decision 38.3
        (`subject_reset`) is the caller that needs it: a state re-entered in seconds, the instance
        never blanked.

        IT DELETES EXPLICITLY, IN FK ORDER, because the cascade does not exist: `meetings.user_id`
        is a plain Integer with no ForeignKey to `users`, so removing the row alone would leave
        that person's meetings, sessions and transcripts behind, owned by an id that no longer
        names anybody — the ghost-identity failure the rig hit on 2026-09-02, one layer down. The
        order is the one `blank-instance.sh` documents: transcriptions → meeting_sessions →
        meetings → api_tokens → the user.

        It does NOT touch the workspace volume, redis, or the flows lanes: those stores belong to
        other services, and a route that reached into them would be this service writing three
        surfaces it does not own. `subject_reset` clears them through their own owners.
        """
        user = (await db.execute(
            select(User).where(User.id == user_id).with_for_update()
        )).scalar_one_or_none()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        meeting_ids = [row[0] for row in (await db.execute(
            select(Meeting.id).where(Meeting.user_id == user_id))).all()]
        if meeting_ids:
            await db.execute(delete(Transcription).where(
                Transcription.meeting_id.in_(meeting_ids)))
            await db.execute(delete(MeetingSession).where(
                MeetingSession.meeting_id.in_(meeting_ids)))
            await db.execute(delete(Meeting).where(Meeting.id.in_(meeting_ids)))
        await db.execute(delete(APIToken).where(APIToken.user_id == user_id))
        await db.delete(user)
        await db.commit()
        return None

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
            "base_url": prefs.get("base_url"),
            "effort": prefs.get("effort"),
            "runner": prefs.get("runner"),
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
        check_internal(request)  # fail closed: no secret configured → 503 unless dev mode

        token = payload.get("token", "")
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing token")

        if delegation_mod.is_delegation_token(token):
            return await _validate_delegation(token, db)

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
        return _validated_identity(user, scopes=scopes, is_admin=signin_allow.is_admin(
            user.email, user.data, signin_allow.admin_emails()[0]))

    # A WORKER'S DELEGATION TOKEN (`vxd_…`) is a bearer like an API key, and this oracle answers for
    # both — so every resolver (the gateway, flows) authenticates it the same way and none of them
    # holds the delegation key. agent-api mints it per dispatch (`core/agent/shared/delegation.py`,
    # vendored here byte for byte); it says WHO the worker acts for and the ceiling the dispatch was
    # granted. The account must still exist: a token minted for a deleted user names nobody.
    # Scopes are the two service domains a person's agent acts in (`bot`, `tx`) — never `browser`,
    # never admin — and the ceiling travels on as `delegation`, for the services to enforce.
    async def _validate_delegation(token: str, db: AsyncSession) -> dict:
        secret = os.environ.get("VEXA_MCP_DELEGATION_SECRET", "")
        if not secret:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail="Delegation tokens are not accepted on this deployment")
        try:
            claims = delegation_mod.verify_delegation(secret, token)
        except delegation_mod.DelegationError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid delegation: {e.reason}")
        try:
            uid = int(str(claims["sub"]))
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: malformed")
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid delegation: no such user")
        scope = claims.get("scope") if isinstance(claims.get("scope"), dict) else {}
        resp = _validated_identity(user, scopes=list(DELEGATED_SCOPES), is_admin=False)
        resp["delegation"] = {"regime": str(scope.get("regime") or ""),
                              "workspaces": scope.get("workspaces") if scope.get("workspaces") == "*"
                              else [str(w) for w in (scope.get("workspaces") or [])]}
        if claims.get("target"):
            resp["delegation"]["target"] = str(claims["target"])
        # WHO THE WORKER ACTS FOR, as a fact about that person — never a role the worker holds
        # (`is_admin` above stays False). The MCP edge spends the deployment's operator key for a
        # worker only when its person is the instance admin AND the person is in the loop
        # (regime `human`); it asks the gateway, which reads this beside the regime.
        resp["person_is_admin"] = signin_allow.is_admin(
            user.email, user.data, signin_allow.admin_emails()[0])
        return resp

    def _validated_identity(user: User, *, scopes: list, is_admin: bool) -> dict:
        resp = {
            "user_id": user.id,
            "scopes": scopes,
            "max_concurrent": user.max_concurrent_bots,
            "email": user.email,
            # THE admin test (signin_allow.is_admin) for an API key; never for a worker's delegation.
            # The terminal's admin gate reads this and holds no list of its own.
            "is_admin": is_admin,
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

    # --- internal tier: instance identity — admin existence + the admin claim.
    #     A fresh install has NO admin unless VEXA_ADMIN_EMAILS names them; the login surface (via
    #     the terminal, which fronts this edge) shows a one-time "set up your instance" claim
    #     screen. Who may claim is `signin_allow.may_claim`, decided here and nowhere else. The claim
    #     is race-safe: a pg advisory xact lock serializes concurrent claims so exactly ONE wins. ---
    _BOOTSTRAP_ADMIN_LOCK = ADMIN_CLAIM_LOCK
    _admin_exists = admin_claimed

    async def _instance_state(db: AsyncSession) -> dict:
        """THE INSTANCE STATE, computed in exactly ONE place: does this instance have an admin — a
        claimed one, or the addresses VEXA_ADMIN_EMAILS names? It used to carry the company-layer
        gate as well; that gate is gone (founder ruling 2026-10-08)."""
        admins, _ = signin_allow.admin_emails()
        return {"admin_exists": bool(admins) or await _admin_exists(db)}

    async def _effective_allow(db: AsyncSession) -> list:
        """The sign-in allow-list in force: VEXA_SIGNIN_ALLOW plus the admin-edited `signin` row."""
        env_valid, _ = signin_allow.env_entries()
        return signin_allow.effective(
            env_valid,
            (await _platform_setting(signin_allow.SETTING_KEY, db)).get(signin_allow.SETTING_FIELD, ""))

    @app.get("/internal/instance", include_in_schema=False, response_model=InstanceState)
    async def instance_status(request: Request, db: AsyncSession = Depends(get_db)):
        check_internal(request)
        return await _instance_state(db)

    @app.get("/admin/instance", include_in_schema=False, response_model=InstanceState,
             dependencies=[Depends(verify_admin_token)])
    async def instance_status_admin(db: AsyncSession = Depends(get_db)):
        """The SAME instance state over the admin-key door, for a caller that holds an admin key and
        no internal secret (the dogfood rehearsal rig's blank-instance check). Same body, same
        computation, different transport."""
        return await _instance_state(db)

    @app.post("/internal/bootstrap-admin", include_in_schema=False, response_model=AdminClaimResponse)
    async def bootstrap_admin(payload: AdminClaimRequest, request: Request,
                              db: AsyncSession = Depends(get_db)):
        """Claim the admin role for `user_id` IF `signin_allow.may_claim` allows it — which needs
        the one-time claim code (`claim_code`) unless VEXA_ADMIN_EMAILS already names the admins.
        Race-safe: under the advisory lock the first permitted caller claims and the code is retired
        in the same transaction, so every later caller gets claimed=False.

        Answers {"claimed", "admin_exists", "why"}; `why` is `claimed`, `admin-exists` (somebody
        holds the role, or VEXA_ADMIN_EMAILS names the admins), `bad-code` (no code, a wrong one, or
        one already used) or `not-allowed` (an allow-list is configured and this address is not on
        it)."""
        from sqlalchemy import text as sa_text
        from sqlalchemy.orm import attributes

        check_internal(request)
        user = await _load_user(
            str(payload.user_id),
            db,
            for_update=True,
        )
        await db.execute(sa_text("SELECT pg_advisory_xact_lock(:key)"),
                         {"key": _BOOTSTRAP_ADMIN_LOCK})
        admins, _ = signin_allow.admin_emails()
        claimed_already = await _admin_exists(db)
        code_ok = claim_code.matches(payload.claim_code, await _claim_record(db))
        allowed, why = signin_allow.may_claim(
            user.email, admin_claimed=claimed_already, admins=admins,
            allow=await _effective_allow(db), code_ok=code_ok)
        if not allowed:
            return {"claimed": False, "admin_exists": claimed_already or bool(admins), "why": why}
        data = dict(user.data or {})
        data["is_admin"] = True
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await _write_claim_record(db, {})          # the code works once
        await db.commit()
        return {"claimed": True, "admin_exists": True, "why": why}

    @app.post("/internal/signin-admission", include_in_schema=False,
              response_model=SigninAdmissionResponse)
    async def signin_admission(payload: SigninAdmissionRequest, request: Request,
                               db: AsyncSession = Depends(get_db)):
        """MAY THIS ADDRESS SIGN IN? Asked by every terminal sign-in door BEFORE anything is created
        or sent (Vexa-ai/vexa#1783). Decided here because this service owns every input: the user
        rows, the claimed admin, VEXA_ADMIN_EMAILS, the `signin` settings row, and the
        VEXA_SIGNIN_ALLOW seed. The rule itself is `signin_allow.decide`; the terminal holds no part
        of it.

        Answers `SigninAdmissionResponse` (signin.v1). The caller must read `admitted` as POSITIVE
        evidence — only a literal true admits — so an older admin-api with no such route (404), an
        unreachable one, or a malformed body all refuse. That is the fail-closed direction.

        Internal tier WITHOUT the dev-mode escape: the answer says whether an address has an account
        here, which is exactly the enumeration the sign-in form is built not to reveal, so this door
        is never open without the secret, dev mode included."""
        check_internal_no_dev_bypass(request)
        email = signin_allow.normalize_email(payload.email)
        if not signin_allow.is_address(email):
            return {"admitted": False, "why": signin_allow.WHY_NOT_ALLOWED}
        user = (await db.execute(
            select(User).where(func.lower(User.email) == email).limit(1)
        )).scalars().first()
        data = user.data if user is not None and isinstance(user.data, dict) else {}
        admins, _ = signin_allow.admin_emails()
        allow = await _effective_allow(db)
        # The claim-code door is only consulted when nothing else could admit and it is open at all
        # (nobody claimed, nothing configured) — two queries fewer on the common path.
        admin_claimed_now, code_ok = True, False
        if user is None and not admins and not allow:
            admin_claimed_now = await _admin_exists(db)
            if not admin_claimed_now:
                code_ok = claim_code.matches(payload.claim_code, await _claim_record(db))
        admitted, why = signin_allow.decide(
            email,
            user_exists=user is not None,
            is_admin=data.get("is_admin") is True,
            admins=admins,
            admin_claimed=admin_claimed_now,
            allow=allow,
            claim_code_ok=code_ok,
        )
        return {"admitted": admitted, "why": why}

    @app.post("/internal/admin-claim/check", include_in_schema=False,
              response_model=ClaimCodeCheckResponse)
    async def admin_claim_check(payload: ClaimCodeCheckRequest, request: Request,
                                db: AsyncSession = Depends(get_db)):
        """Is this the live admin claim code? The terminal's claim screen asks before it lets a code
        ride a sign-in, so a typo is answered at once rather than as a sign-in that never arrives.
        True only while the claim is open (nobody claimed, VEXA_ADMIN_EMAILS empty)."""
        check_internal(request)
        admins, _ = signin_allow.admin_emails()
        valid = (not admins and not await _admin_exists(db)
                 and claim_code.matches(payload.claim_code, await _claim_record(db)))
        return {"valid": bool(valid)}

    # --- GET /internal/users/by-email/{email} → JUST the id, for the internal tier ---
    # The post-meeting run mounts the desks of the people who were in the meeting, and it starts
    # from the invite's ATTENDEE addresses. agent-api therefore has to turn an address into a
    # subject, and until this route existed it had only two ways to do it, both wrong:
    #
    #   * `GET /admin/users/email/{email}`, which is gated by `verify_admin_token` — a credential
    #     that can also CREATE and PATCH users. Handing agent-api an admin token so it can ask one
    #     read-only question is a permanent over-grant for a temporary need.
    #   * guessing the subject from a speaker's display name, which mounts the WRONG HUMAN'S desk.
    #     Not a risk worth carrying at any price.
    #
    # So: the narrowest possible door. Same internal-secret tier the gateway's authz oracle already
    # uses, and the response is ONLY the id. Never name, never email, never scopes, never `data` —
    # the caller already knows the address it asked about, and everything else would be a new
    # disclosure this question does not need. A route that answers exactly one question cannot be
    # repurposed into a directory.
    #
    # 404 for an unknown address is deliberate and safe here: the caller is already inside the
    # internal tier, so this leaks nothing to anyone who was not trusted with far more. The mount
    # path treats it as "no subject yet — skip this desk", and the drop step creates it afterwards.
    @app.get("/internal/users/by-email/{email}", include_in_schema=False)
    async def internal_user_id_by_email(email: str, request: Request,
                                        db: AsyncSession = Depends(get_db)):
        check_internal(request)
        # Case-folded (R-B08) — the mount path reads this one, so an exact match here silently
        # drops a mixed-case signup out of every meeting room they are actually in.
        user = (await db.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )).scalars().first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        return {"id": user.id}

    @app.get("/internal/users/{user_id}/is-admin", include_in_schema=False)
    async def user_is_admin(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        """Is THIS subject the instance admin? The role oracle agent-api asks before it mounts the
        organisation tier read-write. It exists because the admin is CLAIMED at first sign-in — long
        after any deployment env was written — so an env allow-list could never have been the
        definition of who may rewrite how every agent in the company behaves."""
        check_internal(request)
        user = await _load_user(user_id, db)
        return {"user_id": user.id, "email": user.email,
                "is_admin": signin_allow.is_admin(user.email, user.data, signin_allow.admin_emails()[0])}

    @app.post("/internal/release-admin", include_in_schema=False)
    async def release_admin(payload: dict, request: Request, db: AsyncSession = Depends(get_db)):
        """RELEASE the admin role from a user so the instance can be claimed again — with a fresh
        claim code, which this answers (`claim_code`) and logs whenever no admin remains.

        The counterpart of bootstrap-admin, and it exists for one honest reason: an instance whose
        admin is a leftover TEST IDENTITY cannot rehearse first-run, and the alternative was hand
        surgery on a jsonb column by whoever remembered the query. A named route is auditable; a
        one-off UPDATE in somebody's shell is not. Internal-tier only, and it deliberately does NOT
        delete the user or anything they own — role, and only role."""
        from sqlalchemy.orm import attributes
        check_internal(request)
        user = await _load_user(str(payload.get("user_id", "")), db, for_update=True)
        data = dict(user.data or {})
        had = data.pop("is_admin", None) is True
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        body = {"user_id": user.id, "email": user.email, "released": had,
                **(await _instance_state(db))}
        # An instance handed back to first run needs a claim code to be claimed again. The caller
        # holds the internal secret, so it is given the code as well as the log.
        if not body["admin_exists"]:
            code, _ = await issue_admin_claim_code(
                db, host=socket.gethostname(), now=datetime.now(timezone.utc), force=True)
            if code:
                claim_log.warning(claim_code.announcement(code))
                body["claim_code"] = code
        return body

    # --- internal tier: workspace membership index (Lane M) — the DERIVED users.data.memberships[]
    #     mirror of the authoritative policy/members.json in each shared workspace's git repo. agent-api
    #     (no DB) POSTs mirror updates here over the same X-Internal-Secret internal edge as /internal/
    #     validate. The git file is the source of truth (Q6): this index is a rebuildable listing cache.
    @app.get("/internal/users/{user_id}/memberships", include_in_schema=False)
    async def list_memberships(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        check_internal(request)
        user = await _load_user(user_id, db)
        data = user.data if isinstance(user.data, dict) else {}
        return {"memberships": data.get("memberships", [])}

    @app.post("/internal/users/{user_id}/memberships", include_in_schema=False)
    async def upsert_membership(user_id: str, payload: dict, request: Request,
                                db: AsyncSession = Depends(get_db)):
        """Upsert {workspace_id, role, added_at} into the user's memberships[] (idempotent per ws)."""
        check_internal(request)
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
        check_internal(request)
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
        check_internal(request)
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

        NO DEV-MODE BYPASS (see `check_internal_no_dev_bypass`): the person is named in the PATH by
        the caller, so an unauthenticated dev-mode answer here is a cross-user read of somebody's
        private preferences."""
        check_internal_no_dev_bypass(request)
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
        check_internal_no_dev_bypass(request)
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
        check_internal(request)
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
        check_internal(request)
        if key not in SETTING_KEYS:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail=f"Unknown setting key. Known: {sorted(SETTING_KEYS)}")
        body = {"key": key, "value": await _platform_setting(key, db)}
        if key == signin_allow.SETTING_KEY:
            # The deployment's half of the allow-list, read-only here, so the admin editing the
            # settings half sees the whole effective list — and any env entry that can never match
            # because it is malformed, rather than finding out from a refused colleague.
            env_valid, env_problems = signin_allow.env_entries()
            body["env"] = {signin_allow.SETTING_FIELD: ", ".join(env_valid)}
            body["env_problems"] = env_problems
        return body

    @app.put("/internal/settings/{key}", include_in_schema=False)
    async def put_platform_setting(key: str, payload: dict, request: Request,
                                   db: AsyncSession = Depends(get_db)):
        """Partial update, same field rules + clear semantics as the user-tier writers."""
        check_internal(request)
        fields = SETTING_KEYS.get(key)
        if fields is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                detail=f"Unknown setting key. Known: {sorted(SETTING_KEYS)}")
        update = {f: payload.get(f) for f in fields if f in payload}
        # A WRITE THAT RECOGNISED NOTHING IS AN ERROR, not a no-op with a 200 on it.
        #
        # This filter silently drops any field not in `fields`. On 2026-09-02 the first-run wizard
        # sent {"global": "handoff"} to record that the admin had left the wizard for the setup
        # chat; "global" was not in _SETUP_FIELDS, so the write stored NOTHING and answered 200.
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
                        f"Known fields: {list(fields)}"))
        if key == signin_allow.SETTING_KEY:
            try:
                cleaned = {f: signin_allow.normalize_setting(v) for f, v in update.items()}
            except signin_allow.InvalidAllowList as bad:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail="; ".join(bad.problems))
        else:
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
        check_internal(request)
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
