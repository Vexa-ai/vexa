"""N16 · the sign-in lists are validated at boot, and a malformed entry refuses it (P14).

A malformed `VEXA_SIGNIN_ALLOW` or `VEXA_ADMIN_EMAILS` entry matches nothing. Before this check it
surfaced only on `GET /internal/settings/signin`, i.e. after the person it was written for had been
turned away — or, for the admin list, after the instance had closed its claim and named an admin who
does not exist. Offline: `build_production_app` configures an engine without connecting.
"""
from __future__ import annotations

import pytest

from admin_api.app import signin_allow
from admin_api.config_preflight import ConfigError

FAKE_URL = "postgresql+asyncpg://u:p@localhost:5432/vexa"


@pytest.fixture()
def boot_env(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-real-secret")
    monkeypatch.setenv("DB_PASSWORD", "a-real-db-password")
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    return monkeypatch


def _boot():
    from admin_api.__main__ import build_production_app
    return build_production_app()


def test_well_formed_lists_boot(boot_env):
    boot_env.setenv("VEXA_SIGNIN_ALLOW", "@example.com, alice@example.org")
    boot_env.setenv("VEXA_ADMIN_EMAILS", "owner@example.com")
    assert signin_allow.boot_problems() == []
    assert _boot() is not None


def test_unset_lists_boot(boot_env):
    assert _boot() is not None


def test_a_malformed_allow_list_entry_refuses_the_boot_and_names_it(boot_env):
    boot_env.setenv("VEXA_SIGNIN_ALLOW", "@example.com, bank.example")
    with pytest.raises(ConfigError) as refused:
        _boot()
    assert "VEXA_SIGNIN_ALLOW" in str(refused.value) and "bank.example" in str(refused.value)


def test_a_domain_or_a_typo_in_the_admin_list_refuses_the_boot(boot_env):
    boot_env.setenv("VEXA_ADMIN_EMAILS", "owner@example.com, @example.com")
    with pytest.raises(ConfigError) as refused:
        _boot()
    assert "VEXA_ADMIN_EMAILS" in str(refused.value) and "@example.com" in str(refused.value)
    boot_env.setenv("VEXA_ADMIN_EMAILS", "owner-at-example.com")
    with pytest.raises(ConfigError):
        _boot()
