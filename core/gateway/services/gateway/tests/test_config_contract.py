"""#526 · config.v1 (ADR-0026) — gateway's declaration + boot preflight.

The gateway attaches INTERNAL_API_SECRET as X-Internal-Secret on the admin-api /internal/validate
hop; unset, admin-api rejects validation and EVERY API-key check 503s — yet the gateway was outside
the config-contract machinery, so it booted green. This pins the declaration + the boot preflight
that refuses a secretless deploy. Offline, stdlib only.
"""
from __future__ import annotations

import pytest

from gateway import config_preflight as cp


def test_declaration_loads_and_internal_secret_is_required():
    decl = cp.load_declaration()
    assert decl["service"] == "gateway"
    required = {k["key"] for k in decl["keys"] if k["class"] == "required-explicit"}
    assert "INTERNAL_API_SECRET" in required


def test_preflight_refuses_boot_without_internal_api_secret():
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({})
    assert "INTERNAL_API_SECRET" in str(ei.value)


def test_preflight_passes_when_required_set():
    cp.preflight({"INTERNAL_API_SECRET": "a-real-secret"})
