"""A configuration key the gateway does not read is named at every boot that still sets it, with the
release it changed in and the page that says what to do; it has no effect. Unset, the boot is silent."""
import inspect
import io

import pytest

from gateway import adapters
from gateway.changed import CHANGED_IN, COMPAT_REMOVED_IN, DOCS, warn_removed_keys_at_boot


def test_unset_prints_nothing():
    out = io.StringIO()
    assert warn_removed_keys_at_boot({}, stream=out) == [] and out.getvalue() == ""


@pytest.mark.parametrize("value", ["legacy", "strict"])
def test_a_set_scope_mode_is_named_as_removed(value):
    out = io.StringIO()
    assert warn_removed_keys_at_boot({"VEXA_WS_SCOPE_MODE": value}, stream=out) == ["VEXA_WS_SCOPE_MODE"]
    line = out.getvalue()
    assert "VEXA_WS_SCOPE_MODE is not read since 0.13.4" in line and DOCS in line and CHANGED_IN == "0.13.4"
    # what holds instead: 0.12's /ws until the named release, never a setting
    assert f"until {COMPAT_REMOVED_IN}" in line and COMPAT_REMOVED_IN == "0.13.6"


def test_the_production_boot_calls_it():
    assert "warn_removed_keys_at_boot()" in inspect.getsource(adapters.build_production_app)
