"""The connection routes (credential-broker.v1): the agent and human roles' lifecycle and use of a
connection.

* Lifecycle — list, request setup, prepare a custom service, the human saves (a secret, an OAuth
  application), consent (authorize and the Google callback), disconnect and delete.
* Use — account reads, Gmail drafts (create, reply in a thread, update one this broker created),
  Google Calendar changes (create, update, cancel) and custom-service calls, each audited before it
  acts and held to the connection policy agent-api forwards (`action_policy`). A write (a draft, a
  calendar change) is refused to a worker without a person in the loop whatever policy it forwards;
  only an account read may run unattended, and only when the forwarded policy allows it.
* The person's own narrower connection-policy setting (`/api/preferences`).

An upstream's refusal (`ProviderError`, `ServiceError`) answers 409; an upstream that is down or
answers unusably (`faults.UpstreamFault`) answers 502/503 through the app's handler.

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
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from . import action_policy, connection_setup, providers, secret_service, service_oauth, setup_schema
from .broker import Broker, credential_hosts
from .faults import UpstreamFault
from .reasons import ReasonedError, refusal_reason
from .models import (AccountReadBody, ActionPreferences, AuthorizeBody, CalendarEventBody, CustomCallBody,
                     CustomSecretBody, GmailDraftBody, GmailDraftUpdateBody, OAuthApplicationBody, PreparedSetupBody,
                     SetupBody)


#: An unfinished setup — awaiting the person, no credential stored, no OAuth application saved —
#: that nobody has asked for in this long is hidden from Connections and is never reused.
PENDING_SETUP_TTL = 24 * 3600
#: The SQL condition for an unfinished setup. A saved credential or OAuth application never matches.
PENDING = "status='awaiting_user' AND version=0 AND oauth_app_version=0"


def _host(endpoint: str) -> str:
    try:
        return (urlsplit((endpoint or "").replace("{secret}", "x")).hostname or "").lower()
    except ValueError:
        return ""


def _normalised(spec: dict) -> dict:
    """A stored proposal in today's shape, so a key added since (``prefix``) does not read as a change."""
    if not spec:
        return {}
    try:
        return setup_schema.SetupSpec.model_validate(spec).model_dump()
    except ValidationError:
        return spec


