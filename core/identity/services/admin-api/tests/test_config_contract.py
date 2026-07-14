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
