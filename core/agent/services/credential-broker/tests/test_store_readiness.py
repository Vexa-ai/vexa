"""Readiness sees a dead store credential (fail loud).

An OpenBao token that has expired or been revoked leaves `/v1/sys/health` at 200, while every
credential operation is refused. The store's readiness check reads with the token, so `/ready`
answers 503 with `kind: config` and logs a typed `broker_fault` line, instead of reporting ready for
a broker that will refuse every Gmail, Calendar and custom-service call.
"""
import json

import httpx
import pytest

from credential_broker.app import create_app
from credential_broker.broker import Broker
from credential_broker.store import OpenBaoStore, StoreUnavailable
from fastapi.testclient import TestClient


def _openbao(tmp_path, token_status):
    """An OpenBao whose /sys/health is always 200 and whose token answers ``token_status``."""
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.headers.get("x-vault-token")))
        if req.url.path == "/v1/sys/health":
            return httpx.Response(200, json={"initialized": True, "sealed": False})
        if token_status == 404:
            return httpx.Response(404, json={"errors": []})
        return httpx.Response(token_status, json={"errors": ["permission denied"]})

    token = tmp_path / "token"
    token.write_text("fixture-openbao-token\n")
    return OpenBaoStore("https://bao:8200", token, transport=httpx.MockTransport(handler)), seen


@pytest.mark.parametrize("status", [401, 403])
def test_an_expired_or_revoked_token_is_unhealthy(tmp_path, status):
    store, seen = _openbao(tmp_path, status)
    with pytest.raises(StoreUnavailable) as e:
        store.healthy()
    assert e.value.kind == "config"
    assert seen and all(tok == "fixture-openbao-token" for _, path, tok in seen if path != "/v1/sys/health")


def test_a_live_token_reading_the_unwritten_probe_path_is_healthy(tmp_path):
    store, seen = _openbao(tmp_path, 404)
    assert store.healthy() is True
    assert seen == [("GET", f"/v1/connections/data/{OpenBaoStore.PROBE_PATH}", "fixture-openbao-token")]


def test_ready_fails_loud_on_a_dead_store_token(tmp_path, settings, capsys):
    store, _ = _openbao(tmp_path, 403)
    client = TestClient(create_app(Broker(settings, store)))
    capsys.readouterr()
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["reason"] == "store_unavailable" and r.json()["kind"] == "config"
    faults = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"event":"broker_fault"' in line]
    assert faults[-1]["fields"] == {"source": "store", "kind": "config", "route": "/ready"}
    assert client.get("/health").status_code == 200


def test_ready_is_ok_with_a_live_token(tmp_path, settings):
    store, _ = _openbao(tmp_path, 404)
    assert TestClient(create_app(Broker(settings, store))).get("/ready").status_code == 200
