"""A worker's delegation token resolves through the same oracle as an API key.

`/internal/validate` is identity's answer to "who is this bearer" for every resolver (the gateway,
flows). A `vxd_` delegation token minted by agent-api for one dispatch is a bearer too: verified
here (signature, audience, expiry), bound to an account that still exists, answered with the
person's identity, the two service scopes, and the dispatch's ceiling as `delegation`.

No database: `get_db` is overridden with a session that knows one user, so this runs anywhere.
"""
import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def first(self):
        return None


class _Session:
    def __init__(self, users):
        self._users = users

    async def execute(self, stmt):
        params = stmt.compile().params
        uid = next((v for v in params.values() if isinstance(v, int)), None)
        return _Result(self._users.get(uid))

    async def commit(self):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2,
                data={"is_admin": True, "memberships": [{"workspace_id": "ws_1"}]})
    app = create_app()

    async def _db():
        yield _Session({42: user})

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


#: What a resolver that reads `delegation` sends beside its token (identity.v1 AcceptsDelegationHeader).
DECLARED = {validate_mod.ACCEPTS_DELEGATION_HEADER: validate_mod.ACCEPTS_DELEGATION_VALUE}


def _validate(client, token, *, declared=True):
    headers = {"X-Internal-Secret": INTERNAL, **(DECLARED if declared else {})}
    return client.post("/internal/validate", json={"token": token}, headers=headers)


def test_a_delegation_token_resolves_to_its_person_and_ceiling(client):
    tok = delegation.mint_delegation(DLG, subject="42", regime="autonomous", workspaces=["ws_1"],
                                     target="ws_1")
    r = _validate(client, tok)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == 42
    assert body["email"] == "ada@example.com"
    assert body["max_concurrent"] == 2
    assert body["workspaces"] == ["ws_1"]
    assert sorted(body["scopes"]) == ["bot", "tx"]
    assert body["is_admin"] is False, "a worker never carries its person's admin role"
    assert body["delegation"] == {"regime": "autonomous", "workspaces": ["ws_1"], "target": "ws_1"}


def test_a_human_regime_token_carries_the_soft_ceiling(client):
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*")
    assert _validate(client, tok).json()["delegation"] == {"regime": "human", "workspaces": "*"}


def test_the_unit_a_token_was_minted_for_is_answered_as_the_session(client):
    """Vexa-ai/vexa#1783: the signed `unit` claim — the chat or job the worker runs as — is passed on,
    so the gateway can sign it into the identity it forwards and the broker can audit by it."""
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*",
                                     unit="agent-42-chat-main")
    assert _validate(client, tok).json()["delegation"] == {
        "regime": "human", "workspaces": "*", "unit": "agent-42-chat-main"}


def test_a_shared_turn_token_carries_shared_on_its_ceiling(client):
    """ADR-0048: a turn other people read is marked on the ceiling the gateway signs, so every
    service behind it can refuse a person-wide read."""
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces=["ws_1"], shared=True)
    assert _validate(client, tok).json()["delegation"] == {"regime": "human", "workspaces": ["ws_1"],
                                                           "shared": True}
    private = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces=["ws_1"])
    assert "shared" not in _validate(client, private).json()["delegation"]


def test_a_cross_org_room_token_carries_its_audience_beside_shared(client):
    """Rooms (K1): a shared turn some of whose readers are outside the org carries
    `audience: cross_org`, so crm-api answers it public fields only. Never without `shared`."""
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces=["ws_1"], shared=True,
                                     audience="cross_org")
    assert _validate(client, tok).json()["delegation"] == {"regime": "human", "workspaces": ["ws_1"],
                                                           "shared": True, "audience": "cross_org"}
    with pytest.raises(ValueError):
        delegation.mint_delegation(DLG, subject="42", regime="human", workspaces=["ws_1"], audience="cross_org")
    with pytest.raises(ValueError):
        delegation.mint_delegation(DLG, subject="42", regime="human", workspaces=["ws_1"], shared=True,
                                   audience="everyone")


@pytest.mark.parametrize("token", [
    delegation.mint_delegation("another-key", subject="42"),
    delegation.mint_delegation(DLG, subject="42", ttl_sec=-10),
    "vxd_not.a.token",
])
def test_a_forged_expired_or_malformed_token_is_401(client, token):
    assert _validate(client, token).status_code == 401


def test_a_token_for_an_account_that_no_longer_exists_is_401(client):
    tok = delegation.mint_delegation(DLG, subject="999")
    r = _validate(client, tok)
    assert r.status_code == 401
    assert "no such user" in r.json()["detail"]


def test_a_deployment_without_the_delegation_key_refuses_every_delegation(client, monkeypatch):
    monkeypatch.delenv("VEXA_MCP_DELEGATION_SECRET")
    tok = delegation.mint_delegation(DLG, subject="42")
    assert _validate(client, tok).status_code == 401


# ── a delegation answer goes only to a caller that declared it reads one ─────────────────────────

