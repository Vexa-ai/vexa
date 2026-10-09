"""The platform-settings door — `GET/PUT /internal/settings/{key}` — is one table, not a branch per key.

Each key is one row of `platform_settings.SETTINGS`: its fields, the rulebook that cleans a write, and
— for a key the deployment also sets from its environment — the reader of that env half. The door
itself names no key. Both answers are one declared model, `PlatformSettingResponse`; `env` and
`env_problems` appear only for a key that has an env half (today `signin`).

No database: `get_db` is overridden with a session that holds platform_settings rows.
"""
import inspect
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from admin_api.app import platform_settings as ps
from admin_api.app.db import get_db
from admin_api.app.main import create_app

SECRET = "internal-secret-for-tests"
H = {"X-Internal-Secret": SECRET}


class _Rows:
    def __init__(self, rows=None):
        self.rows = {k: SimpleNamespace(key=k, value=v) for k, v in (rows or {}).items()}

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def commit(self):
        pass


@pytest.fixture()
def door(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)

    def _make(rows=None):
        db = _Rows(rows)
        app = create_app()

        async def _db():
            yield db

        app.dependency_overrides[get_db] = _db
        return TestClient(app), db

    return _make


def test_every_key_is_one_row_of_the_table():
    assert set(ps.SETTINGS) == {"models", "transcription", "setup", "diagnostics", "signin"}
    for kind in ps.SETTINGS.values():
        assert kind.fields and callable(kind.clean)
    assert [k for k, kind in ps.SETTINGS.items() if kind.env_half] == ["signin"]


def test_the_door_names_no_key():
    """A key-specific rule belongs in the key's row of the table, never in the door."""
    for handler in (ps.get_platform_setting, ps.put_platform_setting):
        src = inspect.getsource(handler)
        assert "signin_allow" not in src
        for key in ps.SETTINGS:
            assert f'"{key}"' not in src, f"the door branches on {key!r}"


def test_both_answers_are_the_declared_model():
    for route in ps.router.routes:
        assert route.response_model is ps.PlatformSettingResponse
        assert route.response_model_exclude_unset is True
    assert set(ps.PlatformSettingResponse.model_fields) == {"key", "value", "env", "env_problems"}


@pytest.mark.parametrize("call", [
    lambda c: c.get("/internal/settings/global_setup", headers=H),
    lambda c: c.put("/internal/settings/global_setup", headers=H,
                    json={"state": "missing", "company": "Acme GmbH"}),
])
def test_the_retired_global_setup_key_is_unknown(door, call):
    """Nothing in the product writes or reads it any more, so it is not a settings key."""
    c, db = door()
    assert call(c).status_code == 404
    assert "global_setup" not in db.rows


def test_a_key_without_an_env_half_answers_key_and_value_only(door):
    c, _ = door()
    r = c.put("/internal/settings/models", headers=H, json={"model": "haiku"})
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "models", "value": {"model": "haiku"}}
    assert c.get("/internal/settings/models", headers=H).json() == {
        "key": "models", "value": {"model": "haiku"}}


def test_the_signin_read_carries_the_env_half_and_its_problems(door, monkeypatch):
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@oenb.at,typo.example")
    c, _ = door({"signin": {"allow": "alice@example.com"}})
    body = c.get("/internal/settings/signin", headers=H).json()
    assert set(body) == {"key", "value", "env", "env_problems"}
    assert body["key"] == "signin"
    assert body["value"] == {"allow": "alice@example.com"}
    assert body["env"] == {"allow": "@oenb.at"}
    assert len(body["env_problems"]) == 1 and "typo.example" in body["env_problems"][0]


def test_the_signin_read_with_no_env_states_an_empty_half(door):
    c, _ = door()
    assert c.get("/internal/settings/signin", headers=H).json() == {
        "key": "signin", "value": {}, "env": {"allow": ""}, "env_problems": []}


def test_a_write_answers_key_and_value_only_and_each_key_keeps_its_own_rulebook(door):
    c, db = door()
    r = c.put("/internal/settings/signin", headers=H, json={"allow": "Alice@Example.com\n@OENB.at"})
    assert r.json() == {"key": "signin", "value": {"allow": "alice@example.com, @oenb.at"}}
    r = c.put("/internal/settings/signin", headers=H, json={"allow": "x, oenb.at"})
    assert r.status_code == 422 and "@oenb.at" in r.json()["detail"]
    assert db.rows["signin"].value == {"allow": "alice@example.com, @oenb.at"}
    r = c.put("/internal/settings/models", headers=H, json={"base_url": "ftp://nope"})
    assert r.status_code == 422 and "base_url" in r.json()["detail"]


def test_a_write_that_recognises_nothing_is_refused(door):
    c, _ = door()
    r = c.put("/internal/settings/setup", headers=H, json={"nonsense": "x"})
    assert r.status_code == 400 and "nonsense" in r.json()["detail"]


def test_the_door_is_internal_tier(door):
    c, _ = door()
    assert c.get("/internal/settings/models").status_code == 403
    assert c.put("/internal/settings/models", json={"model": "x"}).status_code == 403
