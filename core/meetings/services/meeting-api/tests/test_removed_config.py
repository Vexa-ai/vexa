"""A configuration key meeting-api does not read is named at every boot that still sets it, with the
release it changed in and the page that says what to do; it has no effect. Unset, the boot is silent."""
from __future__ import annotations

import inspect
import io

import pytest

from meeting_api import removed_config as rc

KEYS = ("VEXA_WS_SCOPE_MODE", "VEXA_WEBHOOK_STORED_URL_POLICY", "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S")


def test_a_default_boot_prints_nothing():
    out = io.StringIO()
    assert rc.warn_at_boot({}, stream=out) == [] and out.getvalue() == ""


@pytest.mark.parametrize("key,value", [
    ("VEXA_WS_SCOPE_MODE", "legacy"),
    ("VEXA_WEBHOOK_STORED_URL_POLICY", "v012"),
    ("VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S", "14400"),
    ("VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S", "0"),
])
def test_each_set_key_is_named_with_the_release_and_the_page(key, value):
    out = io.StringIO()
    assert rc.warn_at_boot({key: value}, stream=out) == [key]
    line = out.getvalue()
    assert f"{key} is not read since 0.13.4" in line
    assert "https://docs.vexa.ai/changes-0-13-4-auth" in line


def test_the_kept_0_12_behaviours_name_their_removal_release():
    assert rc.COMPAT_REMOVED_IN == "0.13.6"
    for key in ("VEXA_WS_SCOPE_MODE", "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S"):
        assert f"until {rc.COMPAT_REMOVED_IN}" in rc.REMOVED_KEYS[key]


def test_all_three_set_are_all_named():
    assert rc.warn_at_boot({k: "x" for k in KEYS}, stream=io.StringIO()) == list(KEYS)
    assert set(rc.REMOVED_KEYS) == set(KEYS)


def test_the_production_boot_calls_it():
    from meeting_api import __main__ as entry

    src = inspect.getsource(entry.build_production_app)
    assert "from .removed_config import warn_at_boot" in src and "warn_at_boot()" in src
