"""The sign-in verbs are for external MCP clients only, and their links use the public address.

* A hosted worker (a caller the real `_Auth` front door authenticated with a `vxd_` delegation) is
  refused by `auth_link`, `auth_claim`, `start_onboarding` and `confirm_login` with
  `already_signed_in` — and is not offered them in its tool list.
* An anonymous external client still gets a link, built on the configured public address.
* A link is never handed out on a private address, and the post-sign-in commands use the same
  public address as the link.

Offline: `_Auth` is driven directly around a stand-in app; mail and admin-api are recorders.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import pytest

import public_origin
import vexa_control_mcp as rig
import vexa_oauth
from conftest import as_user, tool

PUBLIC = "https://mcp.example.com/mcp"
SIGNIN_CALLS = (("auth_link", ()), ("auth_claim", ("h-any",)),
                ("start_onboarding", ("robin@example.com",)),
                ("confirm_login", ("robin@example.com", "123456")))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _worker_token(uid: str = "7") -> str:
    """A correctly signed delegation for ``uid`` under the autonomous regime (a scheduled run)."""
    now = int(time.time())
    head = _b64(json.dumps({"alg": "HS256", "typ": "vxd"}).encode())
    body = _b64(json.dumps({"sub": uid, "aud": rig.DELEGATION_AUDIENCE, "iat": now,
                            "exp": now + 300, "jti": "test-signin-worker",
                            "scope": {"regime": "autonomous", "workspaces": []}}).encode())
    sig = hmac.new(rig.DELEGATION_SECRET.encode(), f"{head}.{body}".encode("ascii"),
                   hashlib.sha256).digest()
    return f"{rig.DELEGATION_PREFIX}{head}.{body}.{_b64(sig)}"


def _as_worker(call, *, awaitable=False):
    """``call()``'s result inside a request the real middleware authenticated as a worker."""
    out: dict = {}

    async def inner(scope, receive, send):
        out["result"] = await call() if awaitable else call()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        out.setdefault("sent", []).append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "query_string": b"",
             "headers": [(b"host", b"localhost:18310"),
                         (b"authorization", f"Bearer {_worker_token()}".encode())],
             "scheme": "http", "client": ("127.0.0.1", 5000)}
    asyncio.run(rig._Auth(inner)(scope, receive, send))
    assert "result" in out, f"the request never reached the app: {out.get('sent')}"
    return out["result"]


@pytest.fixture
def signin(monkeypatch):
    """Sign-in switched on, a public address configured, empty stores, mail captured."""
    sent: list = []
    monkeypatch.setattr(rig, "_send_code", lambda email, code: sent.append((email, code)) or None)
    monkeypatch.setenv(vexa_oauth.ENABLED_ENV, "1")
    monkeypatch.setattr(rig, "CANONICAL", PUBLIC)
    for store in (rig.EMAIL_CODES_STORE, rig.LOGINS_STORE, rig.TOKENS_STORE):
        rig.rig_secrets.write(store, {})
    rig._CODE_SENDS.clear()
    rig._SOURCE_SENDS.clear()
    rig.CALL_SOURCE.set("")
    http = as_user(monkeypatch, "7", routes={
        "/internal/signin-admission": (200, {"admitted": True, "why": "existing-user"}),
        "/admin/users/email/": (200, {"id": 42}), "/admin/users": (200, {"id": 42})})
    rig.CURRENT.set(None)
    return sent, http


@pytest.mark.parametrize("verb,args", SIGNIN_CALLS, ids=[v for v, _ in SIGNIN_CALLS])
def test_a_delegated_worker_is_refused_every_external_signin_verb(signin, verb, args):
    sent, http = signin
    out = json.loads(_as_worker(lambda: tool(verb)(*args)))
    assert out.get("refused") == "already_signed_in", (verb, out)
    assert out["verb"] == verb
    assert "already signed in" in out["tell_your_person"]
    assert "not a sign-in problem" in out["tell_your_person"]
    assert "human_session_required" in out["not_a_sign_in_problem"]
    assert sent == [], "a code was mailed for a worker"
    assert rig._logins() == {}, "a login handle was minted for a worker"
    assert not [c for c in http.calls if "/admin/users" in c["url"]]


def test_a_delegated_worker_cannot_claim_a_pending_handle(signin):
    """auth_claim of a delegated caller is refused even when the handle is real and finished."""
    rig._logins_save({"h-1": {"exp": time.time() + 600, "token": "vxa_mcp_x", "uid": "42",
                              "email": "robin@example.com"}})
    out = json.loads(_as_worker(lambda: tool("auth_claim")("h-1")))
    assert out.get("refused") == "already_signed_in", out
    assert "token" not in out
    assert "h-1" in rig._logins(), "the refusal spent the person's handle"


