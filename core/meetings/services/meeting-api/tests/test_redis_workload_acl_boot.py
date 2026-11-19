"""S51 — meeting-api refuses an unknown REDIS_WORKLOAD_ACL at boot, as agent-api does.

The switch is one name across both services. agent-api's settings validate it against
``^(per-workload|shared)$`` and refuse to start on anything else; meeting-api compared the value to
the exact string ``"shared"`` and read every other value — a typo, a capital letter — as
``per-workload``. So one deployment value could boot one service and be silently re-read by the other.
"""
from __future__ import annotations

import pytest

import meeting_api.__main__ as main_mod
from meeting_api.config_preflight import ConfigError


@pytest.mark.parametrize("env, mode", [
    ({}, "per-workload"),                                   # unset → the default
    ({"REDIS_WORKLOAD_ACL": ""}, "per-workload"),           # empty → the default
    ({"REDIS_WORKLOAD_ACL": "per-workload"}, "per-workload"),
    ({"REDIS_WORKLOAD_ACL": "shared"}, "shared"),
    ({"REDIS_WORKLOAD_ACL": "  shared\n"}, "shared"),       # surrounding whitespace is a carrier artefact
])
def test_the_two_modes_and_the_default_are_read(env, mode):
    assert main_mod._redis_workload_acl(env) == mode


@pytest.mark.parametrize("value", ["Shared", "share", "per_workload", "off", "none", "shared,per-workload"])
def test_any_other_value_refuses_the_boot(value):
    with pytest.raises(ConfigError) as exc:
        main_mod._redis_workload_acl({"REDIS_WORKLOAD_ACL": value})
    assert repr(value) in str(exc.value)          # the operator is told which value was refused


def test_the_production_app_checks_it_before_building_anything(monkeypatch):
    """The refusal is at BOOT: `build_production_app` raises before it opens a database engine or a
    Redis client — a misread mode never reaches a spawn."""
    monkeypatch.setattr(main_mod, "_require_config", lambda env=None: None)
    monkeypatch.setenv("REDIS_WORKLOAD_ACL", "Shared")

    import meeting_api.db as db_mod

    def _no_engine(*a, **k):  # pragma: no cover — reaching it is the failure
        raise AssertionError("an engine was built before REDIS_WORKLOAD_ACL was checked")

    monkeypatch.setattr(db_mod, "build_engine", _no_engine)
    with pytest.raises(ConfigError):
        main_mod.build_production_app()


def test_the_accepted_set_is_agent_apis():
    """The parity fact `redis-workload-acl-modes` holds the two patterns equal at the gate; this says
    the same thing where a meeting-api developer will see it."""
    assert main_mod._REDIS_WORKLOAD_ACL.pattern == r"^(per-workload|shared)$"
