"""OAuth 2.1 for the Vexa MCP server — resource metadata, an authorization server, and DCR.

Built because there is no other way. An MCP client sets its Authorization header when the
server is configured; only the client can change it. So a token minted mid-conversation is
useless until a human edits config and restarts — which is what every onboarding attempt hit
today. OAuth is the one mechanism where the CLIENT acquires its own credential.

Implements the parts the MCP authorization spec (2025-06-18) makes mandatory:

  RFC 9728  protected resource metadata     — MUST, and MUST be pointed at from a 401
  RFC 8414  authorization server metadata   — MUST be provided by the AS
  RFC 7591  dynamic client registration     — SHOULD; without it every client is pre-arranged
  OAuth 2.1 authorization code + PKCE       — PKCE is MUST
  RFC 8707  resource indicators             — MUST be sent, and the RS MUST check the audience

Identity on the consent screen is PROVEN, the same way every other rig door proves it: the
person types the address, a 6-digit code is mailed to it (`_issue_email_code`), and only the code
coming back issues anything (`_redeem_email_code`). The account is then found or created only for
an address the instance's sign-in admission admits (`_account_for`). The authorization request is
bound to a `redirect_uri` the client registered, exactly.

`VEXA_RIG_OAUTH_ENABLED=0` (also `false`/`no`/`off`) turns the whole surface off without a code
change: every OAuth path answers 404, the 401 stops advertising the metadata, and tokens this
server issued stop resolving. Unset, it is on.

Endpoints are served over HTTP for the local rig. The spec requires HTTPS for anything that is not
localhost; this must sit behind TLS before it leaves the tunnel.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import time
import urllib.parse

import rig_secrets

# THESE ARE STORE NAMES, NOT PATHS (R-D05). `tokens.json` held live bearer tokens and `codes.json`
# live authorization codes, both written as plaintext JSON — the chmod below happened AFTER the
# write, and the directory itself was left at the default mode. They now go through the same sealed
# store the rest of the rig uses: encrypt-then-MAC, 0600 in a 0700 directory, locked
# read-modify-write, with any plaintext file an older rig left behind migrated on first read.
CLIENTS = "oauth/clients"
CODES = "oauth/codes"
TOKENS = "oauth/tokens"
CODE_TTL = 60          # seconds; an authorization code is single-use and short-lived
TOKEN_TTL = 8 * 3600
PENDING_TTL = 15 * 60  # seconds an authorization request waits for its emailed code
ENABLED_ENV = "VEXA_RIG_OAUTH_ENABLED"


def enabled() -> bool:
    """The operator's kill switch: the OAuth surface is on unless `VEXA_RIG_OAUTH_ENABLED` says off."""
    return os.environ.get(ENABLED_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def _load(name: str) -> dict:
    return rig_secrets.read(name)


def _save(name: str, d: dict) -> None:
    rig_secrets.write(name, d)


def _j(status: int, obj, extra_headers=None):
    body = json.dumps(obj).encode()
    hdrs = [(b"content-type", b"application/json"),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode())]
    for k, v in (extra_headers or []):
        hdrs.append((k, v))
    return status, hdrs, body


def _html(status: int, markup: str):
    body = markup.encode()
    return status, [(b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode())], body


# --------------------------------------------------------------------------- redirect uri
def _registered_redirect(client: dict, requested: str) -> str | None:
    """The redirect URI this request may use: `requested` only when it EXACTLY matches one the
    client registered; with none requested, the client's single registered URI. Otherwise None."""
    registered = [u for u in (client.get("redirect_uris") or []) if isinstance(u, str) and u]
    if requested:
        return requested if requested in registered else None
    return registered[0] if len(registered) == 1 else None


# --------------------------------------------------------------------------- token check
def resolve_token(tok: str, canonical: str) -> dict | None:
    """Validate a bearer token and CHECK THE AUDIENCE.

    RFC 8707 / the MCP spec: a resource server must only accept tokens minted for itself.
    Skipping this is the confused-deputy hole — a token issued for some other service would
    otherwise be accepted here. Nothing resolves while the surface is switched off."""
    if not enabled():
        return None
    rec = _load(TOKENS).get(tok)
    if not rec:
        return None
    if rec.get("exp", 0) < time.time():
        return None
    if rec.get("aud") and canonical and rec["aud"].rstrip("/") != canonical.rstrip("/"):
        return None
    return rec


