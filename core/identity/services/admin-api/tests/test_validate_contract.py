"""`/internal/validate` answers exactly the sealed identity.v1 shape (ValidateRequest / ValidateResponse /
ValidateDelegation), checked against the contract's goldens and schema rather than a copy of either.

The pydantic models in `app/validate.py` are the server's spelling of that wire. These tests fail when
the two drift: a field one side has and the other lacks, a required set that differs, a golden the
model cannot round-trip, or an answer the schema refuses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import pathlib
import time

import pytest
from fastapi.testclient import TestClient

from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "core" / "identity").is_dir())
CONTRACT = ROOT / "core" / "identity" / "contracts" / "identity.v1"
SCHEMA = json.loads((CONTRACT / "identity.schema.json").read_text())
DEFS = SCHEMA["$defs"]
GOLDENS = sorted((CONTRACT / "golden").glob("ValidateResponse.*.json"))
INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"


def _conforms(body: dict) -> None:
    """The sealed ValidateResponse rules this answer must keep (the contract's own validator runs in
    gate:schema over the goldens; this holds the live answer to the same rules)."""
    sealed = DEFS["ValidateResponse"]
    assert set(body) <= set(sealed["properties"]), set(body) - set(sealed["properties"])
    assert set(sealed["required"]) <= set(body)
    for field, needs in sealed.get("dependentRequired", {}).items():
        if field in body:
            assert set(needs) <= set(body), (field, needs)
    if "delegation" in body:
        dlg, rules = body["delegation"], DEFS["ValidateDelegation"]
        assert set(dlg) <= set(rules["properties"]) and set(rules["required"]) <= set(dlg)
        assert dlg["regime"] in rules["properties"]["regime"]["enum"]
        assert dlg["workspaces"] == "*" or (isinstance(dlg["workspaces"], list)
                                            and all(isinstance(w, str) for w in dlg["workspaces"]))


@pytest.mark.parametrize("model,defn", [
    (validate_mod.ValidatedIdentity, "ValidateResponse"),
    (validate_mod.DelegationCeiling, "ValidateDelegation"),
    (validate_mod.ValidateRequest, "ValidateRequest"),
])
def test_the_model_declares_the_sealed_fields(model, defn):
    sealed = DEFS[defn]
    assert set(model.model_fields) == set(sealed["properties"]), defn
    required = {n for n, f in model.model_fields.items() if f.is_required()}
    assert required <= set(sealed.get("required", [])), defn


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda p: p.name)
def test_every_sealed_answer_round_trips_through_the_model(golden):
    body = json.loads(golden.read_text())
    out = validate_mod.ValidatedIdentity.model_validate(body).model_dump(mode="json", exclude_unset=True)
    assert out == body
    _conforms(out)


def test_the_regime_vocabulary_is_the_sealed_one():
    from typing import get_args

    sealed = DEFS["ValidateDelegation"]["properties"]["regime"]["enum"]
    assert set(get_args(validate_mod.DelegationCeiling.model_fields["regime"].annotation)) == set(sealed)


def test_the_goldens_cover_both_bearers():
    names = {p.name for p in GOLDENS}
    assert any("delegation" in n for n in names) and any("api-key" in n for n in names)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _token(scope: dict) -> str:
    """A delegation token signed with the deployment's key, carrying an arbitrary `scope`."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "vxdlg"}
    payload = {"sub": "42", "aud": "vexa-mcp", "scope": scope, "iat": now, "exp": now + 300, "jti": "j"}
    canon = lambda d: json.dumps(d, separators=(",", ":"), sort_keys=True).encode()  # noqa: E731
    body = _b64u(canon(header)) + "." + _b64u(canon(payload))
    sig = hmac.new(DLG.encode(), body.encode("ascii"), hashlib.sha256).digest()
    return "vxd_" + body + "." + _b64u(sig)


class _Session:
    def __init__(self, user):
        self.user = user

    async def execute(self, stmt):
        class R:
            def __init__(s, u):
                s.u = u

            def scalar_one_or_none(s):
                return s.u

            def first(s):
                return None
        return R(self.user)

    async def commit(self):
        pass


def _client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    app = create_app()

    async def _db():
        yield _Session(User(id=42, email="ada@example.com", max_concurrent_bots=2, data={}))

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _validate(client, token):
    """As a resolver that reads `delegation` asks (identity.v1 AcceptsDelegationHeader)."""
    return client.post("/internal/validate", json={"token": token},
                       headers={"X-Internal-Secret": INTERNAL,
                                validate_mod.ACCEPTS_DELEGATION_HEADER:
                                    validate_mod.ACCEPTS_DELEGATION_VALUE})


def test_the_signing_helper_matches_the_minter(monkeypatch):
    """The control: a token this test signs with a sealed regime is accepted, so the refusals below
    are about the scope and nothing else."""
    r = _validate(_client(monkeypatch), _token({"regime": "autonomous", "workspaces": ["ws_1"]}))
    assert r.status_code == 200, r.text
    _conforms(r.json())


@pytest.mark.parametrize("scope", [
    {"regime": "root", "workspaces": "*"},
    {"regime": "", "workspaces": ["ws_1"]},
    {"workspaces": ["ws_1"]},
    {"regime": "human", "workspaces": "everything"},
    {"regime": "human", "workspaces": [1, {"a": 1}]},
    "human",
])
def test_a_token_whose_scope_is_outside_the_sealed_shape_is_refused(monkeypatch, scope):
    r = _validate(_client(monkeypatch), _token(scope))
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "Invalid delegation: scope"


def test_the_delegation_declaration_is_the_sealed_header():
    """The header a resolver declares it reads `delegation` with is the contract's, by name."""
    assert validate_mod.ACCEPTS_DELEGATION_HEADER == DEFS["AcceptsDelegationHeader"]["const"]
    assert validate_mod.ACCEPTS_DELEGATION_VALUE == "1"
