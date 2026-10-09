"""The Git credential store asks the broker as the person the request acts for.

The broker serves a git-role call only with the gateway's signature over the person
(gateway-identity.v1), so agent-api forwards the signature the request it is serving carried.
`broker_client.ForwardedIdentity` holds that signature, and the subject the guard rebuilt from it,
for the life of the request; `git_secret_store` signs its assertion for that subject and forwards
the signature. With no signed identity in hand (the internal tier, a probe, work running outside
any request) nothing is sent: the store refuses, logged as a typed fault, and never falls back to
naming a person on agent-api's own say.
"""
import contextvars
import json
import logging
import threading

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import broker_assertion, broker_client, git_secret_store

SIGNED = "v1.signed-by-the-gateway.for-u1"


@pytest.fixture
def broker(tmp_path, monkeypatch):
    key = tmp_path / "git.key"
    key.write_text("fixture-git-key-" + "b" * 40 + "\n")
    monkeypatch.setenv("VEXA_GIT_STORE_BROKER_URL", "http://credential-broker:8100")
    monkeypatch.setenv("VEXA_GIT_STORE_KEY_FILE", str(key))
    sent = []
    real = httpx.Client

    def client(**kwargs):
        def handle(req):
            sent.append(req)
            return httpx.Response(200, json={"found": True, "value": "fixture"})
        return real(transport=httpx.MockTransport(handle), **{k: v for k, v in kwargs.items() if k != "transport"})
    monkeypatch.setattr(broker_client.httpx, "Client", client)
    return {"sent": sent, "key": key.read_bytes().strip()}


def app_with(*, forwarded=True):
    app = FastAPI()

    @app.get("/read")
    def read():                     # a sync route, served in the threadpool like the workspace routes
        return git_secret_store._call("pat/u1", "get")
    if forwarded:
        app.add_middleware(broker_client.ForwardedIdentity)
    return app


def test_the_request_s_signed_person_is_the_actor_and_the_signature_is_forwarded(broker):
    r = TestClient(app_with()).get("/read", headers={"x-user-id": "u1", "x-vexa-identity": SIGNED})
    assert r.status_code == 200 and r.json() == {"found": True, "value": "fixture"}
    req = broker["sent"][-1]
    claims = broker_assertion.verify(req.headers[broker_assertion.HEADER], key_for=lambda role: broker["key"],
                                     method="POST", path="/api/internal/git-secret", body=req.content)
    assert (claims["role"], claims["actor"]) == ("git", "u1")
    assert req.headers["x-vexa-identity"] == SIGNED
    assert json.loads(req.content) == {"name": "pat/u1", "action": "get", "value": None}


@pytest.mark.parametrize("headers", [
    {"x-user-id": "u1"},                         # the internal tier names a person without a signature
    {"x-vexa-identity": SIGNED},                 # a signature with no subject beside it
    {},
])
def test_without_a_signed_person_nothing_is_sent(broker, caplog, headers):
    caplog.set_level(logging.WARNING)
    client = TestClient(app_with(), raise_server_exceptions=False)
    assert client.get("/read", headers=headers).status_code == 500
    assert not broker["sent"]
    faults = [json.loads(r.getMessage()) for r in caplog.records if '"broker_fault"' in r.getMessage()]
    assert faults[-1]["kind"] == "identity_missing" and faults[-1]["role"] == "git"


def test_outside_any_request_the_store_refuses(broker):
    with pytest.raises(git_secret_store.GitStoreUnavailable) as e:
        git_secret_store._call("pat/u1", "get")
    assert "signed in through the gateway" in str(e.value)
    assert not broker["sent"]


def test_an_app_without_the_middleware_sends_nothing(broker):
    client = TestClient(app_with(forwarded=False), raise_server_exceptions=False)
    assert client.get("/read", headers={"x-user-id": "u1", "x-vexa-identity": SIGNED}).status_code == 500
    assert not broker["sent"]


