"""10-A: ``VEXA_WS_SCOPE_MODE=legacy`` is an opt-in residual, so every gateway boot with it open prints
a banner naming it, what it re-opens, and the release that removes it. Closed, the boot is silent."""
import inspect
import io

import pytest

from gateway import adapters
from gateway.multiplex import WS_SCOPE_LEGACY_REMOVED_IN, warn_ws_scope_legacy_at_boot


@pytest.mark.parametrize("env", [{}, {"VEXA_WS_SCOPE_MODE": "strict"}, {"VEXA_WS_SCOPE_MODE": "bogus"}])
def test_closed_prints_nothing(env):
    out = io.StringIO()
    assert warn_ws_scope_legacy_at_boot(env, stream=out) is False and out.getvalue() == ""


@pytest.mark.parametrize("value", ["legacy", " LEGACY "])
def test_legacy_prints_a_banner_with_its_removal_release(value):
    out = io.StringIO()
    assert warn_ws_scope_legacy_at_boot({"VEXA_WS_SCOPE_MODE": value}, stream=out) is True
    banner = out.getvalue()
    assert "WARNING" in banner and "VEXA_WS_SCOPE_MODE=legacy" in banner and "bot-only" in banner
    assert f"removed in {WS_SCOPE_LEGACY_REMOVED_IN}" in banner and WS_SCOPE_LEGACY_REMOVED_IN == "0.14.0"
    assert len(banner.splitlines()) >= 6


def test_the_production_boot_calls_it():
    assert "warn_ws_scope_legacy_at_boot()" in inspect.getsource(adapters.build_production_app)
