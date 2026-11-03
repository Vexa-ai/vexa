"""S64 — an authenticated-bot storage misconfiguration refuses meeting-api's boot (P14).

With ``BOT_AUTHENTICATED`` on, the spawn and the session write-back both read the store through
``auth_session_config``, which refuses an incomplete store and a bots' key pair that reuses a storage
root key. Those were found only on the first ``POST /bots``; the boot now applies the same reading
before it builds anything. The refusal names variables, never a value.
"""
from __future__ import annotations

import pytest

import meeting_api.__main__ as main_mod
from meeting_api.config_preflight import ConfigError

ROOT = {"MINIO_ACCESS_KEY": "minio-root-access-0001", "MINIO_SECRET_KEY": "minio-root-secret-0002"}
STORE = {"BOT_AUTHENTICATED": "true", "BOT_USERDATA_S3_PATH": "userdata/bot-identity-1",
         "BOT_S3_ENDPOINT": "http://storage:9000", "BOT_S3_BUCKET": "vexa",
         "BOT_S3_ACCESS_KEY": "bot-read-access-0005", "BOT_S3_SECRET_KEY": "bot-read-secret-0006"}


@pytest.mark.parametrize("env", [
    {},                                                       # authenticated mode off
    {"BOT_AUTHENTICATED": "false", "BOT_S3_ACCESS_KEY": ROOT["MINIO_ACCESS_KEY"], **ROOT},
    {**ROOT, **STORE},                                        # complete, with the bots' own pair
])
def test_a_sound_or_unused_store_boots(env):
    main_mod._auth_session_at_boot(env)


@pytest.mark.parametrize("missing", ["BOT_USERDATA_S3_PATH", "BOT_S3_ENDPOINT", "BOT_S3_BUCKET"])
def test_an_incomplete_store_refuses_the_boot(missing):
    env = {**ROOT, **STORE}
    del env[missing]
    with pytest.raises(ConfigError, match="refuses to boot.*incomplete"):
        main_mod._auth_session_at_boot(env)


@pytest.mark.parametrize("half,root", [("BOT_S3_ACCESS_KEY", "MINIO_ACCESS_KEY"),
                                       ("BOT_S3_SECRET_KEY", "MINIO_SECRET_KEY")])
def test_a_bots_pair_that_reuses_a_root_key_refuses_the_boot_naming_no_value(half, root):
    env = {**ROOT, **STORE, half: ROOT[root]}
    with pytest.raises(ConfigError) as exc:
        main_mod._auth_session_at_boot(env)
    assert f"{half} = {root}" in str(exc.value)
    assert not any(v in str(exc.value) for v in (*ROOT.values(), *STORE.values()) if len(v) > 8)


def test_the_production_app_checks_it_before_building_anything(monkeypatch):
    """At BOOT: `build_production_app` raises before it opens a database engine."""
    monkeypatch.setattr(main_mod, "_require_config", lambda env=None: None)
    monkeypatch.delenv("REDIS_WORKLOAD_ACL", raising=False)
    for k, v in {**ROOT, **STORE, "BOT_S3_ACCESS_KEY": ROOT["MINIO_ACCESS_KEY"]}.items():
        monkeypatch.setenv(k, v)

    import meeting_api.db as db_mod

    def _no_engine(*a, **k):  # pragma: no cover — reaching it is the failure
        raise AssertionError("an engine was built before the authenticated-bot store was checked")

    monkeypatch.setattr(db_mod, "build_engine", _no_engine)
    with pytest.raises(ConfigError, match="BOT_S3_ACCESS_KEY = MINIO_ACCESS_KEY"):
        main_mod.build_production_app()
