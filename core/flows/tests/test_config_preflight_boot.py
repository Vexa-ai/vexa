"""THE VENDORED CONFIG VALIDATOR IS ACTUALLY CALLED — review E6 (P14/P9, ADR-0026).

`core/flows/src/config_preflight.py` is byte-identical to `deploy/contracts/config.v1/preflight.py`
and gate-checked as such, and until this change NOTHING under `core/flows/src` imported it: the
validator every adopted service runs at boot was dead code in the one service that declares 35
keys. The sibling MCP does call it (`vexa_mcp/app.py`: `from .config_preflight import preflight;
preflight()`).

What flows checked instead was `flows_config.preflight()` over `DOOR_KEYS` — a different and much
narrower question (can this deployment NAME its doors), which is flows' own rule and is not in the
declaration. Both now run, in both entrypoints.

THE DRIFT THAT PROVES IT MATTERED: the declaration forbids SEVEN placeholder literals per secret
key; `flows_api.py` carried its own hand-typed list of FOUR. So `VEXA_FLOWS_API_KEY` set to
`vexa-internal-secret` — a literal published in this repository, therefore not a secret, presentable
by anyone who can read the source — booted green. And `VEXA_FLOWS_TIMELINE_KEY` declared no
forbidden values at all and was silently coerced to `""`, which reads as prudent and is not: the
operator got no error and no effect.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

import config_preflight
import flows_config

# The app module is a singleton that refuses to compose without its credentials, so this file
# supplies them around the import exactly as `test_health.py` and `test_subject_bearer.py` do —
# whichever module imports first wins, and these tests read constants back off the LIVE module.
_ENV = {"VEXA_FLOWS_API_KEY": "test-flows-key-preflight",
        "INTERNAL_API_SECRET": "test-internal-secret",
        "VEXA_FLOWS_DB_URL": "postgresql+psycopg://preflight:unreachable@127.0.0.1:1/flows"}
_saved = {k: os.environ.get(k) for k in _ENV}
os.environ.update(_ENV)
try:
    from flows_integrations import flows_api  # noqa: E402
finally:
    for _k, _v in _saved.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


def _src(name: str) -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "src" / name).read_text(encoding="utf-8")


def _declaration() -> dict:
    return json.loads(_src("config.v1.json"))


#: A complete, honest environment for this service — every `required-explicit` key set to something
#: that is not a placeholder. Each test breaks exactly one thing in a copy of it.
def _good_env() -> dict:
    env = {}
    for entry in _declaration()["keys"]:
        if entry.get("class") == "required-explicit":
            env[entry["key"]] = f"real-{entry['key'].lower().replace('_', '-')}"
    env["VEXA_FLOWS_DB_URL"] = "postgresql+psycopg://flows:pw@db:5432/flows"
    env["VEXA_FLOWS_ADMIN_API_URL"] = "http://admin-api:8057"
    return env


def test_the_environment_this_file_calls_good_actually_passes():
    """The control. Without it every refusal below could be passing for the wrong reason."""
    out = config_preflight.preflight(_good_env())
    assert out["service"] == "flows-api"


def test_the_declaration_forbids_seven_placeholders_on_every_secret_key():
    """The list this service's code used to re-type as four."""
    for entry in _declaration()["keys"]:
        if entry.get("secret") and entry.get("class") == "required-explicit":
            assert len(entry["forbidden_values"]) == 7, entry["key"]
            assert "vexa-internal-secret" in entry["forbidden_values"]


@pytest.mark.parametrize("placeholder", ["vexa-internal-secret", "lite-internal-secret",
                                         "changeme", "change-me", "CHANGE-ME", "default",
                                         "secret"])
def test_an_operator_key_on_a_published_literal_refuses_to_boot(placeholder):
    """E6's headline: `VEXA_FLOWS_API_KEY=vexa-internal-secret` used to boot green, and the control
    MCP is public and forwards to this service with whatever that key is."""
    env = _good_env() | {"VEXA_FLOWS_API_KEY": placeholder}
    with pytest.raises(config_preflight.ConfigError) as e:
        config_preflight.preflight(env)
    assert "VEXA_FLOWS_API_KEY" in str(e.value)
    # NEVER ECHOED. Checked on the literals that are distinctive enough for the check to mean
    # something — `secret` and `default` are ordinary English and appear in the refusal's own prose.
    if placeholder not in ("secret", "default"):
        assert placeholder not in str(e.value), "the refusal echoed the value"


def test_the_timeline_key_is_declared_with_forbidden_values_too():
    """It had none — the one secret-class key the placeholder rule did not cover."""
    entry = next(e for e in _declaration()["keys"] if e["key"] == "VEXA_FLOWS_TIMELINE_KEY")
    assert entry.get("secret") is True
    assert set(entry["forbidden_values"]) == {"vexa-internal-secret", "lite-internal-secret",
                                              "changeme", "change-me", "CHANGE-ME", "default",
                                              "secret"}


