"""A webhook secret that is stored sealed and does not open is never handed on: no unsigned delivery.

`/internal/validate` hands the gateway a person's webhook (url, secret, events), the gateway signs
it into the identity, and meeting-api delivers with that secret. If the stored secret does not open
under this service's at-rest key (a missing or replaced key file), handing on the URL without the
secret would make every delivery unsigned. The whole webhook is left out instead, and logged.
Also: admin-api refuses to boot without a usable at-rest key.
"""
import secrets

import pytest

from admin_api import at_rest
from test_internal_validate_wire import KEY, _client, _validate

PURPOSE = at_rest.WEBHOOK_SECRET_PURPOSE


def _key(tmp_path, monkeypatch, name="k"):
    f = tmp_path / name
    f.write_text(secrets.token_hex(32))
    monkeypatch.setenv(at_rest.KEY_FILE_ENV, str(f))
    return f


def test_a_sealed_secret_that_opens_is_handed_on(tmp_path, monkeypatch):
    _key(tmp_path, monkeypatch)
    data = {"webhook_url": "https://hooks.example/in", "webhook_secret": at_rest.seal("whsec_x", PURPOSE)}
    client, _ = _client(monkeypatch, data=data)
    body = _validate(client, {"token": KEY}).json()
    assert body["webhook_url"] == "https://hooks.example/in" and body["webhook_secret"] == "whsec_x"


def test_a_sealed_secret_that_does_not_open_leaves_the_whole_webhook_out(tmp_path, monkeypatch):
    _key(tmp_path, monkeypatch, "old")
    sealed = at_rest.seal("whsec_x", PURPOSE)
    _key(tmp_path, monkeypatch, "replaced")                    # the key file was replaced
    client, _ = _client(monkeypatch, data={"webhook_url": "https://hooks.example/in", "webhook_secret": sealed,
                                           "webhook_events": {"meeting.completed": True}})
    r = _validate(client, {"token": KEY})
    assert r.status_code == 200
    body = r.json()
    assert "webhook_url" not in body and "webhook_secret" not in body and "webhook_events" not in body


def test_admin_api_refuses_to_boot_without_the_key(monkeypatch, tmp_path):
    from admin_api.config_preflight import preflight, ConfigError

    monkeypatch.delenv(at_rest.KEY_FILE_ENV, raising=False)
    with pytest.raises(ConfigError, match=at_rest.KEY_FILE_ENV):
        preflight({"INTERNAL_API_SECRET": "x" * 40, "ADMIN_API_TOKEN": "y" * 40, "DB_PASSWORD": "p",
                   "VEXA_TERMINAL_SESSION_SECRET": "z" * 40})
    with pytest.raises(at_rest.AtRestKeyUnavailable):
        at_rest.load_key()
