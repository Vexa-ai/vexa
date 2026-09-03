"""F95 — the internal-tier secret: ONE name, no default, and the placeholders that shipped.

Flows spelled this secret `VEXA_INTERNAL_SECRET` — a third name for a value compose, helm,
admin-api, gateway and agent-api all called `INTERNAL_API_SECRET`. Three names meant three refusal
lists, and this file's list refused four generic placeholders (`changeme`, `change-me`, `default`,
`secret`) while the literal the deploy surfaces ACTUALLY shipped — `vexa-internal-secret`, published
in the OSS repository and the exact value the internal tier compared against — was on none of them.

A refusal list written from imagination rather than from the compose file it defends against is the
whole finding, so these tests pin the list to the literals that were really there.
Offline, stdlib only.
"""
from __future__ import annotations

import pytest

from flows_steps import common


def _clear(monkeypatch):
    for name in (common.INTERNAL_SECRET_ENV, *common.INTERNAL_SECRET_ENV_DEPRECATED):
        monkeypatch.delenv(name, raising=False)


def test_the_canonical_name_is_the_compose_secret_key():
    assert common.INTERNAL_SECRET_ENV == "INTERNAL_API_SECRET"


def test_it_reads_the_canonical_name(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-real-secret")
    assert common.require_internal_secret() == "a-real-secret"


def test_the_old_spellings_still_work_and_say_so(monkeypatch, capsys):
    """An operator mid-upgrade is WARNED, not 401ed — a rename that silently drops the value would
    leave flows reading zero desks with nothing looking broken, which is the failure this whole
    module's refusal exists to prevent."""
    for legacy in common.INTERNAL_SECRET_ENV_DEPRECATED:
        _clear(monkeypatch)
        monkeypatch.setenv(legacy, "a-real-secret")
        assert common.require_internal_secret() == "a-real-secret"
        err = capsys.readouterr().err
        assert legacy in err and "DEPRECATED" in err
        assert "a-real-secret" not in err, "a warning must never echo the value"


def test_the_canonical_name_wins_over_a_stale_legacy_export(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("INTERNAL_API_SECRET", "canonical")
    monkeypatch.setenv("VEXA_INTERNAL_SECRET", "stale")
    assert common.require_internal_secret() == "canonical"


def test_unset_refuses(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(RuntimeError) as ei:
        common.require_internal_secret()
    assert "INTERNAL_API_SECRET" in str(ei.value)


def test_the_literals_the_deploy_surfaces_shipped_are_refused(monkeypatch):
    """The regression, named: every one of these was a real default in a real deploy surface —
    docker-compose.yml, the helm chart Secret, lite's entrypoint, the dogfood env example."""
    for placeholder in ("vexa-internal-secret", "lite-internal-secret", "changeme", "CHANGE-ME"):
        _clear(monkeypatch)
        monkeypatch.setenv("INTERNAL_API_SECRET", placeholder)
        with pytest.raises(RuntimeError) as ei:
            common.require_internal_secret()
        assert "refusing to start" in str(ei.value)
