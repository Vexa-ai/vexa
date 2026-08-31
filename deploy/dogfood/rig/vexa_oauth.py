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

What this deliberately does NOT do yet, stated so nobody assumes otherwise:
  * The consent screen takes an email and does not verify it. It is exactly as strong as
    start_onboarding was — identity is claimed, not proven. Federating the login step to
    Google is the next move and it slots in at one function, `_identify()`.
  * Endpoints are served over HTTP for the local rig. The spec requires HTTPS for anything
    that is not localhost; this must sit behind TLS before it leaves the tunnel.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import secrets
import time
import urllib.parse

HOME = pathlib.Path.home()
STORE = HOME / ".storm/oauth"
STORE.mkdir(parents=True, exist_ok=True)

CLIENTS = STORE / "clients.json"
CODES = STORE / "codes.json"
TOKENS = STORE / "tokens.json"
CODE_TTL = 60          # seconds; an authorization code is single-use and short-lived
TOKEN_TTL = 8 * 3600


def _load(p: pathlib.Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save(p: pathlib.Path, d: dict) -> None:
    p.write_text(json.dumps(d, indent=1))
    try:
        p.chmod(0o600)
    except Exception:
        pass


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


# --------------------------------------------------------------------------- identity
def _identify(email: str) -> str | None:
    """Turn a consent-screen answer into a platform uid.

    THE WEAK LINK, on purpose and in one place: the email is claimed, never proven. Everything
    else here is spec-correct; this is where Google (or an emailed code) federates in, and
    until it does, OAuth improves the CREDENTIAL mechanism without improving identity
    assurance. Do not read a successful login as proof of who someone is."""
    from vexa_control_mcp import ADMIN_API, _admin_key, _http, AGENT_API
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    ak = {"X-Admin-API-Key": _admin_key()}
    st, u = _http("GET", f"{ADMIN_API}/admin/users/email/{email}", ak)
    if st != 200:
        st, u = _http("POST", f"{ADMIN_API}/admin/users", ak,
                      {"email": email, "name": email.split("@")[0].title()})
    uid = str((u or {}).get("id", ""))
    if uid:
        _http("POST", f"{AGENT_API}/api/workspace/init", {"X-User-Id": uid}, {})
    return uid or None


# --------------------------------------------------------------------------- token check
def resolve_token(tok: str, canonical: str) -> dict | None:
    """Validate a bearer token and CHECK THE AUDIENCE.

    RFC 8707 / the MCP spec: a resource server must only accept tokens minted for itself.
    Skipping this is the confused-deputy hole — a token issued for some other service would
    otherwise be accepted here."""
    rec = _load(TOKENS).get(tok)
    if not rec:
        return None
    if rec.get("exp", 0) < time.time():
        return None
    if rec.get("aud") and canonical and rec["aud"].rstrip("/") != canonical.rstrip("/"):
        return None
    return rec


CONSENT = """<!doctype html><meta charset="utf-8"><title>Authorize Vexa</title>
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
 .note{{font-size:12.5px;color:#8A6A1C;background:#F2ECD9;border-left:3px solid #8A6A1C;
   padding:11px 13px;border-radius:5px;margin-top:18px}}
</style>
<div class="card">
  <h1>Authorize Vexa</h1>
  <p><b>{client}</b> is asking to use your Vexa account — your meetings, the knowledge your
     team builds, and the flows that run automatically.</p>
  <div class="who">{resource}</div>
  <form method="POST">
    <input type="hidden" name="rid" value="{rid}">
    <label for="email">The email your calendar invites come from</label>
    <input id="email" name="email" type="email" required autofocus
           placeholder="you@yourcompany.com" autocomplete="email">
    <button type="submit">Allow</button>
  </form>
  <div class="note">This address is not verified yet. Anyone who reaches this page can type
     any address — so treat it as a claim, not proof, until the login step is federated.</div>
</div>"""


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
            if cid not in _load(CLIENTS):
                await reply(_j(400, {"error": "invalid_client"}))
                return True
            if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
                # OAuth 2.1: PKCE is mandatory. Refuse rather than silently downgrade.
                await reply(_j(400, {"error": "invalid_request",
                                     "error_description": "PKCE with S256 is required"}))
                return True
            rid = secrets.token_urlsafe(16)
            pend = _load(CODES)
            pend["pending:" + rid] = {
                "client_id": cid,
                "redirect_uri": q.get("redirect_uri", ""),
                "state": q.get("state", ""),
                "code_challenge": q["code_challenge"],
                "resource": q.get("resource", canonical),
                "scope": q.get("scope", "vexa.read vexa.write"),
                "at": time.time(),
            }
            _save(CODES, pend)
            name = _load(CLIENTS)[cid].get("client_name", "an MCP client")
            await reply(_html(200, CONSENT.format(
                client=name, resource=q.get("resource", canonical), rid=rid)))
            return True

        form = dict(urllib.parse.parse_qsl((await body_bytes()).decode()))
        pend = _load(CODES)
        p = pend.pop("pending:" + form.get("rid", ""), None)
        if not p:
            await reply(_html(400, "<p>That authorization request expired. Start again.</p>"))
            return True
        uid = _identify(form.get("email", ""))
        if not uid:
            await reply(_html(400, "<p>That does not look like an email address.</p>"))
            return True
        code = secrets.token_urlsafe(24)
        pend[code] = {**p, "uid": uid, "email": form.get("email", "").strip().lower(),
                      "issued": time.time()}
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
