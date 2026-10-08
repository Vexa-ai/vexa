"""The credential broker's HTTP front door (credential-broker.v1).

Every route except GET /health requires an X-Vexa-Assertion signed with a role key; the role decides
what the caller may do (contract `x-routes`). The broker owns three things nobody else writes:
connection metadata and its audit trail (metadata.sqlite), the credential store (ADR-0040), and
the OAuth state that binds a consent to the browser session that started it.

No route returns a stored credential to the `agent` or `human` roles. Errors are fixed sentences
and never echo input. Every refusal and every fault is logged as a typed line — source, kind,
route — and never with a value.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import assertion, connection_setup, providers, secret_service, service_oauth
from .obs import TraceMiddleware, log_event
from .settings import Settings, google_client
from .store import Record, Store, StoreUnavailable

PROVIDER_PATTERN = r"^(custom_secret|google_calendar|google_email)$"
_CID = re.compile(r"/[a-f0-9]{32}(?=/|$)")


def route_of(path: str) -> str:
    """A path with connection ids folded to {cid}: low-cardinality, and safe to log."""
    return _CID.sub("/{cid}", path.split("?", 1)[0])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetupBody(Strict):
    label: str = Field(default="Connection", min_length=1, max_length=80)
    provider: str = Field(pattern=PROVIDER_PATTERN)


class CustomSecretBody(Strict):
    setup_request: str = Field(default="", max_length=80)
    confirmed_host: str = Field(default="", max_length=253)
    fields: dict[str, str] = Field(default_factory=dict, max_length=10)
    value: str = Field(default="", max_length=65536)
    endpoint: str = Field(default="", max_length=2000)
    header: str = Field(default="Authorization", max_length=64)
    scheme: Literal["bearer", "raw"] = "bearer"
    method: Literal["GET", "POST"] = "GET"


class OAuthApplicationBody(Strict):
    client_id: str = Field(min_length=1, max_length=200)
    client_secret: str = Field(min_length=1, max_length=2000)
    setup_request: str = Field(max_length=80)
    confirmed_host: str = Field(default="", max_length=253)


class PreparedSetupBody(Strict):
    setup: dict


class AccountReadBody(Strict):
    action: Literal["gmail.search", "gmail.read", "gmail.thread", "calendar.events"]
    query: str = Field(default="", max_length=500)
    page_token: str = Field(default="", max_length=2048)
    message_id: str = Field(default="", max_length=128)
    time_min: str = Field(default="", max_length=40)
    time_max: str = Field(default="", max_length=40)
    limit: int = Field(default=10, ge=1, le=20)


class GmailDraftBody(Strict):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{8,80}$")
    recipient: str = Field(min_length=3, max_length=320)
    subject: str = Field(max_length=500)
    body: str = Field(min_length=1, max_length=50000)


class CustomCallBody(Strict):
    parameters: dict[str, str] = Field(default_factory=dict)
    body: Optional[dict] = None


class GitSecretBody(Strict):
    name: str = Field(pattern=r"^(pat/[A-Za-z0-9_.-]{1,128}|deploy/(user|ws)-[A-Za-z0-9_.-]{1,120}\.(priv|pub))$")
    action: Literal["get", "put", "migrate"]
    value: Optional[str] = Field(default=None, max_length=32768)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, actor TEXT, session TEXT, label TEXT, status TEXT, version INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, actor TEXT, session TEXT, operation_id TEXT, connection TEXT, action TEXT, outcome TEXT, vault_request_id TEXT, version INTEGER, receipt TEXT);
CREATE TABLE IF NOT EXISTS oauth_states (state_hash TEXT PRIMARY KEY, actor TEXT, session TEXT, connection TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS draft_requests (id TEXT PRIMARY KEY, actor TEXT, connection TEXT, fingerprint TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS assertion_nonces (nonce TEXT PRIMARY KEY, expires REAL);
"""
# Columns the 0.13.2 development harness added one release at a time. Kept, with the same names and
# defaults, so a deployment that ran the harness opens its existing metadata.sqlite unchanged.
_COLUMNS = (
    ("provider", "TEXT NOT NULL DEFAULT 'custom_secret'"),
    ("setup_request", "TEXT NOT NULL DEFAULT ''"),
    ("account", "TEXT NOT NULL DEFAULT ''"),
    ("setup_spec", "TEXT NOT NULL DEFAULT ''"),
    ("oauth_app_version", "INTEGER NOT NULL DEFAULT 0"),
    ("approved_host", "TEXT NOT NULL DEFAULT ''"),
)


