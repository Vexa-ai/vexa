"""An authentication prefix the service names: `Authorization: <prefix> <credential>`.

Discord's bot API is the case that motivated it (`Authorization: Bot <token>`); nothing here is
Discord-specific. `bearer` stays `Bearer <credential>`, `raw` stays the bare credential."""
from unittest.mock import MagicMock, patch

import pytest

from credential_broker import connection_setup, secret_service

ENDPOINT = "https://chat.service.test/api/v10/users/@me"


def sent_headers(config):
    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = 200
    response.read.return_value = b'{"ok":true}'
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=connection):
        secret_service.execute(config, {})
    return connection.request.call_args.kwargs["headers"]


@pytest.mark.parametrize("prefix", ["Bot", "Token", "Bearer", "SSWS", "Klaviyo-API-Key"])
def test_prefix_renders_prefix_space_credential(prefix):
    config = secret_service.configure("fixture-token", ENDPOINT, "Authorization", "prefix", "GET", prefix=prefix)
    assert config["prefix"] == prefix
    assert sent_headers(config)["Authorization"] == f"{prefix} fixture-token"


def test_bearer_and_raw_unchanged():
    assert sent_headers(secret_service.configure("t", ENDPOINT, "Authorization", "bearer", "GET"))["Authorization"] == "Bearer t"
    assert sent_headers(secret_service.configure("t", ENDPOINT, "X-API-Key", "raw", "GET"))["X-API-Key"] == "t"


@pytest.mark.parametrize("prefix", ["", "Bot X", "Bot\n", "Bot:", "a" * 33, "Bót", "Bot\r\nCookie"])
def test_invalid_prefix_refused(prefix):
    with pytest.raises(secret_service.ServiceError):
        secret_service.configure("t", ENDPOINT, "Authorization", "prefix", "GET", prefix=prefix)


def test_prefix_only_with_prefix_scheme():
    with pytest.raises(secret_service.ServiceError):
        secret_service.configure("t", ENDPOINT, "Authorization", "bearer", "GET", prefix="Bot")


def test_setup_spec_carries_prefix():
    spec = connection_setup.validate({"endpoint": ENDPOINT, "scheme": "prefix", "prefix": "Bot", "secret_label": "Bot token"})
    assert spec["scheme"] == "prefix" and spec["prefix"] == "Bot"
    with pytest.raises((ValueError, secret_service.ServiceError)):
        connection_setup.validate({"endpoint": ENDPOINT, "scheme": "prefix"})
    with pytest.raises((ValueError, secret_service.ServiceError)):
        connection_setup.validate({"endpoint": ENDPOINT, "scheme": "prefix", "prefix": "Bot X"})


def test_prepared_prefix_setup_saved_and_called_with_the_exact_header(signed, connection, store):
    cid = connection("custom_secret", "Chat bot")
    r = signed("agent", "POST", f"/api/connections/{cid}/prepare",
               {"setup": {"endpoint": ENDPOINT, "scheme": "prefix", "prefix": "Bot", "secret_label": "Bot token"}})
    assert r.status_code == 200, r.text
    row = next(c for c in signed("agent", "GET", "/api/connections").json()["connections"] if c["id"] == cid)
    assert row["setup"]["prefix"] == "Bot"
    saved = signed("human", "POST", f"/api/connections/{cid}/custom-secret",
                   {"value": "fixture-token", "setup_request": row["setup_request"], "confirmed_host": "chat.service.test"})
    assert saved.status_code == 200, saved.text
    assert store.rows[cid][-1]["value"]["prefix"] == "Bot"
    connection_mock = MagicMock()
    response = connection_mock.getresponse.return_value
    response.status = 200
    response.read.return_value = b'{"id":"1"}'
    with patch.object(secret_service, "public_addresses", return_value=["8.8.8.8"]), \
            patch.object(secret_service, "PinnedHTTPS", return_value=connection_mock):
        r = signed("agent", "POST", f"/api/connections/{cid}/call", {})
    assert r.status_code == 200, r.text
    assert connection_mock.request.call_args.kwargs["headers"]["Authorization"] == "Bot fixture-token"
    assert "fixture-token" not in r.text
