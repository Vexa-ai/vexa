"""The rig's OAuth consent screen issues a token only for an address its holder proved, and only to
the client's registered redirect URI, and only for an address the instance admits.

The proof is the same emailed 6-digit code every other rig door takes. The switch
`VEXA_RIG_OAUTH_ENABLED` is off unless set to 1, and off turns the whole surface off.

Offline: the ASGI handler is driven directly; admin-api is the recording HTTP stand-in and mail is
captured.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import urllib.parse

import pytest

import vexa_control_mcp as rig
import vexa_oauth
from conftest import as_user, tool

CANON = "https://rig.example/mcp"
REDIRECT = "http://localhost:9999/callback"
VERIFIER = "a-verifier-that-is-long-enough-for-pkce-0123456789"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
ADMITTED = (200, {"admitted": True, "why": "existing-user"})


def call(method, path, *, query="", form=None, body=None):
    payload = b""
    if form is not None:
        payload = urllib.parse.urlencode(form).encode()
    if body is not None:
        payload = json.dumps(body).encode()
    msgs: list = []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(m):
        msgs.append(m)

    scope = {"type": "http", "path": path, "method": method, "query_string": query.encode()}
    handled = asyncio.run(vexa_oauth.handle(scope, receive, send, CANON))
    start = next((m for m in msgs if m["type"] == "http.response.start"), {})
    out = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body")
    return handled, start.get("status"), dict(start.get("headers", [])), out.decode()


@pytest.fixture
def mail(monkeypatch):
    sent: list = []
    monkeypatch.setattr(rig, "_send_code", lambda email, code: sent.append((email, code)) or None)
    monkeypatch.setenv(vexa_oauth.ENABLED_ENV, "1")
    for store in (vexa_oauth.CLIENTS, vexa_oauth.CODES, vexa_oauth.TOKENS, rig.EMAIL_CODES_STORE):
        rig.rig_secrets.write(store, {})
    rig._CODE_SENDS.clear()
    return sent


def _http(monkeypatch, admission=ADMITTED):
    http = as_user(monkeypatch, "7", routes={
        "/internal/signin-admission": admission,
        "/admin/users/email/": (200, {"id": 42}),
        "/admin/users": (200, {"id": 42})})
    rig.CURRENT.set(None)
    return http


def _register(redirect_uris=(REDIRECT,), name="Test client") -> str:
    _, status, _, out = call("POST", "/oauth/register",
                             body={"redirect_uris": list(redirect_uris), "client_name": name})
    assert status == 201
    return json.loads(out)["client_id"]


def _authorize(cid, redirect_uri=REDIRECT, state="st-1"):
    q = {"client_id": cid, "code_challenge": CHALLENGE, "code_challenge_method": "S256",
         "state": state, "response_type": "code"}
    if redirect_uri is not None:
        q["redirect_uri"] = redirect_uri
    return call("GET", "/oauth/authorize", query=urllib.parse.urlencode(q))


def _rid(page: str) -> str:
    return re.search(r'name="rid" value="([^"]+)"', page).group(1)


def _start(cid) -> str:
    _, status, _, page = _authorize(cid)
    assert status == 200
    return _rid(page)


# ── the proof ────────────────────────────────────────────────────────────────────────────────────

def test_a_typed_address_alone_issues_nothing(monkeypatch, mail):
    http = _http(monkeypatch)
    rid = _start(_register())
    _, status, headers, page = call("POST", "/oauth/authorize", form={"rid": rid, "email": "ana@example.com"})
    assert status == 200 and b"location" not in headers
    assert "6-digit code" in page
    assert [e for e, _ in mail] == ["ana@example.com"]
    assert not [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/admin/users")]


def test_the_emailed_code_issues_a_token_for_that_address(monkeypatch, mail):
    _http(monkeypatch)
    rid = _start(_register())
    call("POST", "/oauth/authorize", form={"rid": rid, "email": "Ana@Example.com"})
    code = mail[-1][1]
    _, status, headers, _ = call("POST", "/oauth/authorize", form={"rid": rid, "code": code})
    assert status == 302
    location = headers[b"location"].decode()
    assert location.startswith(REDIRECT + "?code=") and location.endswith("&state=st-1")

    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
    _, status, _, out = call("POST", "/oauth/token", form={
        "grant_type": "authorization_code", "code": auth_code, "code_verifier": VERIFIER})
    assert status == 200
    token = json.loads(out)["access_token"]
    rec = vexa_oauth.resolve_token(token, CANON)
    assert rec["uid"] == "42" and rec["email"] == "ana@example.com"


def test_a_wrong_code_issues_nothing_and_five_end_the_request(monkeypatch, mail):
    _http(monkeypatch)
    rid = _start(_register())
    call("POST", "/oauth/authorize", form={"rid": rid, "email": "ana@example.com"})
    right = mail[-1][1]
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(5):
        _, status, headers, _ = call("POST", "/oauth/authorize", form={"rid": rid, "code": wrong})
        assert status == 400 and b"location" not in headers
    _, status, headers, _ = call("POST", "/oauth/authorize", form={"rid": rid, "code": right})
    assert status == 400 and b"location" not in headers


def test_a_code_proves_only_the_address_its_request_was_bound_to(monkeypatch, mail):
    """The second step reads the address from the request, never from the form."""
    _http(monkeypatch)
    cid = _register()
    mine, theirs = _start(cid), _start(cid)
    call("POST", "/oauth/authorize", form={"rid": mine, "email": "me@example.com"})
    my_code = mail[-1][1]
    call("POST", "/oauth/authorize", form={"rid": theirs, "email": "them@example.com"})

    _, status, headers, _ = call("POST", "/oauth/authorize",
                                 form={"rid": theirs, "code": my_code, "email": "me@example.com"})
    assert b"location" not in headers

    _, status, headers, _ = call("POST", "/oauth/authorize",
                                 form={"rid": mine, "code": my_code, "email": "them@example.com"})
    assert status == 302
    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(headers[b"location"].decode()).query)["code"][0]
    assert rig.rig_secrets.read(vexa_oauth.CODES)[auth_code]["email"] == "me@example.com"


def test_a_stale_request_expires(monkeypatch, mail):
    _http(monkeypatch)
    rid = _start(_register())
    pend = rig.rig_secrets.read(vexa_oauth.CODES)
    pend["pending:" + rid]["at"] = time.time() - vexa_oauth.PENDING_TTL - 1
    rig.rig_secrets.write(vexa_oauth.CODES, pend)
    _, status, _, page = call("POST", "/oauth/authorize", form={"rid": rid, "email": "ana@example.com"})
    assert status == 400 and "expired" in page and mail == []


# ── admission ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer", [
    (200, {"admitted": False, "why": "not-allowed"}),
    (200, {"admitted": True, "why": "unclaimed-instance"}),
    (503, {}),
], ids=["not-allowed", "unclaimed-instance", "admin-api-down"])
def test_an_address_the_instance_does_not_admit_gets_no_account_and_no_code(monkeypatch, mail, answer):
    http = _http(monkeypatch, admission=answer)
    rid = _start(_register())
    call("POST", "/oauth/authorize", form={"rid": rid, "email": "stranger@example.com"})
    _, status, headers, _ = call("POST", "/oauth/authorize", form={"rid": rid, "code": mail[-1][1]})
    assert status == 403 and b"location" not in headers
    assert not [c for c in http.calls if c["url"].endswith("/admin/users") or "/admin/users/email/" in c["url"]]
    admission = [c for c in http.calls if c["url"].endswith("/internal/signin-admission")]
    assert admission and admission[0]["headers"].get("X-Internal-Secret")


def test_the_other_rig_doors_ask_admission_too(monkeypatch, mail):
    http = _http(monkeypatch, admission=(200, {"admitted": False, "why": "not-allowed"}))
    tool("start_onboarding")("stranger@example.com")
    out = json.loads(tool("confirm_login")("stranger@example.com", mail[-1][1]))
    assert "may not sign in" in out["error"] and "token" not in out
    assert not [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/admin/users")]


# ── the redirect URI ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asked", ["http://localhost:9999/other", "http://evil.example/callback",
                                   REDIRECT + "/", REDIRECT + "?x=1"])
def test_an_unregistered_redirect_uri_is_refused_without_a_redirect(monkeypatch, mail, asked):
    _http(monkeypatch)
    _, status, headers, _ = _authorize(_register(), redirect_uri=asked)
    assert status == 400 and b"location" not in headers
    assert not [k for k in rig.rig_secrets.read(vexa_oauth.CODES) if k.startswith("pending:")]


def test_an_omitted_redirect_uri_means_the_single_registered_one(monkeypatch, mail):
    _http(monkeypatch)
    _, status, _, _ = _authorize(_register(), redirect_uri=None)
    assert status == 200
    _, status, _, _ = _authorize(_register((REDIRECT, "http://localhost:1/b")), redirect_uri=None)
    assert status == 400


def test_client_supplied_text_is_escaped_on_the_consent_page(monkeypatch, mail):
    _http(monkeypatch)
    cid = _register(name="<script>alert(1)</script>")
    q = {"client_id": cid, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE,
         "code_challenge_method": "S256", "resource": '"><img src=x onerror=alert(1)>'}
    _, status, _, page = call("GET", "/oauth/authorize", query=urllib.parse.urlencode(q))
    assert status == 200
    assert "<script>" not in page and "<img" not in page


# ── the kill switch ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["0", "false", "off", "no", "", "maybe"])
def test_the_kill_switch_turns_the_whole_surface_off(monkeypatch, mail, value):
    _http(monkeypatch)
    rig.rig_secrets.write(vexa_oauth.TOKENS, {"tok-1": {"uid": "42", "exp": time.time() + 60}})
    assert vexa_oauth.resolve_token("tok-1", CANON)["uid"] == "42"

    monkeypatch.setenv(vexa_oauth.ENABLED_ENV, value)
    for method, path in (("GET", "/.well-known/oauth-protected-resource"),
                         ("GET", "/.well-known/oauth-authorization-server"),
                         ("POST", "/oauth/register"), ("GET", "/oauth/authorize"),
                         ("POST", "/oauth/authorize"), ("POST", "/oauth/token")):
        handled, status, _, _ = call(method, path, body={} if method == "POST" else None)
        assert handled and status == 404, path
    assert vexa_oauth.resolve_token("tok-1", CANON) is None


def test_the_surface_is_off_when_the_switch_is_unset(monkeypatch, mail):
    monkeypatch.delenv(vexa_oauth.ENABLED_ENV, raising=False)
    assert not vexa_oauth.enabled()
    handled, status, _, _ = call("GET", "/.well-known/oauth-authorization-server")
    assert handled and status == 404
    for value in ("1", "true", "on", "yes"):
        monkeypatch.setenv(vexa_oauth.ENABLED_ENV, value)
        assert vexa_oauth.enabled(), value


# ── what a client may register, and what the person is shown ──────────────────────────────────────

@pytest.mark.parametrize("uris", [
    ["http://evil.example/callback"], ["cursor://anysphere/oauth/callback"],
    ["https://user:pw@claude.ai/cb"], ["https://claude.ai/cb#frag"], ["javascript:alert(1)"],
    ["https:///nohost"], [], "http://localhost:9999/callback", [REDIRECT, "http://10.0.0.5/cb"],
], ids=["plain-http-elsewhere", "custom-scheme", "userinfo", "fragment", "javascript",
        "no-host", "none", "not-a-list", "one-bad-of-two"])
def test_registration_takes_only_https_or_loopback_redirects(monkeypatch, mail, uris):
    _, status, _, out = call("POST", "/oauth/register", body={"redirect_uris": uris})
    assert status == 400 and json.loads(out)["error"] == "invalid_redirect_uri"
    assert rig.rig_secrets.read(vexa_oauth.CLIENTS) == {}


@pytest.mark.parametrize("uri", ["https://claude.ai/api/mcp/auth_callback",
                                 "http://localhost:33418/callback", "http://127.0.0.1:5/cb",
                                 "http://[::1]:5/cb"])
def test_https_and_loopback_redirects_register(monkeypatch, mail, uri):
    _, status, _, _ = call("POST", "/oauth/register", body={"redirect_uris": [uri]})
    assert status == 201


def test_the_consent_screen_names_where_the_code_goes(monkeypatch, mail):
    _http(monkeypatch)
    cid = _register(("https://claude.ai/api/mcp/auth_callback",), name="Claude")
    _, status, _, page = _authorize(cid, redirect_uri="https://claude.ai/api/mcp/auth_callback")
    assert status == 200 and "sent back to <b>claude.ai</b>" in page


def test_a_client_stored_before_the_rule_cannot_redirect_elsewhere(monkeypatch, mail):
    _http(monkeypatch)
    rig.rig_secrets.write(vexa_oauth.CLIENTS, {"old": {
        "client_id": "old", "redirect_uris": ["http://evil.example/cb"], "client_name": "x"}})
    _, status, headers, _ = _authorize("old", redirect_uri="http://evil.example/cb")
    assert status == 400 and b"location" not in headers


# ── refresh tokens expire and rotate ─────────────────────────────────────────────────────────────

def _grant(monkeypatch, mail):
    """A full authorization: (client_id, access token, refresh token)."""
    _http(monkeypatch)
    cid = _register()
    rid = _start(cid)
    call("POST", "/oauth/authorize", form={"rid": rid, "email": "ana@example.com"})
    _, _, headers, _ = call("POST", "/oauth/authorize", form={"rid": rid, "code": mail[-1][1]})
    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(headers[b"location"].decode()).query)["code"][0]
    _, status, _, out = call("POST", "/oauth/token", form={
        "grant_type": "authorization_code", "code": auth_code, "code_verifier": VERIFIER,
        "client_id": cid})
    assert status == 200
    body = json.loads(out)
    return cid, body["access_token"], body["refresh_token"]


def _refresh(refresh, client_id=None):
    form = {"grant_type": "refresh_token", "refresh_token": refresh}
    if client_id:
        form["client_id"] = client_id
    _, status, _, out = call("POST", "/oauth/token", form=form)
    return status, json.loads(out)


def test_a_refresh_rotates_both_tokens(monkeypatch, mail):
    cid, access, refresh = _grant(monkeypatch, mail)
    status, out = _refresh(refresh, cid)
    assert status == 200 and out["refresh_token"] != refresh and out["access_token"] != access
    assert vexa_oauth.resolve_token(access, CANON) is None, "the old access token survived"
    assert vexa_oauth.resolve_token(out["access_token"], CANON)["email"] == "ana@example.com"
    status, again = _refresh(refresh, cid)
    assert status == 400 and again["error"] == "invalid_grant", "a refresh token worked twice"
    assert _refresh(out["refresh_token"], cid)[0] == 200


def test_a_refresh_token_expires(monkeypatch, mail):
    cid, _, refresh = _grant(monkeypatch, mail)
    toks = rig.rig_secrets.read(vexa_oauth.TOKENS)
    for rec in toks.values():
        rec["refresh_exp"] = time.time() - 1
    rig.rig_secrets.write(vexa_oauth.TOKENS, toks)
    assert _refresh(refresh, cid)[0] == 400


def test_a_refresh_from_another_client_is_refused(monkeypatch, mail):
    _, _, refresh = _grant(monkeypatch, mail)
    assert _refresh(refresh, "vexa-client-someone-else")[0] == 400


def test_a_refresh_asks_admission_again(monkeypatch, mail):
    cid, _, refresh = _grant(monkeypatch, mail)
    _http(monkeypatch, admission=(200, {"admitted": False, "why": "not-allowed"}))
    status, out = _refresh(refresh, cid)
    assert status == 400 and out["error"] == "invalid_grant"


def test_an_authorization_code_is_bound_to_its_client(monkeypatch, mail):
    _http(monkeypatch)
    cid = _register()
    rid = _start(cid)
    call("POST", "/oauth/authorize", form={"rid": rid, "email": "ana@example.com"})
    _, _, headers, _ = call("POST", "/oauth/authorize", form={"rid": rid, "code": mail[-1][1]})
    auth_code = urllib.parse.parse_qs(urllib.parse.urlparse(headers[b"location"].decode()).query)["code"][0]
    _, status, _, _ = call("POST", "/oauth/token", form={
        "grant_type": "authorization_code", "code": auth_code, "code_verifier": VERIFIER,
        "client_id": "vexa-client-someone-else"})
    assert status == 400
