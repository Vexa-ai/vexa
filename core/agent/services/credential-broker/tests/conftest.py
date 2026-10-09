"""Fixtures: a broker over a temporary state dir, three role keys, and an in-memory store.

`signed(role, method, path, body)` signs a request exactly as the real callers do, with the
vendored credential-broker.v1 signer; an agent-role request also carries the gateway's signed
identity for its actor (gateway-identity.v1), as agent-api forwards it. Tests never mount a real key
or reach a network.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from credential_broker import assertion, identity_token
from credential_broker.app import Broker, create_app
from credential_broker.settings import Settings
from credential_broker.store import Record, StoreUnavailable

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "credential-broker.v1"
GOOGLE = {"client_id": "fixture-client.apps.googleusercontent.com", "client_secret": "fixture-client-secret"}
REDIRECT = "https://app.example.test/api/auth/callback/google"


class FakeStore:
    """Versioned in-memory store with the Store port's semantics, plus a call log."""

    name = "local"

    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.calls: list[tuple] = []
        self.fail: Optional[str] = None

    def put(self, path: str, data: dict, *, cas: Optional[int] = None) -> Record:
        self.calls.append(("put", path, data, cas))
        if self.fail:
            raise StoreUnavailable(self.fail)
        versions = self.rows.setdefault(path, [])
        if cas is not None and cas != len(versions):
            raise StoreUnavailable("conflict")
        versions.append(json.loads(json.dumps(data)))
        return Record(data=data, version=len(versions), receipt=uuid.uuid4().hex)

    def get(self, path: str, *, version: Optional[int] = None) -> Optional[Record]:
        self.calls.append(("get", path, version))
        if self.fail:
            raise StoreUnavailable(self.fail)
        versions = self.rows.get(path) or []
        if not versions:
            return None
        n = version or len(versions)
        if not 1 <= n <= len(versions):
            return None
        return Record(data=versions[n - 1], version=n, receipt=uuid.uuid4().hex)

    def healthy(self) -> bool:
        return not self.fail

    def puts(self, path: str) -> list:
        return [c for c in self.calls if c[0] == "put" and c[1] == path]


#: The gateway's signing key for this session. The broker is given only its public half.
GATEWAY_KEY = identity_token.generate_signing_key()


def gateway_identity(sub: str, *, key=None, **kw) -> str:
    """X-Vexa-Identity as the gateway signs it for ``sub`` (gateway-identity.v1)."""
    return identity_token.sign(key or GATEWAY_KEY, {"sub": str(sub), "scopes": ["bot", "tx"]}, **kw)


def write_keys(root: Path) -> dict:
    files = {}
    pub = root / "identity-public-key.pem"
    pub.write_bytes(identity_token.public_key_pem(GATEWAY_KEY))
    files["identity"] = str(pub)
    for role in ("agent", "human", "git"):
        p = root / f"{role}.key"
        p.write_text(f"fixture-{role}-key-" + "0123456789abcdef" * 3 + "\n")
        files[role] = str(p)
    return files


@pytest.fixture
def keys(tmp_path) -> dict:
    return write_keys(tmp_path)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def settings(tmp_path, keys) -> Settings:
    return Settings(state_dir=tmp_path / "state", key_files={r: keys[r] for r in ("agent", "human", "git")},
                    store="local", store_key_file="unused",
                    google_client_id=GOOGLE["client_id"], google_client_secret=GOOGLE["client_secret"],
                    product_redirect=REDIRECT, identity_public_key_file=keys["identity"])


@pytest.fixture
def broker(settings, store) -> Broker:
    return Broker(settings, store)


@pytest.fixture
def client(broker) -> TestClient:
    return TestClient(create_app(broker))


@pytest.fixture
def signed(client, keys):
    def call(role, method, path, body=None, *, actor="product-user", session="browser-session",
             key_role=None, at=None, nonce=None, headers=None, identity="signed"):
        raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        key = Path(keys[key_role or role]).read_bytes().strip()
        header = assertion.sign(key, role=role, actor=actor, session=session, method=method, path=path,
                                body=raw, at=at, nonce=nonce)
        h = {assertion.HEADER: header, "Content-Type": "application/json"}
        # agent-api forwards the gateway's signature over the person on every agent-role call;
        # `identity=None` sends none, any other string is sent verbatim.
        if role == "agent" and identity == "signed":
            h[identity_token.HEADER] = gateway_identity(actor)
        elif identity not in (None, "signed"):
            h[identity_token.HEADER] = identity
        h.update(headers or {})
        return client.request(method, path, content=raw, headers=h, follow_redirects=False)
    return call


@pytest.fixture
def connection(signed):
    """Create a connection as the agent; returns its id."""
    def make(provider="custom_secret", label="Fixture", actor="product-user"):
        r = signed("agent", "POST", "/api/setup", {"provider": provider, "label": label}, actor=actor)
        assert r.status_code == 200, r.text
        return r.json()["connection_id"]
    return make


@pytest.fixture
def ready(broker):
    """Mark a connection ready at a given store version (bypassing consent, for read paths)."""
    def mark(cid, version=1):
        broker.sql("UPDATE connections SET status='ready', version=? WHERE id=?", (version, cid))
    return mark


def golden(name: str) -> dict:
    return json.loads((CONTRACT / "golden" / name).read_text())


def schema() -> dict:
    return json.loads((CONTRACT / "credential-broker.schema.json").read_text())
