"""#526 · config.v1 (ADR-0026) — admin-api's declaration + boot preflight.

admin-api carries the fail-closed INTERNAL_API_SECRET guard (the /internal/validate endpoint), but
was OUTSIDE the config-contract machinery: a deploy missing the secret booted green and 503'd every
gateway validation hop — the 2026-04-23 shape (23 meetings failed while monitors stayed green).
This pins the declaration + the boot preflight that refuses that deploy. Offline, stdlib only.
"""
from __future__ import annotations

import pytest

from admin_api import config_preflight as cp


def test_declaration_loads_and_internal_secret_is_required():
    decl = cp.load_declaration()
    assert decl["service"] == "admin-api"
    required = {k["key"] for k in decl["keys"] if k["class"] == "required-explicit"}
    assert "INTERNAL_API_SECRET" in required, "the fail-closed guard's key must refuse a secretless boot"


def test_preflight_refuses_boot_without_internal_api_secret():
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({})  # a deploy that forgot the secret
    assert "INTERNAL_API_SECRET" in str(ei.value)


def test_preflight_passes_when_required_set():
    # defaulted keys (DB_*, ADMIN_API_TOKEN, LOG_LEVEL, …) never block; only the required one matters.
    cp.preflight({"INTERNAL_API_SECRET": "a-real-secret"})


def test_db_pool_keys_declared_defaulted():
    # #635: DB_POOL_SIZE / DB_MAX_OVERFLOW are read in __main__ (an env read scanned by
    # gate:config-contract), so they must be declared — class defaulted, defaults 5/10 matching
    # deploy/db-budget.json. (Contract leg of the config triangle for the values db-budget audits.)
    decl = cp.load_declaration()
    by_key = {k["key"]: k for k in decl["keys"]}
    for key, default in (("DB_POOL_SIZE", "5"), ("DB_MAX_OVERFLOW", "10")):
        assert key in by_key, f"{key} must be declared (it is read in __main__)"
        assert by_key[key]["class"] == "defaulted"
        assert by_key[key]["default"] == default


def test_preflight_refuses_the_published_placeholder():
    """F95 — the failure a required-explicit key does NOT catch.

    `INTERNAL_API_SECRET` was never unset on a stock deploy: compose supplied
    `${INTERNAL_API_SECRET:-vexa-internal-secret}`, a literal in a public repository and the exact
    value the internal tier compared against. The boot was green, the preflight was satisfied, and
    the internal tier was open to anyone who had read the source. A set placeholder is not a
    configured deployment, so it refuses the same way an unset one does — and the message names the
    KEY, never the value."""
    for placeholder in ("vexa-internal-secret", "lite-internal-secret", "changeme"):
        with pytest.raises(cp.ConfigError) as ei:
            cp.preflight({**{}, "INTERNAL_API_SECRET": placeholder})
        assert "INTERNAL_API_SECRET" in str(ei.value)
        assert placeholder not in str(ei.value), "a refusal must never echo the value"


def test_the_flows_publish_edge_is_declared_and_never_blocks_the_boot():
    """PRD decision 42 item 2 — A PUBLISH EDGE IS NOT A DEPENDENCY, proven at the boot layer.

    admin-api reads FLOWS_API_URL and VEXA_FLOWS_API_KEY to hand `onboarding.completed` to flows.
    Every env read must be declared (check 5 of gate:config-contract), and the three classes that
    existed before this all describe a value the service NEEDS: required-explicit refuses the boot
    without it, defaulted supplies one, capability gates endpoints on it. Declaring a publish
    target as any of them asserts that the publisher depends on the consumer — the one thing it
    must not do, and the reason identity can be the domain everyone else depends on.

    So the class says the true thing instead, and the consequence is stated here rather than left
    to be inferred from the preflight's silence: a deployment that runs no flows domain boots
    exactly as one that does. The facts are dropped; nothing else changes."""
    decl = cp.load_declaration()
    by_key = {k["key"]: k for k in decl["keys"]}
    edge = by_key.get("FLOWS_API_URL")
    assert edge, "FLOWS_API_URL is read in app/events.py and must be declared"
    assert edge["class"] == "publish-edge"
    assert edge["publishes_events"] == ["onboarding.completed"]
    assert "default" not in edge, "a fallback address to publish to, invented by us — absent means absent"
    assert by_key["VEXA_FLOWS_API_KEY"]["secret"] is True

    # The boot with nothing but the one genuinely-required secret. No flows, no key, no error.
    cp.preflight({"INTERNAL_API_SECRET": "a-real-secret"})

    required = {k["key"] for k in decl["keys"] if k["class"] == "required-explicit"}
    assert not ({"FLOWS_API_URL", "VEXA_FLOWS_API_KEY"} & required), \
        "a publish edge became a boot requirement — identity would now depend on flows"
