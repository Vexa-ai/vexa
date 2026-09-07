"""F-D20 (b) — flows-api's boot refusal is the SHARED validator's, not a hand-rolled one.

`_require_api_key` carried its own refusal: `if not key: raise RuntimeError(...)` and
`if key in ("changeme", "change-me", "default", "secret"): raise RuntimeError(...)`. Meanwhile
`core/flows/src/config.v1.json` declares `VEXA_FLOWS_API_KEY` as `required-explicit` with
`forbidden_values` of SEVEN literals — the four above plus `vexa-internal-secret`,
`lite-internal-secret` and `CHANGE-ME`, which are the ones the deploy surfaces actually shipped.

So flows-api booted happily on `VEXA_FLOWS_API_KEY=vexa-internal-secret`: a literal published in
this repository, on the key that opens flow submission and activation. That is F95 exactly — *"a
refusal list written from imagination rather than from the compose file it was defending against"*
— and the reason a second, hand-maintained list exists at all is that nothing under
`core/flows/src` imported the vendored validator. `flows_config.py` said so in a comment, and
`tests/test_config_contract.py` drove `cp.preflight` directly while the running service never did.

The rows below are parametrized OFF THE DECLARATION, not off a list written here, so the two
cannot drift again: adding a literal to `config.v1.json` adds a test.
"""
from __future__ import annotations

import inspect
import os

import config_preflight as cp
import pytest

# IMPORTING THIS APP HAS SIDE EFFECTS — the same dance `tests/test_flows_api_service.py` does, and
# for the same reason it is written out there: `flows_api` reads its credentials and composes its
# database AT IMPORT, so getting it in here means setting environment process-wide, and leaving it
# set leaks into every other module in the session (`gate:test-isolation`).
_KEYS = ("VEXA_FLOWS_API_KEY", "VEXA_FLOWS_ADMIN_KEY", "INTERNAL_API_SECRET", "VEXA_FLOWS_DB_URL")
_PRIOR_ENV = {k: os.environ.get(k) for k in _KEYS}

os.environ.setdefault("VEXA_FLOWS_API_KEY", "test-flows-key")
# NEW HERE (F-D20 b), and it is the point of the change: the boot now runs the WHOLE declaration,
# so a test process that imports the app must name every required-explicit key, exactly as a
# deployment does. `VEXA_FLOWS_ADMIN_KEY` is otherwise supplied by an autouse fixture, which runs
# far too late — module import happens at collection.
os.environ.setdefault("VEXA_FLOWS_ADMIN_KEY", "test-admin-key-not-a-placeholder")
os.environ.setdefault("INTERNAL_API_SECRET", "test-internal-secret")
os.environ["VEXA_FLOWS_DB_URL"] = "postgresql+psycopg://key-gate:unreachable@127.0.0.1:1/flows"
try:
    from flows_integrations import flows_api  # noqa: E402
finally:
    for _k, _v in _PRIOR_ENV.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


def _declared(key: str) -> dict:
    return next(e for e in cp.load_declaration()["keys"] if e["key"] == key)


@pytest.fixture
def a_configured_deployment(monkeypatch):
    """Everything the declaration calls required-explicit, set — so each test below varies exactly
    one key and the refusal it reads is about that key."""
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "a-real-operator-key")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_KEY", "a-real-admin-key")
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-real-internal-secret")
    monkeypatch.setenv("VEXA_FLOWS_DB_URL",
                       "postgresql+psycopg://x:y@127.0.0.1:1/flows")
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api:8057")


def test_a_configured_deployment_gets_its_key_back(a_configured_deployment):
    assert flows_api._require_api_key() == "a-real-operator-key"


def test_an_unset_key_is_refused_with_the_shared_config_error(a_configured_deployment,
                                                              monkeypatch):
    monkeypatch.delenv("VEXA_FLOWS_API_KEY", raising=False)
    with pytest.raises(cp.ConfigError) as refused:
        flows_api._require_api_key()
    assert "VEXA_FLOWS_API_KEY" in str(refused.value)


@pytest.mark.parametrize("placeholder", _declared("VEXA_FLOWS_API_KEY")["forbidden_values"])
def test_every_declared_placeholder_is_refused(a_configured_deployment, monkeypatch, placeholder):
    """RED for `vexa-internal-secret`, `lite-internal-secret` and `CHANGE-ME` before this change:
    the hand-rolled tuple did not carry them, and they are the three a stock deploy actually
    supplies."""
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", placeholder)
    with pytest.raises(cp.ConfigError) as refused:
        flows_api._require_api_key()
    assert "VEXA_FLOWS_API_KEY" in str(refused.value)


def test_the_refusal_names_the_key_and_never_echoes_the_value(a_configured_deployment,
                                                              monkeypatch):
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "vexa-internal-secret")
    with pytest.raises(cp.ConfigError) as refused:
        flows_api._require_api_key()
    assert "vexa-internal-secret" not in str(refused.value)


def test_the_module_keeps_no_placeholder_list_of_its_own():
    """The structural guard under the rows above: a second list is how the first one drifted, and
    a list nobody can see drifting is the whole defect."""
    # THE BODY, NOT THE DOCSTRING. The docstring records what the literals WERE and why they were
    # wrong, which is the part of this fix most worth keeping; asserting over the whole source
    # would make writing that history impossible.
    body = inspect.getsource(flows_api._require_api_key).split('"""')[-1]
    for literal in ("changeme", "change-me", "default", "secret"):
        assert f'"{literal}"' not in body, f"{literal!r} is hard-coded again — the declaration owns it"


def test_the_boot_runs_the_shared_validator():
    """It is not enough that the refusal has the right TYPE: the point of routing through
    `config_preflight` is that flows-api boots against its own `config.v1.json`, which
    `gate:config-contract` already holds to the deploy surfaces in both directions."""
    assert "config_preflight" in inspect.getsource(flows_api).split("logger =")[0]
