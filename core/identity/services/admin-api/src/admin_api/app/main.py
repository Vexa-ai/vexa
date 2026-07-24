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
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema.models import APIToken, PlatformSetting, User
from ..token_scope import VALID_SCOPES, generate_prefixed_token
from .db import get_db

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


# --- request/response models ---
class UserCreate(BaseModel):
    email: str
    name: Optional[str] = None
    max_concurrent_bots: int = 3


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    max_concurrent_bots: int

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
    + the GLOBAL auto-join default stamped onto every imported meeting."""
    ics_url: Optional[str] = None
    auto_join: Optional[bool] = None


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
_MODELS_FIELDS = ("mode", "model", "meeting_model", "base_url", "api_key")
_TRANSCRIPTION_FIELDS = ("url", "token")
# "setup" tracks the admin first-run wizard: per-step state ("done" / "skipped") + overall
# completion — the terminal re-surfaces the wizard until it reads completed. Plain strings,
# no secrets, admin-gated like the other keys.
_SETUP_FIELDS = ("models", "transcription", "completed")
SETTING_KEYS = {"models": _MODELS_FIELDS, "transcription": _TRANSCRIPTION_FIELDS,
                "setup": _SETUP_FIELDS}


class ModelPrefsUpdate(BaseModel):
    """Partial update — only fields the caller SENDS change; an empty string clears a field."""
    mode: Optional[str] = None
    model: Optional[str] = None
    meeting_model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


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
        existing = (await db.execute(select(User).where(User.email == user_in.email))).scalars().first()
        if existing:
            response.status_code = status.HTTP_200_OK
            return UserResponse.model_validate(existing)
        u = User(email=user_in.email, name=user_in.name,
                 max_concurrent_bots=user_in.max_concurrent_bots)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        response.status_code = status.HTTP_201_CREATED
        return UserResponse.model_validate(u)

    # --- GET /admin/users/email/{email} → resolve an existing user by email (api.v1). The dashboard
    # login (send-magic-link → findUserByEmail) calls this to find an existing account before minting a
    # session token, so a returning user resolves to their own identity (and meetings) rather than a new
    # one. Mirrors create_user's lookup.
    @app.get("/admin/users/email/{email}", response_model=UserResponse,
             dependencies=[Depends(verify_admin_token)])
    async def get_user_by_email(email: str, db: AsyncSession = Depends(get_db)):
        user = (await db.execute(select(User).where(User.email == email))).scalars().first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
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
                               user: User = Depends(get_current_user),
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
    @app.put("/user/calendar")
    async def set_user_calendar(calendar_update: CalendarUpdate,
                                user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)):
        """Set/clear the caller's secret ICS feed URL (+ the global auto-join default for
        imported meetings). ``ics_url: null`` disconnects the calendar. The URL is a SECRET
        (Google/Outlook secret-address feeds) — it is stored, never echoed in the clear."""
        from urllib.parse import urlparse

        from sqlalchemy.orm import attributes
        data = dict(user.data or {})
        if "ics_url" in calendar_update.model_fields_set:
            url = (calendar_update.ics_url or "").strip()
            if url:
                if len(url) > 2048:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                        detail="ics_url too long")
                parsed = urlparse(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                        detail="ics_url must be an http(s) URL")
                # Catch the #1 paste mistake up front: Google Calendar's EMBED page (HTML, not a
                # feed). The real feed is Settings -> Integrate calendar -> 'Secret address in
                # iCal format' (ends in .ics). Content-level checks happen at fetch time.
                if "/calendar/embed" in (parsed.path or "").lower():
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=("that's the calendar's embed page, not its feed - in Google "
                                "Calendar open Settings -> Integrate calendar and copy the "
                                "'Secret address in iCal format' (ends in .ics)"))
                data["calendar_ics_url"] = url
            else:
                data.pop("calendar_ics_url", None)
        if calendar_update.auto_join is not None:
            data["calendar_auto_join"] = bool(calendar_update.auto_join)
        user.data = data
        attributes.flag_modified(user, "data")
        db.add(user)
        await db.commit()
        return await get_user_calendar(user)  # the masked read-back shape

    @app.get("/user/calendar")
    async def get_user_calendar(user: User = Depends(get_current_user)):
        """Read back the caller's calendar config. The ICS URL is a secret — masked to its host
        + last 4 chars, enough to recognize WHICH feed is connected without disclosing it."""
        from urllib.parse import urlparse

        data = user.data if isinstance(user.data, dict) else {}
        url = data.get("calendar_ics_url")
        masked = None
        if url:
            host = urlparse(url).hostname or ""
            masked = f"{host}/…{url[-4:]}"
        return {
            "ics_url_set": bool(url),
            "ics_url_masked": masked,
            "auto_join": data.get("calendar_auto_join", True),
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

    @app.put("/user/models")
    async def set_user_models(update: ModelPrefsUpdate,
                              user: User = Depends(get_current_user),
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
            "api_key_set": bool(prefs.get("api_key")),
            "api_key": _mask_secret(prefs.get("api_key")),
        }

    @app.put("/user/transcription")
    async def set_user_transcription(update: TranscriptionPrefsUpdate,
                                     user: User = Depends(get_current_user),
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

    async def _load_user(user_id: str, db: AsyncSession) -> User:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown user")
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
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

    @app.get("/internal/instance", include_in_schema=False)
    async def instance_status(request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        return {"admin_exists": await _admin_exists(db)}

    @app.post("/internal/bootstrap-admin", include_in_schema=False)
    async def bootstrap_admin(payload: dict, request: Request,
                              db: AsyncSession = Depends(get_db)):
        """Claim the admin role for `user_id` IF no admin exists yet. Idempotent and race-safe:
        under the advisory lock the first caller claims, every later caller gets claimed=False.
        A user who already IS the admin re-claims harmlessly (claimed=False, admin_exists=True)."""
        from sqlalchemy import text as sa_text
        from sqlalchemy.orm import attributes

        _check_internal(request)
        user = await _load_user(str(payload.get("user_id", "")), db)
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
        user = await _load_user(user_id, db)
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
        user = await _load_user(user_id, db)
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
        rows = (await db.execute(
            select(User).where(User.data["calendar_ics_url"].astext.isnot(None))
        )).scalars().all()
        configs = []
        for u in rows:
            data = u.data if isinstance(u.data, dict) else {}
            url = data.get("calendar_ics_url")
            if url:
                configs.append({
                    "user_id": u.id,
                    "ics_url": url,
                    "auto_join": data.get("calendar_auto_join", True),
                })
        return {"configs": configs}

    # --- internal tier: per-user spawn context — the auto-join sweep's stand-in for the headers
    #     the gateway injects on POST /bots (X-User-Limits + webhook config from /internal/validate).
    #     Same shape /internal/validate returns for those fields, keyed by user id. ---
    async def _platform_setting(key: str, db: AsyncSession) -> dict:
        row = await db.get(PlatformSetting, key)
        return dict(row.value) if row is not None and isinstance(row.value, dict) else {}

    @app.get("/internal/users/{user_id}/bot-context", include_in_schema=False)
    async def get_bot_context(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
        _check_internal(request)
        user = await _load_user(user_id, db)
        data = user.data if isinstance(user.data, dict) else {}
        resp: dict = {"max_concurrent": user.max_concurrent_bots}
        if data.get("webhook_url"):
            resp["webhook_url"] = data["webhook_url"]
            if data.get("webhook_secret"):
                resp["webhook_secret"] = data["webhook_secret"]
            if data.get("webhook_events"):
                resp["webhook_events"] = data["webhook_events"]
        # The effective transcription backend (user pref > platform setting) — bot_spawn overrides
        # its env-derived TRANSCRIPTION_SERVICE_URL/TOKEN with this when present. The token crosses
        # ONLY this internal hop.
        transcription = _resolve_effective(
            data.get("transcription_prefs") or {},
            await _platform_setting("transcription", db),
            _TRANSCRIPTION_FIELDS,
        )
        if transcription:
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
