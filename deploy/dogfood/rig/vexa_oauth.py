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
bound to a `redirect_uri` the client registered, exactly; registration accepts only https or
loopback redirect URIs, and the consent screen names the host the person is sent back to.

Refresh tokens expire (`REFRESH_TTL`) and ROTATE: a refresh retires the access token and refresh
token it was presented with, re-asks the instance's sign-in admission, and refuses a client other
than the one the grant was made to.

THE SWITCH IS OFF UNLESS TURNED ON. `VEXA_RIG_OAUTH_ENABLED=1` (also `true`/`yes`/`on`) opens this
surface and every other rig sign-in door (`/login`, start_onboarding, auth_link). Off, every OAuth
path answers 404, the 401 stops advertising the metadata, and tokens this server issued stop
resolving.

Endpoints are served over HTTP for the local rig. The spec requires HTTPS for anything that is not
localhost; this must sit behind TLS before it leaves the tunnel.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
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
REFRESH_TTL = 30 * 24 * 3600   # a refresh token's life; each refresh issues a new one
PENDING_TTL = 15 * 60  # seconds an authorization request waits for its emailed code
ENABLED_ENV = "VEXA_RIG_OAUTH_ENABLED"


def enabled() -> bool:
    """The operator's switch over every rig sign-in door: on only when `VEXA_RIG_OAUTH_ENABLED`
    says so. Unset, empty or anything else is off."""
    return os.environ.get(ENABLED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


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
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def _acceptable_redirect(uri) -> bool:
    """A redirect URI this server will send a person (and their authorization code) to: https to a
    named host, or plain http to this machine's loopback, with no userinfo and no fragment. A
    custom scheme or a plain-http host elsewhere carries the code where nobody can vouch for it."""
    if not isinstance(uri, str) or not uri or len(uri) > 2048 or "#" in uri:
        return False
    if any(ord(ch) <= 32 or ch == "\\" for ch in uri):
        return False
    try:
        u = urllib.parse.urlsplit(uri)
        host = u.hostname or ""
        u.port  # noqa: B018 — raises on a malformed port
    except ValueError:
        return False
    if not host or u.username is not None or u.password is not None:
        return False
    if u.scheme == "https":
        return True
    return u.scheme == "http" and host in _LOOPBACK_HOSTS


def _redirect_host(uri: str) -> str:
    """The host:port a consent screen names, so the person sees where the code goes."""
    try:
        return urllib.parse.urlsplit(uri).netloc
    except ValueError:
        return ""


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


def _dead(rec, now: float) -> bool:
    """A token record nothing can use any more: its access token and its refresh token have both
    expired. A record from before refresh tokens expired gets `REFRESH_TTL` from its access expiry."""
    if not isinstance(rec, dict):
        return True
    refresh_exp = rec.get("refresh_exp", rec.get("exp", 0) + REFRESH_TTL)
    return rec.get("exp", 0) < now and refresh_exp < now


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
  <p>If you allow it, you are sent back to <b>{redirect}</b>.</p>
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
                        redirect=html.escape(_redirect_host(p.get("redirect_uri") or "")),
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
        if not isinstance(req, dict):
            await reply(_j(400, {"error": "invalid_client_metadata"}))
            return True
        uris = req.get("redirect_uris")
        if (not isinstance(uris, list) or not 1 <= len(uris) <= 10
                or not all(_acceptable_redirect(u) for u in uris)):
            await reply(_j(400, {"error": "invalid_redirect_uri",
                                 "error_description": "redirect_uris must be https URIs, or http "
                                                      "to localhost, 127.0.0.1 or [::1]"}))
            return True
        name = req.get("client_name")
        name = name.strip()[:80] if isinstance(name, str) and name.strip() else "an MCP client"
        cid = "vexa-client-" + secrets.token_urlsafe(12)
        rec = {
            "client_id": cid,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": uris,
            "client_name": name,
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
            if redirect_uri is not None and not _acceptable_redirect(redirect_uri):
                redirect_uri = None             # a client registered before the rule existed
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
                                      _plausible_email, _redeem_email_code)
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
            if not _plausible_email(email):
                await reply(_html(400, _email_step(p, rid)))
                return True
            p["email"] = email
            pend["pending:" + rid] = p
            _save(CODES, pend)
            issued = _issue_email_code(email)
            if issued.get("refused") in ("budget", "source", "address"):
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
            presented = form.get("refresh_token") or ""
            asked_client = form.get("client_id") or ""
            now = time.time()
            taken: dict = {}

            def _take(toks):
                # Expired grants go; the one presented is RETIRED here, under the lock, whether or
                # not a new one is issued below — a refresh token is good for one refresh.
                for k in [k for k, v in toks.items() if _dead(v, now)]:
                    del toks[k]
                key = next((k for k, v in toks.items() if presented and hmac.compare_digest(
                    str(v.get("refresh") or ""), presented)), None)
                if key is not None:
                    taken["old"] = toks.pop(key)
                return toks

            rig_secrets.update(TOKENS, _take)
            old = taken.get("old")
            if (not old or old.get("refresh_exp", old.get("exp", 0) + REFRESH_TTL) < now
                    or (asked_client and old.get("client_id")
                        and asked_client != old["client_id"])):
                await reply(_j(400, {"error": "invalid_grant"}))
                return True
            from vexa_control_mcp import _signin_admitted
            if not old.get("email") or not _signin_admitted(old["email"]):
                await reply(_j(400, {"error": "invalid_grant",
                                     "error_description": "this address may no longer sign in"}))
                return True
            new = secrets.token_urlsafe(32)
            rec = {**old, "exp": now + TOKEN_TTL, "refresh": secrets.token_urlsafe(32),
                   "refresh_exp": now + REFRESH_TTL}
            rig_secrets.update(TOKENS, lambda toks: toks.update({new: rec}) or toks)
            await reply(_j(200, {"access_token": new, "token_type": "Bearer",
                                 "expires_in": TOKEN_TTL, "scope": rec.get("scope", ""),
                                 "refresh_token": rec["refresh"]}))
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
        if form.get("client_id") and form["client_id"] != c.get("client_id"):
            await reply(_j(400, {"error": "invalid_grant"}))
            return True
        verifier = form.get("code_verifier", "")
        chal = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if chal != c["code_challenge"]:
            await reply(_j(400, {"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"}))
            return True

        tok = secrets.token_urlsafe(32)
        now = time.time()
        rec = {
            "uid": c["uid"], "email": c["email"], "client_id": c.get("client_id", ""),
            # RFC 8707: bind the token to the resource it was requested for, so it cannot be
            # replayed at a different service.
            "aud": form.get("resource") or c.get("resource") or canonical,
            "scope": c.get("scope", ""),
            "exp": now + TOKEN_TTL,
            "refresh": secrets.token_urlsafe(32),
            "refresh_exp": now + REFRESH_TTL,
        }

        def _issue(toks):
            for k in [k for k, v in toks.items() if _dead(v, now)]:
                del toks[k]
            toks[tok] = rec
            return toks

        rig_secrets.update(TOKENS, _issue)
        await reply(_j(200, {"access_token": tok, "token_type": "Bearer",
                             "expires_in": TOKEN_TTL, "scope": rec["scope"],
                             "refresh_token": rec["refresh"]}))
        return True

    return False
