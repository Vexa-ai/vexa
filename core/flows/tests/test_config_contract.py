"""config.v1 (ADR-0026) — `core/flows` adopted, and the two declarations held together.

Flows carried its OWN declaration long before this: `flows_config.DECLARED`, written to make the
adoption "a transcription when it happens" (its own docstring), with both directions asserted by
`test_config_declaration.py`. The adoption did not replace that table — the accessors
(`get`/`get_int`/`get_bool`) still refuse an undeclared name at the read, which is a thing a JSON
file cannot do — so the brick now has TWO declarations of the same fact, and two declarations of
one fact drift. That is exactly the shape F95 was (one secret, three names, three refusal lists).

So the file that would have been "the declaration conforms to the schema" is instead "the two
declarations are the same declaration", plus the boot-behaviour assertions the contract's shared
validator makes possible. Schema conformance itself is `gate:config-contract` check 1 and the
contract's own `validate.mjs`; it is not restated here.

WHAT THE SHARED PREFLIGHT IS AND IS NOT WIRED TO. `config_preflight.py` is vendored verbatim (the
gate enforces byte-equality) and is what makes the declaration machine-readable — capability
tri-states, forbidden-value refusals, `/health` rows. The flows entrypoints still call
`flows_config.preflight()`, which is door-scoped and older; putting a second boot validator in
front of a running deployment is a change to how it boots and belongs to whoever is changing that,
not to the adoption. Offline, stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import config_preflight as cp        # noqa: E402  — the vendored canonical validator
import flows_config as cfg           # noqa: E402  — the brick's own table


def _decl():
    return cp.load_declaration()


def test_the_declaration_is_this_service_and_loads():
    decl = _decl()
    assert decl["contract"] == "config.v1"
    assert decl["service"] == "flows-api"


def test_the_two_declarations_name_exactly_the_same_keys():
    """The drift check, and the reason this file exists. A key added to one table and not the
    other is a key the gate checks against a deploy surface and the accessors refuse at the read,
    or the reverse — both of which are found at 3am rather than in CI."""
    contract = {k["key"] for k in _decl()["keys"]}
    table = set(cfg.DECLARED)
    assert contract == table, {
        "in config.v1.json only": sorted(contract - table),
        "in flows_config.DECLARED only": sorted(table - contract),
    }


def test_the_two_declarations_agree_on_every_class_and_default():
    """Same key, same class, same default. The class is not decoration: it decides whether an
    unset key refuses the boot, and the two files must not answer that differently."""
    disagree = {}
    for k in _decl()["keys"]:
        cls, default, _why = cfg.DECLARED[k["key"]]
        want_default = None if k["class"] != "defaulted" else k.get("default")
        got_default = None if default is None else str(default)
        if k["class"] != cls or (k["class"] == "defaulted" and want_default != got_default):
            disagree[k["key"]] = {"config.v1.json": (k["class"], k.get("default")),
                                  "flows_config": (cls, default)}
    assert disagree == {}


def test_every_capability_key_names_a_declared_capability():
    decl = _decl()
    named = set(decl.get("capabilities", {}))
    for k in decl["keys"]:
        if k["class"] == "capability":
            assert k["capability"] in named, f"{k['key']} names capability {k['capability']!r}"


def test_the_no_agents_profile_is_configured_not_broken():
    """PRD 40.7 and decision 4 in one assertion. A deployment with no agent domain and no terminal
    adapter must read as two capabilities that are OFF — never as a service that cannot boot."""
    env = {"VEXA_FLOWS_API_KEY": "k", "VEXA_FLOWS_ADMIN_KEY": "a", "INTERNAL_API_SECRET": "s",
           "VEXA_FLOWS_ADMIN_API_URL": "http://admin-api:8001",
           "VEXA_FLOWS_GATEWAY_URL": "http://gateway:8000"}
    cp.preflight(env)                                  # must not raise
    assert cp.capability_state("agent_domain", env) == "not_configured"
    assert cp.capability_state("terminal_link", env) == "not_configured"
    assert cp.capability_state("mailbox", env) == "not_configured"


def test_the_boot_refuses_a_deployment_that_named_no_credential_and_no_door():
    with pytest.raises(cp.ConfigError) as refused:
        cp.preflight({})
    said = str(refused.value)
    for key in ("VEXA_FLOWS_API_KEY", "VEXA_FLOWS_ADMIN_KEY", "INTERNAL_API_SECRET",
                "VEXA_FLOWS_ADMIN_API_URL", "VEXA_FLOWS_GATEWAY_URL"):
        assert key in said, f"the refusal does not name {key}"
    for capability_key in ("VEXA_FLOWS_AGENT_API_URL", "VEXA_UI_URL", "VEXA_MAIL_ADDR"):
        assert capability_key not in said, \
            f"{capability_key} is a capability — its absence is a product, not a misconfiguration"


#  and  are on the refusal list too, but the refusal PROSE contains both
# words, so asserting they are not echoed cannot distinguish the value from the sentence. The three
# here are literals no message would otherwise say.
@pytest.mark.parametrize("placeholder", ["vexa-internal-secret", "lite-internal-secret", "changeme"])
def test_the_published_placeholders_are_refused_by_name_never_by_value(placeholder):
    """F95 — the failure `required-explicit` does NOT catch: the key was never unset, compose
    supplied a literal from this public repository, and the deployment came up green sharing one
    secret with every reader of the source. `flows_steps.common` already refuses these three
    credentials at their use sites; the declaration is where the shared boot validator learns the
    same list, so the two cannot drift apart."""
    env = {"VEXA_FLOWS_API_KEY": "k", "VEXA_FLOWS_ADMIN_KEY": "a",
           "INTERNAL_API_SECRET": placeholder,
           "VEXA_FLOWS_ADMIN_API_URL": "http://admin-api:8001",
           "VEXA_FLOWS_GATEWAY_URL": "http://gateway:8000"}
    with pytest.raises(cp.ConfigError) as refused:
        cp.preflight(env)
    assert "INTERNAL_API_SECRET" in str(refused.value)
    assert placeholder not in str(refused.value), "a refusal must never echo the value"


def test_the_placeholder_list_is_the_same_list_the_use_sites_refuse():
    from flows_steps import common
    for k in _decl()["keys"]:
        if k["key"] in ("INTERNAL_API_SECRET", "VEXA_FLOWS_ADMIN_KEY", "VEXA_FLOWS_API_KEY"):
            assert set(k["forbidden_values"]) == set(common.PLACEHOLDER_SECRETS), k["key"]