def test_a_worker_is_refused_even_while_signin_is_switched_off(signin, monkeypatch):
    """The true reason wins: a worker hears already_signed_in, not 'switched off'."""
    monkeypatch.delenv(vexa_oauth.ENABLED_ENV, raising=False)
    out = json.loads(_as_worker(lambda: tool("auth_link")()))
    assert out.get("refused") == "already_signed_in", out


def test_an_anonymous_external_client_still_gets_a_link_on_the_public_address(signin):
    out = json.loads(tool("auth_link")())
    assert out["give_your_person_this_link"] == f"https://mcp.example.com/login?h={out['handle']}"
    assert out["handle"] in rig._logins()
    assert json.loads(tool("auth_claim")(out["handle"])).get("pending") is True


def test_an_anonymous_client_can_still_start_onboarding(signin):
    sent, _ = signin
    out = json.loads(tool("start_onboarding")("robin@example.com"))
    assert out.get("code_sent_to") == "robin@example.com", out
    assert sent and sent[-1][0] == "robin@example.com"


@pytest.mark.parametrize("address", ["http://172.18.0.1:18321/mcp", "http://10.0.0.5/mcp",
                                     "https://169.254.1.1/mcp", "http://agent-mcp:18321/mcp",
                                     "http://0.0.0.0:18321/mcp", ""])
def test_auth_link_never_hands_out_a_link_on_a_private_address(signin, monkeypatch, address):
    monkeypatch.setattr(rig, "CANONICAL", address)
    out = json.loads(tool("auth_link")())
    assert "give_your_person_this_link" not in out, out
    assert out["error"] == "sign-in links are unavailable from this server"
    if address:
        assert address.split("//")[1].split("/")[0] not in json.dumps(out), "internal address leaked"
    assert rig._logins() == {}, "a handle was minted for a link nobody can open"


def test_the_post_signin_commands_use_the_public_address(signin):
    rig._logins_save({"h-2": {"exp": time.time() + 600, "token": "vxa_mcp_y", "uid": "42",
                              "email": "robin@example.com"}})
    out = json.loads(tool("auth_claim")("h-2"))
    assert f"http vexa {PUBLIC} " in out["persist_now"]
    assert "https://mcp.example.com/skill" in out["install_the_skill"]


@pytest.mark.skipif(not hasattr(rig.mcp, "list_tools"), reason="needs the real MCP SDK listing")
def test_a_worker_is_not_offered_the_signin_verbs_but_an_external_client_is(signin):
    offered_to_worker = {t.name for t in _as_worker(rig.mcp.list_tools, awaitable=True)}
    assert not offered_to_worker & rig.EXTERNAL_SIGNIN_VERBS, offered_to_worker
    assert "whats_waiting" in offered_to_worker
    offered_to_anonymous = {t.name for t in asyncio.run(rig.mcp.list_tools())}
    assert rig.EXTERNAL_SIGNIN_VERBS <= offered_to_anonymous


def test_the_listing_filter_hides_only_the_signin_verbs_and_only_from_workers(signin):
    assert all(rig._offered_to_caller(v) for v in rig.EXTERNAL_SIGNIN_VERBS)
    hidden = _as_worker(lambda: {v for v in rig.EXTERNAL_SIGNIN_VERBS | {"whats_waiting"}
                                 if not rig._offered_to_caller(v)})
    assert hidden == set(rig.EXTERNAL_SIGNIN_VERBS)


@pytest.mark.parametrize("url,loopback_ok", [
    ("https://mcp.example.com/mcp", False), ("https://8.8.8.8/mcp", False),
    ("http://localhost:18310/mcp", True), ("http://127.0.0.1:18310/mcp", True)])
def test_public_origin_accepts(url, loopback_ok):
    assert public_origin.problem(url, allow_loopback=loopback_ok) == ""


@pytest.mark.parametrize("url", [
    "", "http://mcp.example.com/mcp", "https://mcp.example.com/", "https://localhost/mcp",
    "https://127.0.0.1/mcp", "https://[::1]/mcp", "https://192.168.1.2/mcp",
    "https://172.18.0.1:18321/mcp", "https://[fe80::1]/mcp", "https://agent-mcp/mcp",
    "https://rig.internal/mcp", "https://box.local/mcp", "not a url"])
def test_public_origin_refuses(url):
    assert public_origin.problem(url, allow_loopback=False)
