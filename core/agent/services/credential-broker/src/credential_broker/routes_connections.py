"""The connection routes (credential-broker.v1): the agent and human roles' lifecycle and use of a
connection.

* Lifecycle — list, request setup, prepare a custom service, the human saves (a secret, an OAuth
  application), consent (authorize and the Google callback), disconnect and delete.
* Use — account reads, Gmail drafts and custom-service calls, each audited before it acts.

No route returns a stored credential. Errors are fixed sentences and never echo input.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from . import connection_setup, providers, secret_service, service_oauth, setup_schema
from .broker import Broker, destination_host
from .models import (AccountReadBody, CustomCallBody, CustomSecretBody, GmailDraftBody, OAuthApplicationBody,
                     PreparedSetupBody, SetupBody)


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.get("/api/connections")
    def connection_list(request: Request):
        who = b.identity(request, {"agent", "human"})
        rows = b.sql("SELECT id,provider,label,status,created,setup_request,account,setup_spec,oauth_app_version,approved_host"
                     " FROM connections WHERE actor=? AND status!='deleted' ORDER BY created DESC",
                     (who["actor"],), rows=True)
        for row in rows:
            row["setup"] = json.loads(row.pop("setup_spec") or "{}")
            row["application_configured"] = bool(row.pop("oauth_app_version"))
        return {"connections": rows}

    @router.post("/api/setup")
    def setup(request: Request, body: SetupBody):
        who = b.identity(request, {"agent", "human"})
        cid = uuid.uuid4().hex
        b.audit(who, cid, "setup.request", "requested")
        b.sql("INSERT INTO connections(id,actor,session,label,status,created,provider,setup_request) VALUES (?,?,?,?,?,?,?,?)",
              (cid, who["actor"], who["session"], body.label, "awaiting_user", time.time(), body.provider, uuid.uuid4().hex))
        return {"connection_id": cid, "status": "awaiting_user"}

    @router.post("/api/connections/{cid}/request")
    def request_setup(cid: str, request: Request):
        who = b.identity(request, {"agent", "human"})
        row = b.connection(who, cid)
        if row["status"] in {"ready", "awaiting_user"}:
            b.audit(who, cid, "setup.request", "requested")
            b.sql("UPDATE connections SET setup_request=? WHERE id=? AND actor=? AND status!='deleted'",
                  (uuid.uuid4().hex, cid, who["actor"]))
        return {"connection_id": cid, "status": row["status"]}

    @router.post("/api/connections/{cid}/prepare")
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
        except ValidationError as e:
            # NAME THE FIELD. "Invalid setup" alone sent an agent guessing which of its keys was
            # wrong; the sentence names each one and never echoes a value.
            raise HTTPException(422, "Invalid setup specification: " + setup_schema.describe(e)) from None
        except (ValueError, secret_service.ServiceError) as e:
            # Both raise only fixed sentences of this package's own (connection_setup, secret_service).
            raise HTTPException(422, f"Invalid setup specification: {e}") from None
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

    @router.post("/api/connections/{cid}/custom-secret")
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

    @router.post("/api/connections/{cid}/oauth-application")
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
    @router.post("/api/connections/{cid}/authorize")
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

    @router.get("/api/auth/callback/google")
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

    @router.post("/api/connections/{cid}/disconnect")
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

    @router.post("/api/connections/{cid}/delete")
    def delete_connection(cid: str, request: Request):
        who = b.identity(request, {"human"})
        with b.lock:
            b.connection(who, cid)
            b.audit(who, cid, "connection.delete", "requested")
            b.sql("UPDATE connections SET status=? WHERE id=?", ("deleted", cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
            b.audit(who, cid, "connection.delete", "disabled_and_removed")
        return {"connection_id": cid, "status": "deleted"}

    @router.post("/api/connections/{cid}/read")
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

    @router.post("/api/connections/{cid}/draft")
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

    @router.post("/api/connections/{cid}/call")
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

    return router
