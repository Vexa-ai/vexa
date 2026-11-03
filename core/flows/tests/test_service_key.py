"""The flows key: ONE name, no default, and the placeholders that shipped.

`VEXA_FLOWS_SERVICE_KEY` is the flows tier's one credential into the other domains — it opens the
gateway's service door and nothing else. It is refused the way the internal-tier secret it replaced
was: unset stops the process, and every literal a deploy surface ever shipped for a secret is
refused by name, because each of those is published in the OSS repository and authenticates
nobody and everybody.

A refusal list written from imagination rather than from the compose file it defends against was
the F95 finding, so these tests pin the list to the literals that were really there.
Offline, stdlib only.
"""
from __future__ import annotations

import pytest

from flows_steps import common


def _clear(monkeypatch):
    monkeypatch.delenv(common.SERVICE_KEY_ENV, raising=False)


def test_the_name_is_the_flows_service_key():
    assert common.SERVICE_KEY_ENV == "VEXA_FLOWS_SERVICE_KEY"
    assert common.SERVICE_KEY_HEADER == "X-Vexa-Service-Key"


def test_it_reads_the_name(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "a-real-flows-key")
    assert common.require_service_key() == "a-real-flows-key"
    assert common.service_key() == "a-real-flows-key"


def test_the_read_never_refuses_and_the_refusal_is_separate(monkeypatch):
    """`service_key()` is the READ a module importable without a deployment uses; it answers ""
    rather than raising. The refusal is `require_service_key()`'s alone."""
    _clear(monkeypatch)
    assert common.service_key() == ""
    with pytest.raises(RuntimeError):
        common.require_service_key()


def test_unset_refuses(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(RuntimeError) as ei:
        common.require_service_key()
    assert "VEXA_FLOWS_SERVICE_KEY" in str(ei.value)


def test_blank_refuses_like_unset(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "   ")
    with pytest.raises(RuntimeError) as ei:
        common.require_service_key()
    assert "unset" in str(ei.value)


def test_the_literals_the_deploy_surfaces_shipped_are_refused(monkeypatch):
    """The regression, named: every one of these was a real default in a real deploy surface —
    docker-compose.yml, the helm chart Secret, lite's entrypoint, the dogfood env example."""
    for placeholder in ("vexa-internal-secret", "lite-internal-secret", "changeme", "CHANGE-ME"):
        _clear(monkeypatch)
        monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", placeholder)
        with pytest.raises(RuntimeError) as ei:
            common.require_service_key()
        assert "refusing to start" in str(ei.value)


@pytest.mark.parametrize("placeholder", list(common.PLACEHOLDER_SECRETS))
def test_every_placeholder_is_refused_and_never_echoed_beyond_its_name(monkeypatch, placeholder):
    _clear(monkeypatch)
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", placeholder)
    with pytest.raises(RuntimeError) as ei:
        common.require_service_key()
    assert "VEXA_FLOWS_SERVICE_KEY" in str(ei.value)


def test_a_real_value_never_appears_in_a_refusal(monkeypatch):
    """The refusals name the VARIABLE, never a value: an unset key has none to print, and a real one
    is never in an error because a real one is not refused."""
    _clear(monkeypatch)
    with pytest.raises(RuntimeError) as ei:
        common.door_headers("126")
    assert "VEXA_FLOWS_SERVICE_KEY" in str(ei.value)
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "k-9f2c-a-real-value-0001")
    assert common.door_headers("126") == {"X-Vexa-Service-Key": "k-9f2c-a-real-value-0001",
                                          "X-User-Id": "126"}


def test_door_headers_name_the_subject_only_when_there_is_one(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VEXA_FLOWS_SERVICE_KEY", "a-real-flows-key")
    assert common.door_headers() == {"X-Vexa-Service-Key": "a-real-flows-key"}
    assert common.door_headers("") == {"X-Vexa-Service-Key": "a-real-flows-key"}
    assert common.door_headers(42) == {"X-Vexa-Service-Key": "a-real-flows-key", "X-User-Id": "42"}