_PAGE = """<!doctype html><meta charset="utf-8"><title>Authorize Vexa</title>
<style>
 body{{margin:0;background:#F5F6F2;color:#141716;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
   display:flex;align-items:center;justify-content:center;min-height:100vh}}
 .card{{background:#fff;border:1px solid #DADED3;border-radius:10px;padding:34px 38px;max-width:460px;
   box-shadow:0 1px 2px rgba(20,23,22,.05),0 14px 40px -26px rgba(20,23,22,.3)}}
 h1{{font-size:1.35rem;margin:0 0 6px;font-weight:600}}
 p{{color:#474E4A;font-size:14.5px;margin:0 0 18px}}
 .who{{font-family:ui-monospace,monospace;font-size:12.5px;color:#7A8179;background:#EAEDE5;
   padding:9px 12px;border-radius:6px;margin-bottom:18px;word-break:break-all}}
 label{{display:block;font-size:13px;color:#474E4A;margin-bottom:6px}}
 input{{width:100%;font:inherit;font-size:15px;padding:11px 13px;border:1px solid #C2C8B9;
   border-radius:6px;background:#fff;color:#141716;box-sizing:border-box}}
 button{{width:100%;margin-top:16px;font:inherit;font-size:15px;font-weight:500;padding:12px;
   border:none;border-radius:6px;background:#1E5E4A;color:#F5F6F2;cursor:pointer}}
</style>
<div class="card">
  <h1>Authorize Vexa</h1>
  <p><b>{client}</b> is asking to use your Vexa account — your meetings, the knowledge your
     team builds, and the flows that run automatically.</p>
  <div class="who">{resource}</div>
  {body}
</div>"""

_EMAIL_STEP = """<form method="POST">
    <input type="hidden" name="rid" value="{rid}">
    <label for="email">The email your calendar invites come from</label>
    <input id="email" name="email" type="email" required autofocus
           placeholder="you@yourcompany.com" autocomplete="email">
    <button type="submit">Send me a code</button>
  </form>"""

_CODE_STEP = """{note}<p>A 6-digit code is on its way to <b>{email}</b>.</p>
  <form method="POST">
    <input type="hidden" name="rid" value="{rid}">
    <label for="code">The 6-digit code from that email</label>
    <input id="code" name="code" inputmode="numeric" required autofocus autocomplete="one-time-code">
    <button type="submit">Allow</button>
  </form>"""


def _consent(p: dict, body: str):
    """One consent page. Every value that came from a client or a person is escaped."""
    return _PAGE.format(client=html.escape(p.get("client_name") or "an MCP client"),
                        resource=html.escape(p.get("resource") or ""), body=body)


def _email_step(p: dict, rid: str) -> str:
    return _consent(p, _EMAIL_STEP.format(rid=html.escape(rid)))


def _code_step(p: dict, rid: str, note: str = "") -> str:
    return _consent(p, _CODE_STEP.format(rid=html.escape(rid), email=html.escape(p.get("email", "")),
                                              note=f"<p>{html.escape(note)}</p>" if note else ""))


def _message(p: dict, text: str) -> str:
    return _consent(p, f"<p>{html.escape(text)}</p>")