def test_the_identity_is_held_for_one_request_only(broker):
    client = TestClient(app_with(), raise_server_exceptions=False)
    assert client.get("/read", headers={"x-user-id": "u1", "x-vexa-identity": SIGNED}).status_code == 200
    assert client.get("/read", headers={"x-user-id": "u1"}).status_code == 500
    assert broker_client.forwarded() == ("", "")


def test_work_a_request_hands_to_a_thread_keeps_the_identity_only_when_the_context_is_copied(broker):
    """A background thread does not inherit the request's context. Work handed to one (a repository
    import) must run under `contextvars.copy_context()` to reach the Git store."""
    seen = {}

    def work(label):
        seen[label] = broker_client.forwarded()

    app = FastAPI()

    @app.get("/spawn")
    def spawn():
        plain = threading.Thread(target=work, args=("plain",))
        copied = threading.Thread(target=contextvars.copy_context().run, args=(work, "copied"))
        for t in (plain, copied):
            t.start()
            t.join()
        return {}
    app.add_middleware(broker_client.ForwardedIdentity)
    TestClient(app).get("/spawn", headers={"x-user-id": "u1", "x-vexa-identity": SIGNED})
    assert seen == {"plain": ("", ""), "copied": ("u1", SIGNED)}


def test_installed_inside_the_guard_it_holds_the_signed_subject_not_an_asserted_one(broker):
    """agent-api installs `ForwardedIdentity` before `IdentityGuard` (so the guard runs first): the
    `x-user-id` it holds is the one the guard rebuilt from the verified claims, whatever the
    caller sent beside the token."""
    from control_plane import identity_token
    signing = identity_token.generate_signing_key()
    token = identity_token.sign(signing, {"sub": "u1", "scopes": []})
    app = app_with()
    app.add_middleware(identity_token.IdentityGuard, verify_key=signing.public_key(), service="test")
    r = TestClient(app).get("/read", headers={"x-vexa-identity": token, "x-user-id": "u2"})
    assert r.status_code == 200
    req = broker["sent"][-1]
    claims = broker_assertion.verify(req.headers[broker_assertion.HEADER], key_for=lambda role: broker["key"],
                                     method="POST", path="/api/internal/git-secret", body=req.content)
    assert claims["actor"] == "u1" and req.headers["x-vexa-identity"] == token


def test_the_shipped_app_installs_the_holder_inside_the_identity_guard(monkeypatch, tmp_path):
    """agent-api's own `create_app` holds the signed person for the Git store, and holds it INSIDE
    the guard, so what it holds is what the guard verified."""
    from control_plane import identity_token
    from control_plane.api import create_app
    from control_plane.dispatch import Dispatcher
    from shared.config import load_settings

    key = identity_token.generate_signing_key()
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(key))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "agent-test-internal-secret")
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(tmp_path / "ws"))

    class _Runtime:
        def spawn(self, workload_id, profile, env):
            return workload_id

    class _Identity:
        def mint(self, subject, launcher, workspaces, tools):
            return "tok"

    app = create_app(Dispatcher(load_settings(), _Runtime(), _Identity()))
    order = [m.cls for m in app.user_middleware]          # outermost first
    assert broker_client.ForwardedIdentity in order
    assert order.index(identity_token.IdentityGuard) < order.index(broker_client.ForwardedIdentity)


def test_a_repository_import_s_thread_keeps_the_request_s_signed_person(tmp_path, broker):
    """The import runs in a thread of its own; it carries the request's context, so its Git
    credential reads still act for the person who asked."""
    from control_plane import workspace_import

    seen = []
    done = threading.Event()

    def operation():
        seen.append(broker_client.forwarded())
        done.set()
        return {"slug": "x"}

    held = broker_client._FORWARDED.set(("u1", SIGNED))
    try:
        workspace_import.start(tmp_path, "u1", "https://example.com/r.git", "main", operation)
    finally:
        broker_client._FORWARDED.reset(held)
    assert done.wait(5)
    assert seen == [("u1", SIGNED)]