@pytest.mark.parametrize("regime,workspaces", [("autonomous", ["ws_1"]), ("human", "*")])
def test_a_caller_that_did_not_declare_it_reads_a_delegation_is_refused_like_an_unknown_token(
        client, regime, workspaces):
    """A resolver that predates delegation reads any 200 as the person. To it a worker's token names
    nobody: the same 401, the same body, as a bearer identity has never heard of."""
    tok = delegation.mint_delegation(DLG, subject="42", regime=regime, workspaces=workspaces)
    refused = _validate(client, tok, declared=False)
    unknown = _validate(client, "vxa_nobody-holds-this", declared=False)
    assert refused.status_code == 401, refused.text
    assert (refused.status_code, refused.json()) == (unknown.status_code, unknown.json())
    assert refused.json() == {"detail": "Invalid token"}


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 2 "])
def test_only_the_declared_value_counts_as_a_declaration(client, value):
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*")
    r = client.post("/internal/validate", json={"token": tok},
                    headers={"X-Internal-Secret": INTERNAL,
                             validate_mod.ACCEPTS_DELEGATION_HEADER: value})
    assert r.status_code == 401 and r.json() == {"detail": "Invalid token"}


def test_a_declared_caller_is_answered_with_the_delegation(client):
    tok = delegation.mint_delegation(DLG, subject="42", regime="autonomous", workspaces=["ws_1"])
    r = _validate(client, tok, declared=True)
    assert r.status_code == 200, r.text
    assert r.json()["delegation"] == {"regime": "autonomous", "workspaces": ["ws_1"]}
    assert r.json()["user_id"] == 42


def test_an_undeclared_caller_is_refused_before_the_token_is_examined(client, monkeypatch):
    """Not even a deployment without the delegation key tells an undeclared caller anything
    different from `Invalid token`."""
    monkeypatch.delenv("VEXA_MCP_DELEGATION_SECRET")
    tok = delegation.mint_delegation(DLG, subject="42")
    r = _validate(client, tok, declared=False)
    assert r.status_code == 401 and r.json() == {"detail": "Invalid token"}


def test_an_api_key_needs_no_declaration(client):
    """The declaration is about delegation only: a person's own key answers the same either way."""
    a, b = (_validate(client, "vxa_nobody-holds-this", declared=d) for d in (True, False))
    assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_the_internal_tier_still_guards_the_oracle(client):
    tok = delegation.mint_delegation(DLG, subject="42")
    assert client.post("/internal/validate", json={"token": tok}, headers=DECLARED).status_code == 403


def test_the_person_behind_a_delegation_is_named_admin_or_not_and_the_worker_never_is(client):
    for regime, ws in (("human", "*"), ("autonomous", ["ws_1"])):
        body = _validate(client, delegation.mint_delegation(DLG, subject="42", regime=regime,
                                                            workspaces=ws)).json()
        assert body["person_is_admin"] is True
        assert body["is_admin"] is False


def test_a_person_who_is_not_the_admin_is_named_so(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    app = create_app()

    async def _db():
        yield _Session({7: User(id=7, email="bob@example.com", max_concurrent_bots=1, data={})})

    app.dependency_overrides[get_db] = _db
    body = _validate(TestClient(app), delegation.mint_delegation(DLG, subject="7", regime="human",
                                                                workspaces="*")).json()
    assert body["person_is_admin"] is False


def test_the_validated_delegation_is_logged_under_its_session_and_the_callers_trace(client):
    """Vexa-ai/vexa#1783: identity's line for a worker's bearer names the session from inside the
    signature and continues the caller's trace — and never carries the token."""
    from admin_api import obs
    sink: list = []
    obs.capture(sink)
    try:
        tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*",
                                         unit="agent-42-chat-main")
        headers = {"X-Internal-Secret": INTERNAL, "X-Trace-Id": "trace-from-the-gateway",
                   "X-User-Delegation-Unit": "agent-99-chat-forged", **DECLARED}
        r = client.post("/internal/validate", json={"token": tok}, headers=headers)
        assert r.status_code == 200, r.text
    finally:
        obs.capture(None)
    line = next(e for e in sink if e["event"] == "delegation_validated")
    assert line["session"] == "agent-42-chat-main"
    assert line["trace_id"] == "trace-from-the-gateway"
    assert all(tok not in str(e) and "forged" not in str(e) for e in sink)


def test_a_routine_token_carries_its_grants_and_flow_on_its_ceiling(client):
    """Flows phase 0: a routine's autonomous turn carries the unattended verbs its person granted it,
    with the routine's name, so the services behind the gateway can let exactly those through."""
    tok = delegation.mint_delegation(DLG, subject="42", regime="autonomous", workspaces=["42"],
                                     grants=["bot.request"], flow="user:42/bot-at-1213")
    assert _validate(client, tok).json()["delegation"] == {
        "regime": "autonomous", "workspaces": ["42"], "grants": ["bot.request"],
        "flow": "user:42/bot-at-1213"}
    plain = delegation.mint_delegation(DLG, subject="42", regime="autonomous", workspaces=["42"])
    assert "grants" not in _validate(client, plain).json()["delegation"]

