"""D2 (0.13.4): the workspace principal — a room's turn as the workspace's agent, not a person.

The delegation token's `sub` is the workspace's own numeric principal (identity's `principals`), with
`principal_kind: "workspace"`, `principal_workspace` and `on_behalf_of=<speaker>`. The minter refuses
every shape that would let a room borrow a person's reach (no `shared`, `workspaces="*"`, grants, a
missing workspace or speaker, itself as the speaker) and refuses the claims on a person's token. The
refresh carries the principal over whole (and a cross-org room's audience, H2). The gateway signs it
into `x-user-principal-kind` and friends, which agent-api reads as a shared caller.
"""
from __future__ import annotations

import time

import fakeredis
import pytest

from control_plane import delegation_refresh as drf
from control_plane import delegation_revocation as dr
from control_plane import identity_token
from shared import delegation
from shared.units import delegation_key

SECRET = "test-delegation-secret"


def _ws(**kw):
    args = dict(subject="1042", regime="human", workspaces=["team"], shared=True,
                principal_kind="workspace", principal_workspace="team", on_behalf_of="43")
    args.update(kw)
    return delegation.mint_delegation(SECRET, **args)


def test_a_workspace_token_names_the_principal_its_workspace_and_the_speaker():
    claims = delegation.verify_delegation(SECRET, _ws())
    assert claims["sub"] == "1042"
    assert claims["principal_kind"] == "workspace" and claims["principal_workspace"] == "team"
    assert claims["on_behalf_of"] == "43" and "bills_to" not in claims, "identity's table owns the payer"
    assert claims["scope"] == {"regime": "human", "workspaces": ["team"], "shared": True}
    assert delegation.principal_of(claims) == {"kind": "workspace", "id": "1042", "workspace": "team",
                                               "on_behalf_of": "43"}


def test_a_person_token_is_byte_identical_and_reads_as_a_person():
    now = 1767225600
    tok = delegation.mint_delegation(SECRET, subject="42", regime="human", workspaces="*", now=now, jti="j")
    claims = delegation.verify_delegation(SECRET, tok, now=now)
    assert set(claims) == {"sub", "aud", "scope", "iat", "exp", "jti"}
    assert delegation.principal_of(claims)["kind"] == "person"


@pytest.mark.parametrize("kw", [
    {"shared": False},                              # a room's turn is always shared
    {"workspaces": "*"},                            # always its explicit ceiling
    {"on_behalf_of": ""},                           # who it answered
    {"principal_workspace": ""},                    # whose agent it is
    {"on_behalf_of": "1042"},                       # never itself
    {"principal_kind": "room"},                     # the one kind there is
    {"regime": "autonomous", "grants": ["bot.request"], "flow": "user:1/x"},   # no routine grants
], ids=["unshared", "star", "no-speaker", "no-workspace", "self", "other-kind", "grants"])
def test_the_minter_refuses_a_workspace_token_that_could_borrow_a_persons_reach(kw):
    with pytest.raises(ValueError):
        _ws(**kw)


@pytest.mark.parametrize("kw", [{"on_behalf_of": "43"}, {"principal_workspace": "team"}])
def test_the_principal_claims_are_refused_on_a_persons_token(kw):
    with pytest.raises(ValueError):
        delegation.mint_delegation(SECRET, subject="42", regime="human", workspaces="*", **kw)


def test_the_refresh_carries_the_principal_and_the_audience_over_whole():
    store = fakeredis.FakeRedis(decode_responses=True)
    ttl, now = 1800, int(time.time())
    unit = "agent-ws-team-room-s1"
    tok = _ws(unit=unit, audience=delegation.AUDIENCE_CROSS_ORG, ttl_sec=ttl, now=now)
    store.set(dr.CURRENT_PREFIX + unit, tok)
    later = now + ttl // 2 + 1
    assert drf.Refresher(SECRET, lambda: ttl)(store, unit, later)
    fresh = delegation.verify_delegation(SECRET, store.get(delegation_key(unit)), now=later)
    assert fresh["sub"] == "1042" and fresh["principal_kind"] == "workspace"
    assert fresh["principal_workspace"] == "team" and fresh["on_behalf_of"] == "43"
    assert fresh["scope"]["shared"] is True and fresh["scope"]["audience"] == "cross_org"


def test_the_gateway_signs_the_principal_into_headers_a_service_reads_as_shared():
    validation = {"user_id": 1042, "scopes": ["bot", "tx"], "max_concurrent": 0,
                  "email": "ws-1042@principal.invalid", "is_admin": False, "person_is_admin": False,
                  "delegation": {"regime": "human", "workspaces": ["team"], "shared": True,
                                 "principal_kind": "workspace", "principal_workspace": "team",
                                 "on_behalf_of": "43", "bills_to": 42}}
    headers = identity_token.headers_from_claims(identity_token.claims_from_validation(validation))
    assert headers["x-user-id"] == "1042", "the principal's own account, never the owner's"
    assert headers["x-user-principal-kind"] == "workspace"
    assert headers["x-user-principal-workspace"] == "team"
    assert headers["x-user-delegation-on-behalf-of"] == "43" and headers["x-user-bills-to"] == "42"
    assert identity_token.is_workspace_principal(headers) and identity_token.is_shared(headers)
    headers.pop("x-user-delegation-shared")
    assert identity_token.is_workspace_principal(headers), "a missing shared flag never reads as private"


def test_another_kind_value_is_never_signed_as_a_workspace():
    validation = {"user_id": 42, "scopes": [], "max_concurrent": 3, "email": "a@example.com",
                  "is_admin": False, "person_is_admin": False,
                  "delegation": {"regime": "human", "workspaces": "*", "principal_kind": "person"}}
    claims = identity_token.claims_from_validation(validation)
    assert "principal_kind" not in claims["delegation"]
