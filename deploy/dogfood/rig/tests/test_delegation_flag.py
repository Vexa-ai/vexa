"""A worker is identified by its verified delegation, never by whether its token carried a `scope`.

delegation.v1 requires `scope`, and agent-api always mints one. These tests sign a token the
contract would not issue (no `scope` claim), send it through the real front door, and hold the rig
to treating the caller as a worker anyway: refused a person's mail and the human-only verbs, kept on
the internal tier for git with an empty ceiling, and refused a named workspace.

Offline: `_Auth` is driven directly around a stand-in app that calls the tool inside the request,
and admin-api, agent-api and the mail double are the recording stand-in.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time

import vexa_control_mcp as rig
from conftest import as_user, tool


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unscoped_token(uid: str = "7") -> str:
    """A correctly signed, unexpired delegation for ``uid`` whose claims carry no `scope`."""
    now = int(time.time())
    head = _b64(json.dumps({"alg": "HS256", "typ": "vxd"}).encode())
    body = _b64(json.dumps({"sub": uid, "aud": rig.DELEGATION_AUDIENCE, "iat": now,
                            "exp": now + 300, "jti": "test-unscoped"}).encode())
    sig = hmac.new(rig.DELEGATION_SECRET.encode(), f"{head}.{body}".encode("ascii"),
                   hashlib.sha256).digest()
    return f"{rig.DELEGATION_PREFIX}{head}.{body}.{_b64(sig)}"


def _as_worker(call):
    """``call()``'s result, run inside a request the real middleware authenticated with an
    unscoped delegation token."""
    out: dict = {}

    async def inner(scope, receive, send):
        out["result"] = call()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        out.setdefault("sent", []).append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "query_string": b"",
             "headers": [(b"host", b"localhost:18310"),
                         (b"authorization", f"Bearer {_unscoped_token()}".encode())],
             "scheme": "http", "client": ("127.0.0.1", 5000)}
    asyncio.run(rig._Auth(inner)(scope, receive, send))
    assert "result" in out, f"the request never reached the app: {out.get('sent')}"
    return out["result"]


def _msg(mid, to):
    return {"ID": mid, "From": {"Address": "vexa@vexa.ai"}, "To": [{"Address": a} for a in to],
            "Subject": f"subject {mid}", "Text": f"body {mid}"}


def test_an_unscoped_worker_never_reads_its_person_s_mail(monkeypatch):
    mine = _msg("m4", ["me@example.com"])
    http = as_user(monkeypatch, "7", routes={
        "/api/v1/messages": (200, {"total": 1, "messages": [mine]}),
        "/api/v1/message/": (200, mine)})
    rig.rig_secrets.write(rig.TOKENS_STORE, {"vxa_mcp_t": {"uid": "7", "email": "me@example.com"}})
    rig.CURRENT.set(None)
    for verb, args in (("mail_read", ("m4",)), ("mail_inbox", ())):
        out = json.loads(_as_worker(lambda: tool(verb)(*args)))
        assert out.get("refused") == "delegated", (verb, out)
    assert not http.urls("/api/v1/"), "the mail double was read for a worker"


def test_an_unscoped_worker_keeps_the_internal_tier_for_git_with_an_empty_ceiling(monkeypatch):
    http = as_user(monkeypatch, "7", routes={
        "/admin/users/7/tokens": (200, {"token": "vxa_person_key"}),
        "/workspace/": (200, {"state": "cloned", "branch": "main", "url": "x"})})
    rig._USER_KEYS.clear()
    rig.rig_secrets.write(rig.USER_KEYS_STORE, {})
    rig.CURRENT.set(None)
    # `_http` adds `_agent_identity_headers()` to every agent-api call that names a person; the
    # recorder stands in for `_http`, so those headers are read inside the same request.
    _, identity = _as_worker(lambda: (tool("workspace_push")(), rig._agent_identity_headers()))
    assert not http.urls("/tokens"), "the person's key was minted for a worker"
    assert not http.urls(rig.GATEWAY), "a worker's git call went through the gateway as the person"
    calls = [c for c in http.calls if "/workspace/push" in c["url"]]
    assert calls and all(c["url"].startswith(f"{rig.AGENT_API}/api/") for c in calls)
    assert all(c["headers"].get("X-User-Id") == "7" for c in calls)
    # Empty, not absent: agent-api reads an empty regime as unwatched and an empty set as the
    # worker's own workspace only. Absent would read as the person.
    assert identity.get("X-User-Regime") == "", identity
    assert identity.get("X-User-Delegation-Workspaces") == "", identity


def test_an_unscoped_worker_is_refused_a_human_only_verb(monkeypatch):
    http = as_user(monkeypatch, "7")
    rig.CURRENT.set(None)
    out = json.loads(_as_worker(lambda: tool("meeting_delete")(meeting_id="31")))
    assert out.get("refused") == "regime", out
    assert not [c for c in http.calls if c["method"] == "DELETE"]


def test_an_unscoped_worker_is_refused_a_named_workspace(monkeypatch):
    http = as_user(monkeypatch, "7", routes={"/api/workspace/file": (200, {})})
    rig.CURRENT.set(None)
    out = json.loads(_as_worker(
        lambda: tool("workspace_write")(slug="team", path="notes/a.md", content="x")))
    assert out.get("refused") == "out_of_scope", out
    assert not http.urls("/api/workspace/file")


def test_a_person_s_own_session_is_not_a_worker(monkeypatch):
    """The flag is set only where a delegation verified; every other path leaves it off."""
    as_user(monkeypatch, "7")
    assert rig._delegated_call() is False
    assert "X-User-Regime" not in rig._agent_identity_headers()