async def handle(scope, receive, send, canonical: str) -> bool:
    """Serve the OAuth surface. Returns True if this request was ours."""
    path = scope.get("path", "")
    method = scope.get("method", "GET")
    oauth_paths = ("/.well-known/oauth-protected-resource",
                   "/.well-known/oauth-authorization-server",
                   "/oauth/register", "/oauth/authorize", "/oauth/token")
    if not any(path == p or path.startswith(p) for p in oauth_paths):
        return False

    base = canonical.rsplit("/mcp", 1)[0] or canonical

    async def reply(triple):
        status, hdrs, body = triple
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    if not enabled():
        await reply(_j(404, {"error": "not_found"}))
        return True

    # ---- RFC 9728: which authorization server protects this resource
    if path.startswith("/.well-known/oauth-protected-resource"):
        await reply(_j(200, {
            "resource": canonical,
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["vexa.read", "vexa.write"],
            "resource_documentation": "https://docs.vexa.ai",
        }))
        return True

    # ---- RFC 8414: what the authorization server can do
    if path.startswith("/.well-known/oauth-authorization-server"):
        await reply(_j(200, {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["vexa.read", "vexa.write"],
        }))
        return True

    async def body_bytes():
        buf = b""
        while True:
            msg = await receive()
            buf += msg.get("body", b"")
            if not msg.get("more_body"):
                return buf

    # ---- RFC 7591: a client registers itself, no pre-arrangement
    if path.startswith("/oauth/register"):
        if method != "POST":
            await reply(_j(405, {"error": "invalid_request"}))
            return True
        try:
            req = json.loads(await body_bytes() or b"{}")
        except Exception:
            await reply(_j(400, {"error": "invalid_client_metadata"}))
            return True
        cid = "vexa-client-" + secrets.token_urlsafe(12)
        rec = {
            "client_id": cid,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": req.get("redirect_uris") or [],
            "client_name": req.get("client_name") or "an MCP client",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",   # public client; PKCE is the protection
        }
        d = _load(CLIENTS)
        d[cid] = rec
        _save(CLIENTS, d)
        await reply(_j(201, rec))
        return True

    # ---- authorization endpoint
    if path.startswith("/oauth/authorize"):
        q = dict(urllib.parse.parse_qsl(scope.get("query_string", b"").decode()))
        if method == "GET":
            cid = q.get("client_id", "")
            client = _load(CLIENTS).get(cid)
            if client is None:
                await reply(_j(400, {"error": "invalid_client"}))
                return True
            redirect_uri = _registered_redirect(client, q.get("redirect_uri", ""))
            if redirect_uri is None:
                # Never redirect to an address the client did not register: answer here instead.
                await reply(_j(400, {"error": "invalid_request",
                                     "error_description": "redirect_uri must exactly match a "
                                                          "registered redirect URI"}))
                return True
            if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
                # OAuth 2.1: PKCE is mandatory. Refuse rather than silently downgrade.
                await reply(_j(400, {"error": "invalid_request",
                                     "error_description": "PKCE with S256 is required"}))
                return True
            rid = secrets.token_urlsafe(16)
            p = {
                "client_id": cid,
                "client_name": client.get("client_name", "an MCP client"),
                "redirect_uri": redirect_uri,
                "state": q.get("state", ""),
                "code_challenge": q["code_challenge"],
                "resource": q.get("resource", canonical),
                "scope": q.get("scope", "vexa.read vexa.write"),
                "at": time.time(),
            }
            pend = _load(CODES)
            pend["pending:" + rid] = p
            _save(CODES, pend)
            await reply(_html(200, _email_step(p, rid)))
            return True

        # POST — two steps on one pending request: the address (a code is mailed to it), then the
        # code. The address is bound to the request at the first step; the second reads it from
        # there, never from the form, so a code proves the address the request was made for.
        from vexa_control_mcp import (NOT_ADMITTED, _account_for, _issue_email_code,
                                      _redeem_email_code)
        form = dict(urllib.parse.parse_qsl((await body_bytes()).decode()))
        rid = form.get("rid", "")
        pend = _load(CODES)
        p = pend.get("pending:" + rid)
        if not p or time.time() - p.get("at", 0) > PENDING_TTL:
            pend.pop("pending:" + rid, None)
            _save(CODES, pend)
            await reply(_html(400, "<p>That authorization request expired. Start again.</p>"))
            return True

        if "code" not in form:
            email = (form.get("email") or "").strip().lower()
            if "@" not in email or email.startswith("@") or email.endswith("@"):
                await reply(_html(400, _email_step(p, rid)))
                return True
            p["email"] = email
            pend["pending:" + rid] = p
            _save(CODES, pend)
            issued = _issue_email_code(email)
            if issued.get("refused") == "budget":
                await reply(_html(429, _message(p, "Too many sign-in codes were sent just now. "
                                                        "Wait a minute and start again.")))
                return True
            if issued.get("refused") == "mail":
                await reply(_html(502, _message(p, "We could not send the code. Try again in "
                                                        "a minute.")))
                return True
            # "already-sent": a code from the last few minutes is still in that inbox — ask for it.
            await reply(_html(200, _code_step(p, rid)))
            return True

        email = p.get("email", "")
        if not email:
            await reply(_html(400, _email_step(p, rid)))
            return True
        checked = _redeem_email_code(email, form.get("code", ""))
        if checked.get("error") == "wrong":
            await reply(_html(400, _code_step(p, rid, "Wrong code — check the email again.")))
            return True
        if not checked.get("ok"):
            pend.pop("pending:" + rid, None)
            _save(CODES, pend)
            await reply(_html(400, "<p>That code expired. Start again.</p>"))
            return True

        uid, why = _account_for(email)
        if not uid:
            pend.pop("pending:" + rid, None)
            _save(CODES, pend)
            if why == NOT_ADMITTED:
                await reply(_html(403, _message(p, "This address can't sign in here. Ask the "
                                                        "person who runs this Vexa to add it.")))
            else:
                await reply(_html(502, _message(p, "Something broke on our side. Try again.")))
            return True

        pend.pop("pending:" + rid, None)
        code = secrets.token_urlsafe(24)
        pend[code] = {**p, "uid": uid, "email": email, "issued": time.time()}
        _save(CODES, pend)
        sep = "&" if "?" in p["redirect_uri"] else "?"
        loc = f'{p["redirect_uri"]}{sep}code={urllib.parse.quote(code)}'
        if p.get("state"):
            loc += "&state=" + urllib.parse.quote(p["state"])
        await reply((302, [(b"location", loc.encode()),
                           (b"content-length", b"0")], b""))
        return True

    # ---- token endpoint
    if path.startswith("/oauth/token"):
        form = dict(urllib.parse.parse_qsl((await body_bytes()).decode()))
        codes = _load(CODES)
        gt = form.get("grant_type")

        if gt == "refresh_token":
            toks = _load(TOKENS)
            old = next((v for k, v in toks.items()
                        if v.get("refresh") == form.get("refresh_token")), None)
            if not old:
                await reply(_j(400, {"error": "invalid_grant"}))
                return True
            new = secrets.token_urlsafe(32)
            toks[new] = {**old, "exp": time.time() + TOKEN_TTL,
                         "refresh": secrets.token_urlsafe(32)}
            _save(TOKENS, toks)
            await reply(_j(200, {"access_token": new, "token_type": "Bearer",
                                 "expires_in": TOKEN_TTL,
                                 "refresh_token": toks[new]["refresh"]}))
            return True

        c = codes.pop(form.get("code", ""), None)
        _save(CODES, codes)                      # single use, whatever happens next
        if not c:
            await reply(_j(400, {"error": "invalid_grant"}))
            return True
        if time.time() - c["issued"] > CODE_TTL:
            await reply(_j(400, {"error": "invalid_grant",
                                 "error_description": "code expired"}))
            return True
        verifier = form.get("code_verifier", "")
        chal = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if chal != c["code_challenge"]:
            await reply(_j(400, {"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"}))
            return True

        tok = secrets.token_urlsafe(32)
        toks = _load(TOKENS)
        toks[tok] = {
            "uid": c["uid"], "email": c["email"],
            # RFC 8707: bind the token to the resource it was requested for, so it cannot be
            # replayed at a different service.
            "aud": form.get("resource") or c.get("resource") or canonical,
            "scope": c.get("scope", ""),
            "exp": time.time() + TOKEN_TTL,
            "refresh": secrets.token_urlsafe(32),
        }
        _save(TOKENS, toks)
        await reply(_j(200, {"access_token": tok, "token_type": "Bearer",
                             "expires_in": TOKEN_TTL, "scope": toks[tok]["scope"],
                             "refresh_token": toks[tok]["refresh"]}))
        return True

    return False
