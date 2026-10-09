"""Configuration: the boot refuses what it cannot honour, the declaration names every key the
settings read, and a configured environment builds a working app end to end with the real
encrypted store."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import write_keys
from credential_broker import assertion, identity_token, main, settings as settings_module
from credential_broker.config_preflight import ConfigError

SRC = Path(settings_module.__file__).parent
DECLARED = {k["key"] for k in json.loads((SRC / "config.v1.json").read_text())["keys"]}


def env_for(tmp_path, **over):
    keys = write_keys(tmp_path)
    store_key = tmp_path / "store.key"
    store_key.write_text("ab" * 32)
    env = {"VEXA_CONNECTIONS_AGENT_KEY_FILE": keys["agent"], "VEXA_CONNECTIONS_HUMAN_KEY_FILE": keys["human"],
           "VEXA_CONNECTIONS_GIT_KEY_FILE": keys["git"], "VEXA_CONNECTIONS_STATE_DIR": str(tmp_path / "state"),
           "VEXA_CONNECTIONS_STORE_KEY_FILE": str(store_key),
           "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": keys["identity"]}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}, keys


def test_every_key_settings_reads_is_declared():
    read = set(re.findall(r'"(VEXA_(?:CONNECTIONS|GATEWAY_IDENTITY)_[A-Z_]+)"', (SRC / "settings.py").read_text()))
    assert read and read <= DECLARED
    assert DECLARED <= read, "a declared key nobody reads is a dead surface"


@pytest.mark.parametrize("over,needle", [
    ({"VEXA_CONNECTIONS_AGENT_KEY_FILE": None}, "VEXA_CONNECTIONS_AGENT_KEY_FILE"),
    ({"VEXA_CONNECTIONS_HUMAN_KEY_FILE": ""}, "VEXA_CONNECTIONS_HUMAN_KEY_FILE"),
    ({"VEXA_CONNECTIONS_STORE_KEY_FILE": None}, "VEXA_CONNECTIONS_STORE_KEY_FILE"),
    ({"VEXA_CONNECTIONS_STORE": "openbao"}, "VEXA_CONNECTIONS_OPENBAO_ADDR"),
    ({"VEXA_CONNECTIONS_STORE": "vault"}, "`local` or `openbao`"),
    ({"VEXA_CONNECTIONS_STORE_KEY_FILE": "/nonexistent/store.key"}, "64 hex characters"),
    ({"VEXA_CONNECTIONS_PRODUCT_REDIRECT": "http://app.example.test/api/auth/callback/google"}, "PRODUCT_REDIRECT"),
    ({"VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": None}, "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE"),
    ({"VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE": "/nonexistent/identity.pem"}, "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE"),
])
def test_boot_refuses_an_unusable_configuration(tmp_path, over, needle):
    env, _ = env_for(tmp_path, **over)
    with pytest.raises(ConfigError) as e:
        main.build_app(env)
    assert needle in str(e.value)


def test_configured_environment_serves_with_the_encrypted_store(tmp_path, capsys):
    env, keys = env_for(tmp_path)
    client = TestClient(main.build_app(env))
    assert client.get("/health").json() == {"status": "ok", "service": "credential-broker", "store": "local"}
    human = Path(keys["human"]).read_bytes().strip()
    body = json.dumps({"provider": "custom_secret", "label": "Canary"}).encode()
    h = {"Content-Type": "application/json",
         assertion.HEADER: assertion.sign(human, role="human", actor="9", session="s", method="POST", path="/api/setup", body=body)}
    cid = client.post("/api/setup", content=body, headers=h).json()["connection_id"]
    body = json.dumps({"value": "STORE-CANARY-SECRET", "endpoint": "https://api.example.test/v1"}).encode()
    path = f"/api/connections/{cid}/custom-secret"
    h = {"Content-Type": "application/json",
         assertion.HEADER: assertion.sign(human, role="human", actor="9", session="s", method="POST", path=path, body=body)}
    assert client.post(path, content=body, headers=h).json() == {"connection_id": cid, "status": "ready"}
    state = tmp_path / "state"
    for f in ("secrets.sqlite", "metadata.sqlite"):
        assert b"STORE-CANARY-SECRET" not in (state / f).read_bytes()
    assert (state / "state-hmac.key").stat().st_mode & 0o077 == 0
    out = capsys.readouterr().out
    assert "STORE-CANARY-SECRET" not in out
    stored = [json.loads(l) for l in out.splitlines() if '"connection_audit"' in l and '"stored"' in l]
    assert stored and stored[-1]["fields"]["action"] == "credential.store" and stored[-1]["fields"]["store"] == "local"


def test_boot_refuses_the_published_rfc8032_identity_key(tmp_path):
    """gateway-identity.v1's goldens are made with RFC 8032 TEST 1, whose seed is printed in the RFC.
    A broker verifying with its public half would accept identities anybody can sign."""
    published = tmp_path / "rfc8032-test1.pem"
    published.write_text(identity_token.PUBLISHED_TEST_KEYS["RFC 8032 section 7.1 TEST 1"])
    env, _ = env_for(tmp_path, VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE=str(published))
    with pytest.raises(ConfigError) as e:
        main.build_app(env)
    assert "VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE" in str(e.value) and "RFC 8032" in str(e.value)
    assert "BEGIN" not in str(e.value)


@pytest.mark.parametrize("role", ["agent", "human", "git"])
def test_boot_refuses_a_role_key_published_in_the_contract_vectors(tmp_path, role):
    """credential-broker.v1's SignedAssertionVector goldens carry fixed fixture keys. A role key
    file still holding one would accept assertions anybody who read the repository can sign."""
    published = sorted(assertion.PUBLISHED_KEYS)[0]
    path = tmp_path / f"published-{role}.key"
    path.write_bytes(published + b"\n")
    env, _ = env_for(tmp_path, **{f"VEXA_CONNECTIONS_{role.upper()}_KEY_FILE": str(path)})
    with pytest.raises(ConfigError) as e:
        main.build_app(env)
    assert f"VEXA_CONNECTIONS_{role.upper()}_KEY_FILE" in str(e.value)
    assert published.decode() not in str(e.value), "a refusal must never echo the key"