def test_a_placeholder_timeline_key_refuses_to_boot():
    env = _good_env() | {"VEXA_FLOWS_TIMELINE_KEY": "changeme"}
    with pytest.raises(config_preflight.ConfigError) as e:
        config_preflight.preflight(env)
    assert "VEXA_FLOWS_TIMELINE_KEY" in str(e.value)


def test_a_missing_required_key_names_every_one_that_is_missing_at_once():
    """One actionable message, not a peel-the-onion loop — the property the vendored validator has
    and `flows_config.preflight()`'s door check has only for doors."""
    env = _good_env()
    env.pop("VEXA_FLOWS_DB_URL")
    env.pop("INTERNAL_API_SECRET")
    with pytest.raises(config_preflight.ConfigError) as e:
        config_preflight.preflight(env)
    assert "VEXA_FLOWS_DB_URL" in str(e.value) and "INTERNAL_API_SECRET" in str(e.value)


# ── the call sites: both entrypoints, and the door check kept alongside ─────────────────────────

def test_the_flows_api_entrypoint_calls_the_vendored_preflight():
    assert callable(flows_api.contract_preflight)
    body = _src("flows_integrations/flows_api.py").split("def main(", 1)[1]
    assert "contract_preflight()" in body.split("\ndef ", 1)[0], (
        "flows-api's entrypoint does not run the config.v1 contract check")


def test_the_worker_entrypoint_calls_the_vendored_preflight():
    body = _src("flows_worker/__main__.py")
    assert "from config_preflight import preflight" in body
    assert "contract_preflight()" in body


def test_both_entrypoints_still_run_the_door_check():
    """The contract check does NOT replace it: `flows_config.preflight()` refuses a deployment that
    cannot NAME a door, which is flows' own rule (no host-port defaults: `http://localhost:18057` is
    a different deployment's admin-api on any host running two stacks) and is not in the
    declaration. Two questions, both asked."""
    assert "flows_config.preflight()" in _src("flows_integrations/flows_api.py")
    assert "flows_config.preflight()" in _src("flows_worker/__main__.py")
    with pytest.raises(flows_config.ConfigError):
        import os
        saved = os.environ.pop("VEXA_FLOWS_ADMIN_API_URL", None)
        try:
            flows_config.preflight()
        finally:
            if saved is not None:
                os.environ["VEXA_FLOWS_ADMIN_API_URL"] = saved


def test_the_running_code_reads_the_declaration_rather_than_re_typing_it():
    """The drift is closed BY CONSTRUCTION, which is the half a boot-time call alone would not give:
    `flows_api`'s own import-time refusal now reads `forbidden_values` off the declaration instead
    of carrying a second, shorter copy of it."""
    declared = set(_declaration()["keys"][0].get("forbidden_values") or ()) or None
    for entry in _declaration()["keys"]:
        if entry["key"] == "VEXA_FLOWS_API_KEY":
            declared = set(entry["forbidden_values"])
    assert set(flows_api._forbidden_values("VEXA_FLOWS_API_KEY")) == declared
    assert set(flows_api._forbidden_values("VEXA_FLOWS_TIMELINE_KEY")) == declared


def test_the_operator_key_refusal_covers_every_declared_placeholder():
    """THE REFUSAL IS THE SHARED VALIDATOR'S, and this asserts that rather than a wording.

    This test was written against a hand-rolled per-key check inside `_require_api_key` that
    carried its own placeholder list. F-D20 (b) deleted that: `_require_api_key` now calls
    `config_preflight.preflight()`, which validates the WHOLE declaration — so there is exactly one
    forbidden-values list in the tree, the one `gate:config-contract` reads, and it cannot drift
    from a second copy because there is no second copy. `tests/test_flows_api_key_refusal.py` drives
    the refusal itself, parametrized off the declaration.

    What remains worth asserting here is the property that made the duplicate list dangerous: every
    literal the declaration forbids for the operator key is a literal the validator actually
    refuses, and the set is not narrower than the declaration."""
    declared = set(flows_api._forbidden_values("VEXA_FLOWS_API_KEY"))
    assert declared, "the declaration forbids nothing for the operator key"
    entry = next(e for e in _declaration()["keys"] if e["key"] == "VEXA_FLOWS_API_KEY")
    assert declared == set(entry["forbidden_values"]), (
        "the refusal list read at runtime is not the one the declaration states")
    # A COMPLETE deployment environment, varying exactly one key — otherwise the validator reports
    # whichever required key the test process happens to lack and says nothing about this one.
    base = {e["key"]: f"a-real-{e['key'].lower()}" for e in _declaration()["keys"]
            if e.get("class") == "required-explicit"}
    for placeholder in sorted(declared):
        with pytest.raises(config_preflight.ConfigError) as refused:
            config_preflight.preflight(env={**base, "VEXA_FLOWS_API_KEY": placeholder})
        assert "VEXA_FLOWS_API_KEY" in str(refused.value), (
            f"the shared validator did not refuse {placeholder!r} for VEXA_FLOWS_API_KEY")
