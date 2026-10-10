"""The broker's core: the state every route shares, and the checks every route runs.

`Broker` owns the metadata database and the credential store port, verifies the caller (the role
assertion's replay memory, the gateway's signed identity), writes the audit trail before the action
it records, logs faults as typed lines, and holds the OAuth helpers the consent routes use. The
routes (`routes_connections.py`, `routes_git.py`) and the app factory (`app.py`) call into it; it
knows nothing about HTTP routing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from . import assertion, identity_token, metadata, providers
from .obs import log_event
from .settings import Settings, google_client
from .store import Record, Store, StoreUnavailable

_CID = re.compile(r"/[a-f0-9]{32}(?=/|$)")


def route_of(path: str) -> str:
    """A path with connection ids folded to {cid}: low-cardinality, and safe to log."""
    return _CID.sub("/{cid}", path.split("?", 1)[0])


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
            metadata.open_schema(c)
        self.hmac_key = metadata.state_key(self.root / "state-hmac.key")

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

    def remove(self, path: str) -> None:
        """Destroy every stored version of ``path``."""
        try:
            self.store.delete(path)
        except StoreUnavailable as exc:
            self.fault("store", exc.kind)
            raise HTTPException(503, "Credential store unavailable") from None

    # ── identity ──────────────────────────────────────────────────────────────────────────
    def key_for(self, role: str) -> bytes:
        return assertion.load_key(self.settings.key_files.get(role, ""))

    def person_signed(self, header: str, actor: str) -> dict:
        """gateway-identity.v1 for an agent- or git-role call: the gateway's signature over ``actor``.

        agent-api forwards the X-Vexa-Identity it verified, unchanged. The broker verifies it with
        the gateway's PUBLIC key (read per use, like the role keys, so a rotated file takes effect
        without a restart), requires its subject to be the assertion's actor, and returns the
        verified claims. Raises AssertionRefused with a typed kind: identity_missing,
        identity_invalid (a bad signature, expired, malformed), identity_mismatch (signed for
        somebody else) or identity_key (this deployment's key file is unusable — a configuration
        fault, refused like the rest)."""
        if not header:
            raise assertion.AssertionRefused("identity_missing")
        try:
            key = identity_token.read_verify_key(self.settings.identity_public_key_file)
        except identity_token.KeyUnavailable:
            raise assertion.AssertionRefused("identity_key") from None
        try:
            claims = identity_token.verify(key, header)
        except identity_token.IdentityError:
            raise assertion.AssertionRefused("identity_invalid") from None
        if not hmac.compare_digest(claims["sub"].encode(), str(actor).encode()):
            raise assertion.AssertionRefused("identity_mismatch")
        return claims

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
        """Something failed that the person cannot fix: a dependency down, misconfigured, or
        answering unusably."""
        log_event("broker_fault", level="warning", fields={"source": source, "kind": kind, **fields})

    @staticmethod
    def refused(source: str, kind: str, route: str) -> None:
        """An upstream refused, and the answer tells the person what to do. Not a fault."""
        log_event("upstream_refused", level="warning", fields={"source": source, "kind": kind, "route": route})

    def pkce(self, state: str):
        verifier = base64.urlsafe_b64encode(hmac.new(self.hmac_key, state.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return verifier, challenge

    def google(self) -> dict:
        """The operator's Google OAuth application, from VEXA_CONNECTIONS_GOOGLE_CLIENT_ID/SECRET
        and nowhere else."""
        client = google_client(self.settings)
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
