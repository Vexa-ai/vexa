"""F208 — admin-api's flows publish-edge URL key gains the VEXA_ prefix meeting-api and agent-api
already used; the bare `FLOWS_API_URL` name is honoured for one release so an unmigrated
deployment keeps publishing, with a boot warning naming the rename. Offline, stdlib only — no
docker, unlike test_onboarding_event.py's stack-backed suite.
"""
from __future__ import annotations

from admin_api.app import events as events_mod


def _clear(monkeypatch):
    monkeypatch.delenv(events_mod.FLOWS_API_URL_ENV, raising=False)
    for legacy in events_mod.FLOWS_API_URL_ENV_DEPRECATED:
        monkeypatch.delenv(legacy, raising=False)


def test_the_canonical_name_carries_the_repo_prefix():
    assert events_mod.FLOWS_API_URL_ENV == "VEXA_FLOWS_API_URL"


def test_the_deprecated_name_is_the_old_bare_spelling():
    assert events_mod.FLOWS_API_URL_ENV_DEPRECATED == ("FLOWS_API_URL",)


def test_it_reads_the_canonical_name(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, "http://flows-api:8200")
    assert events_mod._flows_base() == "http://flows-api:8200"


def test_it_falls_back_to_the_deprecated_name(monkeypatch):
    """An unmigrated deployment — one that still exports the pre-F208 bare name and nothing else —
    keeps publishing rather than silently going dark."""
    _clear(monkeypatch)
    monkeypatch.setenv("FLOWS_API_URL", "http://flows-api:8200")
    assert events_mod._flows_base() == "http://flows-api:8200"


def test_the_canonical_name_wins_when_both_are_set(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, "http://canonical:8200")
    monkeypatch.setenv("FLOWS_API_URL", "http://legacy:8200")
    assert events_mod._flows_base() == "http://canonical:8200"


def test_unset_is_still_the_no_flows_profile(monkeypatch):
    _clear(monkeypatch)
    assert events_mod._flows_base() == ""


def test_deprecated_flows_url_env_in_use_is_none_on_the_canonical_name(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, "http://flows-api:8200")
    assert events_mod.deprecated_flows_url_env_in_use() is None


def test_deprecated_flows_url_env_in_use_is_none_when_unset(monkeypatch):
    _clear(monkeypatch)
    assert events_mod.deprecated_flows_url_env_in_use() is None


def test_deprecated_flows_url_env_in_use_names_the_legacy_var(monkeypatch):
    """This is what `__main__.build_production_app` logs a WARNING with at boot — the check must
    name the exact variable a deploy still has set, not just say "something is deprecated"."""
    _clear(monkeypatch)
    monkeypatch.setenv("FLOWS_API_URL", "http://flows-api:8200")
    assert events_mod.deprecated_flows_url_env_in_use() == "FLOWS_API_URL"