def _state_key(path: Path) -> bytes:
    """A broker-private HMAC key, generated once into the state directory (0600).

    It derives PKCE verifiers and the draft idempotency fingerprint. It is deliberately NOT the
    store key and not a store token, so rotating either never invalidates a pending consent, and
    deleting this file costs only the consents in flight (L5)."""
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(key)
    tmp.chmod(0o600)
    tmp.replace(path)
    return key


def destination_host(spec: dict) -> str:
    """The host a human's secret is sent to for a prepared setup: the token endpoint for OAuth
    (it receives the client secret), otherwise the service endpoint (it receives the key)."""
    url = (spec.get("oauth") or {}).get("token_url") if spec.get("oauth") else spec.get("endpoint", "")
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


class Broker:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.root = Path(settings.state_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "metadata.sqlite"
        self.lock = threading.RLock()
        with closing(self.db()) as c, c:
            c.executescript(_SCHEMA)
            present = {r[1] for r in c.execute("PRAGMA table_info(connections)")}
            for name, decl in _COLUMNS:
                if name not in present:
                    c.execute(f"ALTER TABLE connections ADD COLUMN {name} {decl}")
        self.hmac_key = _state_key(self.root / "state-hmac.key")

    # ── storage ───────────────────────────────────────────────────────────────────────────
    def db(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def sql(self, statement: str, args=(), *, one=False, rows=False):
        with closing(self.db()) as c, c:
            cur = c.execute(statement, args)
            if one:
                r = cur.fetchone()
                return dict(r) if r else None
            if rows:
                return [dict(r) for r in cur.fetchall()]
            return None

    def put(self, path: str, data: dict, *, cas: Optional[int] = None) -> Record:
        try:
            return self.store.put(path, data, cas=cas)
        except StoreUnavailable as exc:
            self.fault("store", exc.kind)
            raise HTTPException(503, "Credential store unavailable") from None

    def get(self, path: str, *, version: Optional[int] = None) -> Optional[Record]:
        try:
            return self.store.get(path, version=version)
        except StoreUnavailable as exc:
            self.fault("store", exc.kind)
            raise HTTPException(503, "Credential store unavailable") from None

    def must_get(self, path: str, version: int) -> dict:
        record = self.get(path, version=version)
        if record is None:
            self.fault("store", "missing")
            raise HTTPException(503, "Credential store unavailable")
        return record.data

    # ── identity ──────────────────────────────────────────────────────────────────────────
    def key_for(self, role: str) -> bytes:
        return assertion.load_key(self.settings.key_files.get(role, ""))

    def remember(self, nonce: str, expires: float) -> bool:
        now = time.time()
        try:
            with closing(self.db()) as c, c:
                c.execute("DELETE FROM assertion_nonces WHERE expires < ?", (now,))
                c.execute("INSERT INTO assertion_nonces VALUES (?, ?)", (nonce, expires))
            return True
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def identity(request: Request, roles: set) -> dict:
        who = getattr(request.state, "who", None)
        if not who:
            raise HTTPException(401, "Product identity refused")
        if who["role"] not in roles:
            log_event("role_refused", level="warning", user_id=who["actor"],
                      fields={"role": who["role"], "route": route_of(request.url.path)})
            raise HTTPException(403, "Human setup required")
        return who

    def connection(self, who: dict, cid: str) -> dict:
        row = self.sql("SELECT * FROM connections WHERE id=? AND actor=? AND status!='deleted'",
                       (cid, who["actor"]), one=True)
        if not row:
            raise HTTPException(404, "Connection not found")
        return row

    # ── audit and faults ──────────────────────────────────────────────────────────────────
    def audit(self, who, cid, action, outcome, *, operation="", receipt_id="", version=0, receipt=""):
        # Committed BEFORE the action it precedes; a failed insert prevents the action.
        self.sql("INSERT INTO audit(at,actor,session,operation_id,connection,action,outcome,vault_request_id,version,receipt)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (time.time(), who["actor"], who["session"], operation, cid, action, outcome, receipt_id, version, receipt))
        log_event("connection_audit", user_id=who["actor"],
                  fields={"action": action, "outcome": outcome, "connection": cid, "role": who.get("role", ""),
                          "store": self.store.name, "version": version, "operation": operation})

    @staticmethod
    def fault(source: str, kind: str, **fields) -> None:
        log_event("broker_fault", level="warning", fields={"source": source, "kind": kind, **fields})

    def pkce(self, state: str):
        verifier = base64.urlsafe_b64encode(hmac.new(self.hmac_key, state.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return verifier, challenge

    def google(self) -> dict:
        client = google_client(self.settings)
        if client is None:
            # A store carried over from the 0.13.2 development harness kept the application here.
            legacy = self.get("operator-google")
            if legacy and legacy.data.get("client_id") and legacy.data.get("client_secret"):
                client = {"client_id": legacy.data["client_id"], "client_secret": legacy.data["client_secret"]}
        if client is None:
            raise providers.ProviderError("Provider application is not configured on this deployment")
        return client

    def redirect(self) -> str:
        if not self.settings.product_redirect:
            self.fault("config", "oauth_redirect_unset")
            raise HTTPException(503, "Product OAuth callback is not configured")
        return self.settings.product_redirect

    def oauth_application(self, row: dict) -> dict:
        if not row["oauth_app_version"]:
            raise HTTPException(409, "Save the OAuth application first")
        return self.must_get("oauth-app-" + row["id"], row["oauth_app_version"])["value"]

    def refreshed(self, who, cid, row, value, operation) -> dict:
        """A fresh Google access token, rotated in the store, when the stored one is near expiry."""
        if value["expires_at"] > time.time() + 30:
            return value
        if not value.get("refresh_token"):
            raise providers.ProviderError("Authorization expired; reconnect this account")
        value = providers.refresh(row["provider"], self.google(), value)
        saved = self.put(cid, {"value": value})
        row["version"] = saved.version
        self.sql("UPDATE connections SET version=? WHERE id=?", (saved.version, cid))
        self.audit(who, cid, "credential.refresh", "stored", operation=operation, receipt_id=saved.receipt, version=saved.version)
        return value


def create_app(broker: Broker) -> FastAPI:
    app = FastAPI(title="credential-broker", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.broker = broker
    b = broker

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Validation errors must not echo credential-bearing input.
        return JSONResponse({"detail": "Invalid request fields"}, 422)

    @app.exception_handler(providers.ProviderError)
    async def provider_failure(request, exc):
        b.fault("provider", "refused", route=route_of(request.url.path))
        return JSONResponse({"detail": str(exc)}, 409)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method == "GET" and request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get(assertion.HEADER.lower(), "")
        body = await request.body()
        path = request.url.path + ("?" + request.url.query if request.url.query else "")
        try:
            if not header:
                raise assertion.AssertionRefused("missing")
            claims = assertion.verify(header, key_for=b.key_for, method=request.method, path=path,
                                      body=body, remember=b.remember)
        except assertion.AssertionRefused as exc:
            log_event("assertion_refused", level="warning",
                      fields={"kind": exc.kind, "method": request.method, "route": route_of(path)})
            return JSONResponse({"detail": "Product identity refused"}, 401)
        request.state.who = {"actor": claims["actor"], "session": claims["session"], "role": claims["role"]}
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.add_middleware(TraceMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "credential-broker", "store": b.store.name}

    @app.get("/api/connections")
    def connection_list(request: Request):
        who = b.identity(request, {"agent", "human"})
        rows = b.sql("SELECT id,provider,label,status,created,setup_request,account,setup_spec,oauth_app_version,approved_host"
                     " FROM connections WHERE actor=? AND status!='deleted' ORDER BY created DESC",
                     (who["actor"],), rows=True)
        for row in rows:
            row["setup"] = json.loads(row.pop("setup_spec") or "{}")
            row["application_configured"] = bool(row.pop("oauth_app_version"))
        return {"connections": rows}

    @app.post("/api/setup")
    def setup(request: Request, body: SetupBody):
        who = b.identity(request, {"agent", "human"})
        cid = uuid.uuid4().hex
        b.audit(who, cid, "setup.request", "requested")
        b.sql("INSERT INTO connections(id,actor,session,label,status,created,provider,setup_request) VALUES (?,?,?,?,?,?,?,?)",
              (cid, who["actor"], who["session"], body.label, "awaiting_user", time.time(), body.provider, uuid.uuid4().hex))
        return {"connection_id": cid, "status": "awaiting_user"}

    @app.post("/api/connections/{cid}/request")
    def request_setup(cid: str, request: Request):
        who = b.identity(request, {"agent", "human"})
        row = b.connection(who, cid)
        if row["status"] in {"ready", "awaiting_user"}:
            b.audit(who, cid, "setup.request", "requested")
            b.sql("UPDATE connections SET setup_request=? WHERE id=? AND actor=? AND status!='deleted'",
                  (uuid.uuid4().hex, cid, who["actor"]))
        return {"connection_id": cid, "status": row["status"]}

    @app.post("/api/connections/{cid}/prepare")
    def prepare(cid: str, request: Request, body: PreparedSetupBody):
        who = b.identity(request, {"agent", "human"})
        row = b.connection(who, cid)
        if row["provider"] != "custom_secret":
            raise HTTPException(409, "Only custom service setup can be prepared")
        previous = json.loads(row["setup_spec"] or "{}")
        if previous.get("oauth") and not body.setup.get("oauth"):
            raise HTTPException(409, "This connection uses OAuth. Preserve its OAuth definition; create a separate connection for an API token.")
        try:
            spec = connection_setup.validate(body.setup)
        except (ValueError, secret_service.ServiceError):
            raise HTTPException(422, "Invalid setup specification; supply a public HTTPS endpoint, supported authentication, and required fields") from None
        with b.lock:
            current = b.connection(who, cid)
            previous = json.loads(current["setup_spec"] or "{}")
            if previous.get("oauth") and not spec.get("oauth"):
                raise HTTPException(409, "This connection uses OAuth. Preserve its OAuth definition.")
            if previous == spec:
                return {"connection_id": cid, "status": current["status"], "setup": spec}
            b.audit(who, cid, "setup.prepare", "proposed")
            b.sql("UPDATE connections SET setup_spec=?,setup_request=?,oauth_app_version=0 WHERE id=?",
                  (json.dumps(spec), uuid.uuid4().hex, cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
        return {"connection_id": cid, "status": row["status"], "setup": spec}

    def require_confirmation(row: dict, host: str, confirmed: str) -> None:
        """M3: the first human save that sends a secret to a host must name that host back.

        A prepared setup can come from the agent, and the agent reads third-party text. The
        terminal shows the destination host as the dominant element of the form and asks the
        person to type it; the broker refuses the save without it, so a UI defect cannot skip it."""
        if not host or host == (row.get("approved_host") or ""):
            return
        if confirmed.strip().lower() != host:
            raise HTTPException(409, "Confirm the destination host before saving")

    @app.post("/api/connections/{cid}/custom-secret")
    def save_custom(cid: str, request: Request, body: CustomSecretBody):
        who = b.identity(request, {"human"})
        row = b.connection(who, cid)
        if row["provider"] != "custom_secret":
            raise HTTPException(409, "Wrong connection type")
        spec = json.loads(row["setup_spec"] or "{}")
        if spec.get("oauth"):
            raise HTTPException(409, "Use the secure OAuth application form")
        if spec:
            require_confirmation(row, destination_host(spec), body.confirmed_host)
        value = body.value
        if not value and row["status"] == "ready":
            saved_config = b.must_get(cid, row["version"])["value"]
            b.audit(who, cid, "credential.read", "retrieved", version=row["version"])
            proposed = spec or {"endpoint": body.endpoint, "header": body.header, "scheme": body.scheme, "method": body.method}
            if any(saved_config.get(k) != proposed.get(k) for k in ("endpoint", "header", "scheme", "method")):
                raise HTTPException(409, "Saved credentials cannot be reused for a different service configuration. Create a separate connection.")
            value = saved_config["value"]
        try:
            if spec:
                spec = connection_setup.validate(spec)
                required = {f["name"] for f in spec["fields"]}
                if set(body.fields) != required or any(not v.strip() or len(v) > 2000 for v in body.fields.values()):
                    raise secret_service.ServiceError("Fill all requested connection fields")
                config = secret_service.configure(value, spec["endpoint"], spec["header"], spec["scheme"], spec["method"])
                config["fixed_query"] = {f["name"]: body.fields[f["name"]] for f in spec["fields"] if f["location"] == "query"}
                config["fixed_body"] = {f["name"]: body.fields[f["name"]] for f in spec["fields"] if f["location"] == "body"}
                if spec["scheme"] == "telegram" and "chat_id" in body.fields and \
                        not re.fullmatch(r"-?[0-9]+|@[A-Za-z0-9_]{5,}", body.fields["chat_id"]):
                    raise secret_service.ServiceError("Enter the Telegram chat ID or channel username; do not use an email address")
                host = destination_host(spec)
            else:
                config = secret_service.configure(value, body.endpoint, body.header, body.scheme, body.method)
                host = (urlsplit(body.endpoint).hostname or "").lower() if body.endpoint else ""
        except secret_service.ServiceError as e:
            raise HTTPException(400, str(e)) from None
        with b.lock:
            current = b.connection(who, cid)
            if current["setup_spec"] and (body.setup_request != current["setup_request"] or current["setup_spec"] != row["setup_spec"]):
                raise HTTPException(409, "Setup changed; review the updated form and save again")
            b.audit(who, cid, "credential.store", "requested")
            saved = b.put(cid, {"value": config})
            b.audit(who, cid, "credential.store", "stored", receipt_id=saved.receipt, version=saved.version)
            b.sql("UPDATE connections SET status=?,version=?,approved_host=? WHERE id=?",
                  ("ready", saved.version, host, cid))
        return {"connection_id": cid, "status": "ready"}

    @app.post("/api/connections/{cid}/oauth-application")
    def save_oauth_application(cid: str, request: Request, body: OAuthApplicationBody):
        who = b.identity(request, {"human"})
        with b.lock:
            row = b.connection(who, cid)
            spec = json.loads(row["setup_spec"] or "{}")
            if row["provider"] != "custom_secret" or not spec.get("oauth"):
                raise HTTPException(409, "Prepare an OAuth connection first")
            if row["setup_request"] != body.setup_request:
                raise HTTPException(409, "Setup changed; review the updated form")
            try:
                spec = connection_setup.validate(spec)
            except (ValueError, secret_service.ServiceError):
                raise HTTPException(422, "Invalid OAuth definition") from None
            host = destination_host(spec)
            require_confirmation(row, host, body.confirmed_host)
            b.audit(who, cid, "oauth.application", "requested")
            saved = b.put("oauth-app-" + cid, {"value": {"client_id": body.client_id, "client_secret": body.client_secret, "spec": spec}})
            b.audit(who, cid, "oauth.application", "stored", receipt_id=saved.receipt, version=saved.version)
            b.sql("UPDATE connections SET oauth_app_version=?,status='awaiting_user',approved_host=? WHERE id=?",
                  (saved.version, host, cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
        return {"connection_id": cid, "status": "awaiting_user"}

    # Authorization starts only from the trusted human panel. The agent gets no URL carrying
    # state, code or token, and cannot complete or submit the human callback.
    @app.post("/api/connections/{cid}/authorize")
    def begin_authorize(cid: str, request: Request):
        who = b.identity(request, {"human"})
        row = b.connection(who, cid)
        custom_oauth = row["provider"] == "custom_secret" and bool(json.loads(row["setup_spec"] or "{}").get("oauth"))
        if not custom_oauth and providers.CATALOG.get(row["provider"], {}).get("method") != "oauth":
            raise HTTPException(409, "This connection uses the secure input panel")
        state = "vxc_" + secrets.token_urlsafe(32)
        _, challenge = b.pkce(state)
        if custom_oauth:
            cfg = b.oauth_application(row)
            try:
                url = service_oauth.authorize(cfg["spec"], cfg, b.redirect(), state, challenge)
            except secret_service.ServiceError as e:
                raise HTTPException(409, str(e)) from None
        else:
            url = providers.authorize(row["provider"], b.google(), b.redirect(), state, challenge)
        b.sql("DELETE FROM oauth_states WHERE connection=? OR expires<?", (cid, time.time()))
        b.sql("INSERT INTO oauth_states VALUES (?,?,?,?,?)",
              (hashlib.sha256(state.encode()).hexdigest(), who["actor"], who["session"], cid, time.time() + 600))
        b.audit(who, cid, "oauth.authorize", "requested")
        return {"authorize_url": url}

    @app.get("/api/auth/callback/google")
    def oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        who = b.identity(request, {"human"})
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        with b.lock:
            pending = b.sql("SELECT * FROM oauth_states WHERE state_hash=?", (state_hash,), one=True)
            if not pending or pending["actor"] != who["actor"] or pending["session"] != who["session"] or pending["expires"] < time.time():
                b.fault("oauth", "state_mismatch")
                raise HTTPException(403, "Authorization expired or does not belong to this session")
            b.sql("DELETE FROM oauth_states WHERE state_hash=?", (state_hash,))
        cid = pending["connection"]
        with b.lock:
            row = b.connection(who, cid)
            if error or not code:
                b.audit(who, cid, "oauth.authorize", "refused")
                return {"connection_id": cid, "status": "refused"}
            try:
                verifier, _ = b.pkce(state)
                if row["provider"] == "custom_secret":
                    cfg = b.oauth_application(row)
                    value = service_oauth.exchange(cfg["spec"], cfg, code=code, verifier=verifier, redirect=b.redirect())
                    value["oauth_application"] = cfg
                else:
                    value = providers.tokens(row["provider"], b.google(), code=code, verifier=verifier, redirect=b.redirect())
                saved = b.put(cid, {"value": value})
                account = providers.account_email(row["provider"], value)
                b.audit(who, cid, "oauth.authorize", "stored", receipt_id=saved.receipt, version=saved.version)
                b.sql("UPDATE connections SET status=?,version=?,account=? WHERE id=?", ("ready", saved.version, account, cid))
            except (providers.ProviderError, HTTPException, secret_service.ServiceError):
                b.fault("oauth", "exchange_refused", route="/api/auth/callback/google")
                b.audit(who, cid, "oauth.authorize", "refused")
                return {"connection_id": cid, "status": "refused"}
        return {"connection_id": cid, "status": "connected"}

    @app.post("/api/connections/{cid}/disconnect")
    def disconnect(cid: str, request: Request):
        who = b.identity(request, {"human"})
        with b.lock:
            b.connection(who, cid)
            b.audit(who, cid, "connection.disconnect", "requested")
            b.sql("UPDATE connections SET status=? WHERE id=?", ("disconnected", cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
            # Disables broker use immediately. Stored versions are retained under the
            # deployment's retention policy; no provider-side revoke is implied.
            b.audit(who, cid, "connection.disconnect", "disabled")
        return {"connection_id": cid, "status": "disconnected"}

    @app.post("/api/connections/{cid}/delete")
    def delete_connection(cid: str, request: Request):
        who = b.identity(request, {"human"})
        with b.lock:
            b.connection(who, cid)
            b.audit(who, cid, "connection.delete", "requested")
            b.sql("UPDATE connections SET status=? WHERE id=?", ("deleted", cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
            b.audit(who, cid, "connection.delete", "disabled_and_removed")
        return {"connection_id": cid, "status": "deleted"}

    @app.post("/api/connections/{cid}/read")
    def account_read(cid: str, request: Request, body: AccountReadBody):
        who = b.identity(request, {"agent", "human"})
        operation = uuid.uuid4().hex
        with b.lock:
            row = b.connection(who, cid)
            expected = "google_calendar" if body.action == "calendar.events" else "google_email"
            if row["provider"] != expected or row["status"] != "ready":
                raise HTTPException(409, "Matching connection is not ready")
            b.audit(who, cid, body.action, "requested", operation=operation, version=row["version"])
            try:
                value = b.must_get(cid, row["version"])["value"]
                b.audit(who, cid, "credential.read", "retrieved", operation=operation, version=row["version"])
                value = b.refreshed(who, cid, row, value, operation)
            except (providers.ProviderError, HTTPException):
                b.audit(who, cid, body.action, "failed", operation=operation, version=row["version"])
                raise
        # Token rotation is serialized; independent account reads must not hold the global lock.
        try:
            result = providers.read_account(row["provider"], value, **body.model_dump())
            b.audit(who, cid, body.action, "success", operation=operation, version=row["version"])
            return {"operation_id": operation, "source": row["provider"], "untrusted_content": True, **result}
        except (providers.ProviderError, HTTPException):
            b.audit(who, cid, body.action, "failed", operation=operation, version=row["version"])
            raise

    @app.post("/api/connections/{cid}/draft")
    def gmail_draft(cid: str, request: Request, body: GmailDraftBody):
        who = b.identity(request, {"agent", "human"})
        with b.lock:
            row = b.connection(who, cid)
            if row["provider"] != "google_email" or row["status"] != "ready":
                raise HTTPException(409, "Gmail connection is not ready")
            # Keyed digest binds a retry to exact input without storing email text.
            fingerprint = hmac.new(b.hmac_key, json.dumps(body.model_dump(), sort_keys=True).encode(), hashlib.sha256).hexdigest()
            prior = b.sql("SELECT * FROM draft_requests WHERE id=?", (body.request_id,), one=True)
            if prior:
                if prior["actor"] != who["actor"] or prior["connection"] != cid or prior["fingerprint"] != fingerprint:
                    raise HTTPException(409, "Request ID already used")
                if not prior["result"]:
                    raise HTTPException(409, "Draft outcome unknown; check Gmail before retrying")
                return json.loads(prior["result"])
            b.audit(who, cid, "gmail.draft", "requested", operation=body.request_id)
            value = b.must_get(cid, row["version"])["value"]
            b.audit(who, cid, "credential.read", "retrieved", operation=body.request_id, version=row["version"])
            if providers.DRAFT_SCOPE not in value.get("scope", "").split():
                b.sql("UPDATE connections SET setup_request=? WHERE id=?", (secrets.token_urlsafe(18), cid))
                b.audit(who, cid, "gmail.draft", "permission_required", operation=body.request_id)
                return {"status": "permission_required", "ui_action": "open_connections",
                        "instruction": "Use Enable drafts in Connections and approve the additional Google permission. No draft has been saved. Do not construct a link."}
            value = b.refreshed(who, cid, row, value, body.request_id)
            b.sql("INSERT INTO draft_requests VALUES (?,?,?,?,?)", (body.request_id, who["actor"], cid, fingerprint, ""))
            try:
                result = providers.create_gmail_draft(value, body.recipient, body.subject, body.body)
            except providers.ProviderError:
                b.audit(who, cid, "gmail.draft", "unknown", operation=body.request_id)
                raise
            b.sql("UPDATE draft_requests SET result=? WHERE id=?", (json.dumps(result), body.request_id))
            b.audit(who, cid, "gmail.draft", "success", operation=body.request_id, version=row["version"])
            return result

    @app.post("/api/connections/{cid}/call")
    def call_custom(cid: str, request: Request, body: CustomCallBody):
        who = b.identity(request, {"agent", "human"})
        row = b.connection(who, cid)
        if row["provider"] != "custom_secret" or row["status"] != "ready":
            raise HTTPException(409, "Service connection is not ready")
        operation = uuid.uuid4().hex
        b.audit(who, cid, "service.call", "requested", operation=operation, version=row["version"])
        value = b.must_get(cid, row["version"])["value"]
        b.audit(who, cid, "credential.read", "retrieved", operation=operation, version=row["version"])
        try:
            if value.get("oauth_application"):
                # Single-use refresh tokens require a fresh read under the shared lock.
                with b.lock:
                    row = b.connection(who, cid)
                    if row["status"] != "ready":
                        raise HTTPException(409, "Service connection is not ready")
                    value = b.must_get(cid, row["version"])["value"]
                    cfg = value["oauth_application"]
                    if value.get("expires_at") is not None and value["expires_at"] <= time.time() + 30:
                        if not value.get("refresh_token"):
                            raise secret_service.ServiceError("Authorization expired; reconnect")
                        value = service_oauth.exchange(cfg["spec"], cfg, refresh=value["refresh_token"])
                        value["oauth_application"] = cfg
                        saved = b.put(cid, {"value": value})
                        b.sql("UPDATE connections SET version=? WHERE id=?", (saved.version, cid))
                        b.audit(who, cid, "credential.refresh", "stored", operation=operation, receipt_id=saved.receipt, version=saved.version)
                    spec = cfg["spec"]
                    config = secret_service.configure(value["access_token"], spec["endpoint"], spec["header"], spec["scheme"], spec["method"])
                    result = secret_service.execute(config, body.parameters, body.body)
            else:
                result = secret_service.execute(value, body.parameters, body.body)
        except secret_service.ServiceError as e:
            b.fault("service", "refused", route="/api/connections/{cid}/call")
            b.audit(who, cid, "service.call", "failed", operation=operation)
            raise HTTPException(409, str(e)) from None
        b.audit(who, cid, "service.call", "complete", operation=operation)
        return {"operation_id": operation, **result}

    @app.post("/api/internal/git-secret")
    def git_secret(request: Request, body: GitSecretBody):
        """Agent-api's Git credential store. Only the git role, only names owned by its actor."""
        who = b.identity(request, {"git"})
        owner = body.name.split("/", 1)[1].removesuffix(".priv").removesuffix(".pub")
        if ".." in body.name or who["actor"] != owner:
            raise HTTPException(403, "Git credential scope refused")
        path = "git/" + body.name
        operation = uuid.uuid4().hex
        with b.lock:
            b.audit(who, body.name, "git.credential." + body.action, "requested", operation=operation)
            stored = b.get(path)
            if body.action == "put" or (body.action == "migrate" and stored is None):
                # One broker lock serializes migration with writes and revocation; CAS also
                # refuses an external writer racing the read.
                b.put(path, {"value": body.value}, cas=stored.version if stored else 0)
                stored = b.get(path)
            b.audit(who, body.name, "git.credential." + body.action, "success", operation=operation,
                    receipt_id=stored.receipt if stored else "", version=stored.version if stored else 0)
            return {"found": stored is not None, "value": stored.data.get("value") if stored else None}

    return app