def _same_label(a: str, b: str) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    def stale_before() -> float:
        return time.time() - PENDING_SETUP_TTL

    def live_pending(actor: str, provider: str) -> list:
        return b.sql(f"SELECT id,label,session,setup_spec FROM connections WHERE actor=? AND provider=? AND {PENDING}"
                     " AND MAX(created,touched)>=? ORDER BY created DESC", (actor, provider, stale_before()), rows=True)

    def supersede(who: dict, keep: str, spec: dict, label: str) -> None:
        """A prepared setup replaces the person's other unfinished setups for the same service: same
        destination host, same name, or — from this same session — one whose proposal was never
        accepted. Only unfinished rows match (`PENDING`): nothing stored is ever removed."""
        host = _host(spec.get("endpoint", ""))
        for row in live_pending(who["actor"], "custom_secret"):
            if row["id"] == keep:
                continue
            previous = json.loads(row["setup_spec"] or "{}")
            if (host and _host(previous.get("endpoint", "")) == host) or _same_label(row["label"], label) or \
                    (not previous and row["session"] == who["session"]):
                b.sql(f"UPDATE connections SET status='deleted' WHERE id=? AND actor=? AND {PENDING}", (row["id"], who["actor"]))
                b.sql("DELETE FROM oauth_states WHERE connection=?", (row["id"],))
                b.audit(who, row["id"], "setup.prepare", "superseded")

    @router.get("/api/connections")
    def connection_list(request: Request):
        who = b.identity(request, {"agent", "human"}, unwatched_ok=True)
        # An unfinished setup left alone past PENDING_SETUP_TTL is not listed (nothing is stored for it).
        rows = b.sql("SELECT id,provider,label,status,created,setup_request,account,setup_spec,oauth_app_version,approved_host,grants"
                     f" FROM connections WHERE actor=? AND status!='deleted' AND NOT ({PENDING} AND MAX(created,touched)<?)"
                     " ORDER BY created DESC", (who["actor"], stale_before()), rows=True)
        for row in rows:
            row["setup"] = json.loads(row.pop("setup_spec") or "{}")
            row["application_configured"] = bool(row.pop("oauth_app_version"))
            row["permissions"] = (row.pop("grants") or "").split()
        return {"connections": rows}

    @router.get("/api/preferences")
    def preferences_read(request: Request):
        # A background routine reads it too: agent-api narrows every action with it.
        who = b.identity(request, {"agent", "human"}, unwatched_ok=True)
        row = b.sql("SELECT value FROM preferences WHERE actor=?", (who["actor"],), one=True)
        return ActionPreferences.model_validate(json.loads(row["value"]) if row else {}).model_dump(exclude_none=True)

    @router.post("/api/preferences")
    def preferences_write(request: Request, body: ActionPreferences):
        who = b.identity(request, {"agent", "human"})
        if who.get("delegated"):
            # The person's own word: no worker saves it, even one whose person is in the chat.
            b.audit(who, "", "preferences.write", "refused")
            raise HTTPException(403, "Only the person can change their own setting")
        b.audit(who, "", "preferences.write", "requested")
        value = body.model_dump(exclude_none=True)
        b.sql("INSERT INTO preferences(actor,value,updated) VALUES (?,?,?) ON CONFLICT(actor) DO UPDATE SET"
              " value=excluded.value, updated=excluded.updated", (who["actor"], json.dumps(value), time.time()))
        b.audit(who, "", "preferences.write", "stored")
        return value

    @router.post("/api/setup")
    def setup(request: Request, body: SetupBody):
        who = b.identity(request, {"agent", "human"})
        with b.lock:
            # Asking again for a service whose setup is still unfinished reuses that setup.
            again = next((r for r in live_pending(who["actor"], body.provider) if _same_label(r["label"], body.label)), None)
            if again is not None:
                b.audit(who, again["id"], "setup.request", "requested")
                b.sql("UPDATE connections SET setup_request=?,touched=? WHERE id=?", (uuid.uuid4().hex, time.time(), again["id"]))
                return {"connection_id": again["id"], "status": "awaiting_user"}
            cid = uuid.uuid4().hex
            b.audit(who, cid, "setup.request", "requested")
            now = time.time()
            b.sql("INSERT INTO connections(id,actor,session,label,status,created,provider,setup_request,touched) VALUES (?,?,?,?,?,?,?,?,?)",
                  (cid, who["actor"], who["session"], body.label, "awaiting_user", now, body.provider, uuid.uuid4().hex, now))
        return {"connection_id": cid, "status": "awaiting_user"}

    @router.post("/api/connections/{cid}/request")
    def request_setup(cid: str, request: Request):
        who = b.identity(request, {"agent", "human"})
        row = b.connection(who, cid)
        if row["status"] in {"ready", "awaiting_user"}:
            b.audit(who, cid, "setup.request", "requested")
            b.sql("UPDATE connections SET setup_request=?,touched=? WHERE id=? AND actor=? AND status!='deleted'",
                  (uuid.uuid4().hex, time.time(), cid, who["actor"]))
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
            supersede(who, cid, spec, current["label"])
            if _normalised(previous) == spec:
                b.sql("UPDATE connections SET touched=? WHERE id=?", (time.time(), cid))
                return {"connection_id": cid, "status": current["status"], "setup": spec}
            b.audit(who, cid, "setup.prepare", "proposed")
            b.sql("UPDATE connections SET setup_spec=?,setup_request=?,oauth_app_version=0,touched=? WHERE id=?",
                  (json.dumps(spec), uuid.uuid4().hex, time.time(), cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
        return {"connection_id": cid, "status": row["status"], "setup": spec}

    def require_confirmation(row: dict, hosts: list, confirmed: set) -> None:
        """M3: the first human save that sends a credential to a host must name that host back —
        every such host: for OAuth both the token endpoint and the service endpoint.

        A prepared setup can come from the agent, and the agent reads third-party text. The
        terminal shows each destination host as a dominant element of the form and asks the person
        to type it; the broker refuses the save without it, so a UI defect cannot skip it.
        ``approved_host`` holds the hosts last approved, space-separated."""
        approved = set((row.get("approved_host") or "").split())
        typed = {c.strip().lower() for c in confirmed if c}
        if any(h not in approved and h not in typed for h in hosts):
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
            require_confirmation(row, credential_hosts(spec), {body.confirmed_host})
        value = body.value
        if not value and row["status"] == "ready":
            saved_config = b.must_get(cid, row["version"], owner=who["actor"])["value"]
            b.audit(who, cid, "credential.read", "retrieved", version=row["version"])
            proposed = spec or {"endpoint": body.endpoint, "header": body.header, "scheme": body.scheme, "method": body.method}
            if any((saved_config.get(k) or "") != (proposed.get(k) or "") for k in ("endpoint", "header", "scheme", "method", "prefix")):
                raise HTTPException(409, "Saved credentials cannot be reused for a different service configuration. Create a separate connection.")
            value = saved_config["value"]
        try:
            if spec:
                spec = connection_setup.validate(spec)
                required = {f["name"] for f in spec["fields"]}
                if set(body.fields) != required or any(not v.strip() or len(v) > 2000 for v in body.fields.values()):
                    raise secret_service.ServiceError("Fill all requested connection fields")
                config = secret_service.configure(value, spec["endpoint"], spec["header"], spec["scheme"], spec["method"],
                                                  spec.get("prefix", ""))
                config["fixed_query"] = {f["name"]: body.fields[f["name"]] for f in spec["fields"] if f["location"] == "query"}
                config["fixed_body"] = {f["name"]: body.fields[f["name"]] for f in spec["fields"] if f["location"] == "body"}
                if spec["scheme"] == "telegram" and "chat_id" in body.fields and \
                        not re.fullmatch(r"-?[0-9]+|@[A-Za-z0-9_]{5,}", body.fields["chat_id"]):
                    raise secret_service.ServiceError("Enter the Telegram chat ID or channel username; do not use an email address")
                host = " ".join(credential_hosts(spec))
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
            saved = b.put(cid, {"value": config}, owner=who["actor"])
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
            hosts = credential_hosts(spec)
            require_confirmation(row, hosts, {body.confirmed_host, *body.confirmed_hosts})
            b.audit(who, cid, "oauth.application", "requested")
            saved = b.put("oauth-app-" + cid, {"value": {"client_id": body.client_id, "client_secret": body.client_secret, "spec": spec}},
                          owner=who["actor"])
            b.audit(who, cid, "oauth.application", "stored", receipt_id=saved.receipt, version=saved.version)
            b.sql("UPDATE connections SET oauth_app_version=?,status='awaiting_user',approved_host=? WHERE id=?",
                  (saved.version, " ".join(hosts), cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
        return {"connection_id": cid, "status": "awaiting_user"}

    # Authorization starts only from the trusted human panel. The agent gets no URL carrying
    # state, code or token, and cannot complete or submit the human callback.
    @router.post("/api/connections/{cid}/authorize")
    def begin_authorize(cid: str, request: Request, body: Optional[AuthorizeBody] = None):
        who = b.identity(request, {"human"})
        row = b.connection(who, cid)
        grant = body.grant if body is not None else ""
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
                raise ReasonedError(409, str(e), refusal_reason(str(e))) from None
        else:
            try:
                url = providers.authorize(row["provider"], b.google(), b.redirect(), state, challenge, grant)
            except providers.ProviderError as e:
                raise HTTPException(409, str(e)) from None
        if grant and custom_oauth:
            raise HTTPException(409, "This permission does not apply to this connection")
        b.sql("DELETE FROM oauth_states WHERE connection=? OR expires<?", (cid, time.time()))
        b.sql("INSERT INTO oauth_states(state_hash,actor,session,connection,expires,grant_name) VALUES (?,?,?,?,?,?)",
              (hashlib.sha256(state.encode()).hexdigest(), who["actor"], who["session"], cid, time.time() + 600, grant))
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
                    value = providers.tokens(row["provider"], b.google(), code=code, verifier=verifier, redirect=b.redirect(),
                                             grant=pending.get("grant_name") or "")
                saved = b.put(cid, {"value": value}, owner=who["actor"])
                account = providers.account_email(row["provider"], value)
                b.audit(who, cid, "oauth.authorize", "stored", receipt_id=saved.receipt, version=saved.version)
                b.sql("UPDATE connections SET status=?,version=?,account=?,grants=? WHERE id=?",
                      ("ready", saved.version, account, " ".join(providers.permissions(row["provider"], value.get("scope", ""))), cid))
            except (providers.ProviderError, HTTPException, secret_service.ServiceError):
                b.refused("oauth", "exchange_refused", "/api/auth/callback/google")
                b.audit(who, cid, "oauth.authorize", "refused")
                return {"connection_id": cid, "status": "refused"}
            except UpstreamFault as exc:
                # The consent did not complete because the token endpoint was down: a fault, typed.
                b.fault(exc.source, exc.kind, route="/api/auth/callback/google")
                b.audit(who, cid, "oauth.authorize", "failed")
                return {"connection_id": cid, "status": "refused"}
        return {"connection_id": cid, "status": "connected"}

    @router.post("/api/connections/{cid}/disconnect")
    def disconnect(cid: str, request: Request):
        who = b.identity(request, {"human"})
        with b.lock:
            b.connection(who, cid)
            b.audit(who, cid, "connection.disconnect", "requested")
            # Every stored version of the account's credential is destroyed first; a store that
            # does not answer leaves the connection as it was (503), to be retried. An OAuth
            # application stays, so the person can reconnect. No provider-side revoke is implied.
            b.remove(cid)
            b.sql("UPDATE connections SET status=?,version=0 WHERE id=?", ("disconnected", cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
            b.audit(who, cid, "connection.disconnect", "disabled_and_credential_removed")
        return {"connection_id": cid, "status": "disconnected"}

    @router.post("/api/connections/{cid}/delete")
    def delete_connection(cid: str, request: Request):
        who = b.identity(request, {"human"})
        with b.lock:
            b.connection(who, cid)
            b.audit(who, cid, "connection.delete", "requested")
            # The credential and the OAuth application, every stored version, before the row says so.
            b.remove(cid)
            b.remove("oauth-app-" + cid)
            b.sql("UPDATE connections SET status=?,version=0,oauth_app_version=0 WHERE id=?", ("deleted", cid))
            b.sql("DELETE FROM oauth_states WHERE connection=?", (cid,))
            b.audit(who, cid, "connection.delete", "disabled_and_removed")
        return {"connection_id": cid, "status": "deleted"}

    @router.post("/api/connections/{cid}/read")
    def account_read(cid: str, request: Request, body: AccountReadBody):
        who = b.identity(request, {"agent", "human"}, unwatched_ok=True)
        action_policy.require_regime(who, action_policy.effective(body.policy), "/api/connections/{cid}/read")
        operation = uuid.uuid4().hex
        with b.lock:
            row = b.connection(who, cid)
            expected = "google_calendar" if body.action == "calendar.events" else "google_email"
            if row["provider"] != expected or row["status"] != "ready":
                raise HTTPException(409, "Matching connection is not ready")
            b.audit(who, cid, body.action, "requested", operation=operation, version=row["version"])
            try:
                value = b.must_get(cid, row["version"], owner=who["actor"])["value"]
                b.audit(who, cid, "credential.read", "retrieved", operation=operation, version=row["version"])
                value = b.refreshed(who, cid, row, value, operation)
            except (providers.ProviderError, UpstreamFault, HTTPException):
                b.audit(who, cid, body.action, "failed", operation=operation, version=row["version"])
                raise
        # Token rotation is serialized; independent account reads must not hold the global lock.
        try:
            # The connected address comes from the broker's own row (Gmail's profile answer at
            # consent), never from the body: AccountReadBody forbids extra fields.
            result = providers.read_account(row["provider"], value, account=row.get("account") or "",
                                            **body.model_dump(exclude={"policy"}))
            b.audit(who, cid, body.action, "success", operation=operation, version=row["version"])
            return {"operation_id": operation, "source": row["provider"], "untrusted_content": True, **result}
        except (providers.ProviderError, UpstreamFault, HTTPException):
            b.audit(who, cid, body.action, "failed", operation=operation, version=row["version"])
            raise

    def idempotent(who, cid, request_id, kind, body, unknown):
        """The keyed fingerprint binding a retry to exact input (never storing the text), and the
        first result when this request id already ran. Raises 409 on reuse with other input, and on
        a first attempt whose outcome is unknown."""
        content = body.model_dump(exclude={"policy"})
        fingerprint = hmac.new(b.hmac_key, (kind + ":" + json.dumps(content, sort_keys=True)).encode(),
                               hashlib.sha256).hexdigest()
        legacy = fingerprint
        plain = ("request_id", "recipient", "subject", "body")
        if kind == "gmail.draft" and body.model_dump(exclude={"policy", *plain}, exclude_defaults=True) == {}:
            # A draft request as this broker fingerprinted it before cc, threads and attachments
            # existed: its retry still answers with the first result.
            legacy = hmac.new(b.hmac_key, json.dumps({k: content[k] for k in plain}, sort_keys=True).encode(),
                              hashlib.sha256).hexdigest()
        prior = b.sql("SELECT * FROM draft_requests WHERE id=?", (request_id,), one=True)
        if prior:
            if prior["actor"] != who["actor"] or prior["connection"] != cid or prior["fingerprint"] not in (fingerprint, legacy):
                raise HTTPException(409, "Request ID already used")
            if not prior["result"]:
                raise HTTPException(409, unknown)
            return fingerprint, json.loads(prior["result"])
        return fingerprint, None

    def credential(who, cid, row, operation, action, scope, permission, instruction):
        """The stored grant, or the permission_required answer when it lacks ``scope``."""
        value = b.must_get(cid, row["version"], owner=who["actor"])["value"]
        b.audit(who, cid, "credential.read", "retrieved", operation=operation, version=row["version"])
        b.sql("UPDATE connections SET grants=? WHERE id=?",
              (" ".join(providers.permissions(row["provider"], value.get("scope", ""))), cid))
        if scope not in value.get("scope", "").split():
            b.sql("UPDATE connections SET setup_request=? WHERE id=?", (secrets.token_urlsafe(18), cid))
            b.audit(who, cid, action, "permission_required", operation=operation)
            return None, {"status": "permission_required", "ui_action": "open_connections",
                          "permission": permission, "instruction": instruction}
        return b.refreshed(who, cid, row, value, operation), None

    def draft(cid: str, request: Request, body: GmailDraftBody, *, update: bool):
        kind = "gmail.draft.update" if update else "gmail.draft"
        # A write: never for a worker without a person in the loop, whatever policy it forwards.
        who = b.identity(request, {"agent", "human"})
        policy = action_policy.effective(body.policy)
        with b.lock:
            row = b.connection(who, cid)
            if row["provider"] != "google_email" or row["status"] != "ready":
                raise HTTPException(409, "Gmail connection is not ready")
            fingerprint, prior = idempotent(who, cid, body.request_id, kind, body,
                                            "Draft outcome unknown; check Gmail before retrying")
            if prior is not None:
                return prior
            if update and not b.sql("SELECT 1 FROM drafts WHERE draft_id=? AND actor=? AND connection=?",
                                    (body.draft_id, who["actor"], cid), one=True):
                b.audit(who, cid, kind, "refused", operation=body.request_id)
                raise HTTPException(409, "Only drafts Vexa created in this mailbox can be updated")
            try:
                action_policy.require_domains([body.recipient, *body.cc, *body.bcc], policy.recipient_domains,
                                              "draft_recipient_domains")
                files = action_policy.attachments(body.attachments, policy)
            except HTTPException:
                b.audit(who, cid, kind, "refused_by_policy", operation=body.request_id)
                raise
            b.audit(who, cid, kind, "requested", operation=body.request_id)
            value, answer = credential(who, cid, row, body.request_id, kind, providers.DRAFT_SCOPE, "drafts",
                                       "Use Enable drafts in Connections and approve the additional Google permission. "
                                       "No draft has been saved. Do not construct a link.")
            if answer is not None:
                return answer
            b.sql("INSERT INTO draft_requests VALUES (?,?,?,?,?)", (body.request_id, who["actor"], cid, fingerprint, ""))
            try:
                result = providers.create_gmail_draft(
                    value, body.recipient, body.subject, body.body, cc=body.cc, bcc=body.bcc,
                    thread_id=body.thread_id, in_reply_to=body.in_reply_to, attachments=files,
                    draft_id=body.draft_id if update else "")
            except providers.ProviderError:
                # Google refused before anything was written: the request id may be used again.
                b.sql("DELETE FROM draft_requests WHERE id=? AND result=''", (body.request_id,))
                b.audit(who, cid, kind, "refused", operation=body.request_id)
                raise
            except UpstreamFault:
                b.audit(who, cid, kind, "unknown", operation=body.request_id)
                raise
            b.sql("UPDATE draft_requests SET result=? WHERE id=?", (json.dumps(result), body.request_id))
            b.sql("INSERT OR IGNORE INTO drafts(draft_id,actor,connection,created) VALUES (?,?,?,?)",
                  (result["draft_id"], who["actor"], cid, time.time()))
            b.audit(who, cid, kind, "success", operation=body.request_id, version=row["version"])
            return result

    @router.post("/api/connections/{cid}/draft")
    def gmail_draft(cid: str, request: Request, body: GmailDraftBody):
        return draft(cid, request, body, update=False)

    @router.post("/api/connections/{cid}/draft-update")
    def gmail_draft_update(cid: str, request: Request, body: GmailDraftUpdateBody):
        return draft(cid, request, body, update=True)

    @router.post("/api/connections/{cid}/calendar-event")
    def calendar_event(cid: str, request: Request, body: CalendarEventBody):
        # A write: never for a worker without a person in the loop, whatever policy it forwards.
        who = b.identity(request, {"agent", "human"})
        policy = action_policy.effective(body.policy)
        unknown = "Calendar change outcome unknown; check your calendar before retrying"
        with b.lock:
            row = b.connection(who, cid)
            if row["provider"] != "google_calendar" or row["status"] != "ready":
                raise HTTPException(409, "Google Calendar connection is not ready")
            fingerprint, prior = idempotent(who, cid, body.request_id, body.action, body, unknown)
            if prior is not None:
                return prior
            create = body.action == "calendar.event.create"
            if create == bool(body.event_id):
                raise HTTPException(422, "event_id is required to update or cancel, and not accepted to create")
            try:
                action_policy.require_calendar(body.calendar_id, policy)
                action_policy.require_notify(body.notify, policy)
                action_policy.require_domains(body.attendees or [], policy.attendee_domains, "calendar_attendee_domains")
            except HTTPException:
                b.audit(who, cid, body.action, "refused_by_policy", operation=body.request_id)
                raise
            b.audit(who, cid, body.action, "requested", operation=body.request_id)
            value, answer = credential(who, cid, row, body.request_id, body.action, providers.CALENDAR_WRITE_SCOPE,
                                       "calendar_write",
                                       "Use Enable calendar changes in Connections and approve the additional Google "
                                       "permission. Nothing was changed. Do not construct a link.")
            if answer is not None:
                return answer
            b.sql("INSERT INTO draft_requests VALUES (?,?,?,?,?)", (body.request_id, who["actor"], cid, fingerprint, ""))
            fields = body.model_dump(include={"title", "start", "end", "description", "location", "attendees"})
            try:
                result = providers.write_calendar_event(
                    value, body.action, body.calendar_id, event_id=body.event_id, fields=fields, notify=body.notify,
                    conference=body.conference, request_key=who["actor"] + ":" + body.request_id)
            except providers.ProviderError:
                b.sql("DELETE FROM draft_requests WHERE id=? AND result=''", (body.request_id,))
                b.audit(who, cid, body.action, "refused", operation=body.request_id)
                raise
            except UpstreamFault:
                b.audit(who, cid, body.action, "unknown", operation=body.request_id)
                raise
            b.sql("UPDATE draft_requests SET result=? WHERE id=?", (json.dumps(result), body.request_id))
            b.audit(who, cid, body.action, "success", operation=body.request_id, version=row["version"])
            if body.notify:
                # Mail sent in the person's name: its own row, with how many it went to.
                b.audit(who, cid, "calendar.attendees_notified", f"sent:{len(body.attendees or [])}",
                        operation=body.request_id, version=row["version"])
            return result

    @router.post("/api/connections/{cid}/call")
    def call_custom(cid: str, request: Request, body: CustomCallBody):
        who = b.identity(request, {"agent", "human"})
        row = b.connection(who, cid)
        if row["provider"] != "custom_secret" or row["status"] != "ready":
            raise HTTPException(409, "Service connection is not ready")
        operation = uuid.uuid4().hex
        b.audit(who, cid, "service.call", "requested", operation=operation, version=row["version"])
        value = b.must_get(cid, row["version"], owner=who["actor"])["value"]
        b.audit(who, cid, "credential.read", "retrieved", operation=operation, version=row["version"])
        try:
            if value.get("oauth_application"):
                # Single-use refresh tokens require a fresh read under the shared lock.
                with b.lock:
                    row = b.connection(who, cid)
                    if row["status"] != "ready":
                        raise HTTPException(409, "Service connection is not ready")
                    value = b.must_get(cid, row["version"], owner=who["actor"])["value"]
                    cfg = value["oauth_application"]
                    if value.get("expires_at") is not None and value["expires_at"] <= time.time() + 30:
                        if not value.get("refresh_token"):
                            raise secret_service.ServiceError("Authorization expired; reconnect")
                        value = service_oauth.exchange(cfg["spec"], cfg, refresh=value["refresh_token"])
                        value["oauth_application"] = cfg
                        saved = b.put(cid, {"value": value}, owner=who["actor"])
                        b.sql("UPDATE connections SET version=? WHERE id=?", (saved.version, cid))
                        b.audit(who, cid, "credential.refresh", "stored", operation=operation, receipt_id=saved.receipt, version=saved.version)
                    spec = cfg["spec"]
                    config = secret_service.configure(value["access_token"], spec["endpoint"], spec["header"], spec["scheme"], spec["method"])
                    result = secret_service.execute(config, body.parameters, body.body)
            else:
                result = secret_service.execute(value, body.parameters, body.body)
        except secret_service.ServiceError as e:
            b.refused("service", "refused", "/api/connections/{cid}/call")
            b.audit(who, cid, "service.call", "failed", operation=operation)
            raise ReasonedError(409, str(e), refusal_reason(str(e))) from None
        except UpstreamFault:
            b.audit(who, cid, "service.call", "failed", operation=operation)
            raise
        b.audit(who, cid, "service.call", "complete", operation=operation)
        return {"operation_id": operation, **result}

    return router
