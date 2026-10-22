"""The 0.12 compatibility switches meeting-api reads: closed by default, and loud while one is open.

Each open switch is a residual (10-A ``VEXA_WS_SCOPE_MODE=legacy``, 10-B
``VEXA_WEBHOOK_STORED_URL_POLICY=v012``, 10-E ``VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S`` above 0), so
every boot with one open prints a banner naming it, what it re-opens, and the release that removes it.
"""
import io

import pytest

from meeting_api import compat_switches as cs


def test_a_default_boot_opens_nothing_and_prints_nothing():
    out = io.StringIO()
    assert cs.warn_at_boot({}, stream=out) == [] and out.getvalue() == ""


@pytest.mark.parametrize("env", [
    {"VEXA_WS_SCOPE_MODE": "strict", "VEXA_WEBHOOK_STORED_URL_POLICY": "strict",
     "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S": "0"},
    {"VEXA_WS_SCOPE_MODE": "bogus", "VEXA_WEBHOOK_STORED_URL_POLICY": "bogus",
     "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S": "junk"},
])
def test_closed_or_unknown_values_print_nothing(env):
    out = io.StringIO()
    assert cs.warn_at_boot(env, stream=out) == [] and out.getvalue() == ""


@pytest.mark.parametrize("key,value", [
    ("VEXA_WS_SCOPE_MODE", "legacy"),
    ("VEXA_WS_SCOPE_MODE", " Legacy "),
    ("VEXA_WEBHOOK_STORED_URL_POLICY", "v012"),
    ("VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S", "14400"),
])
def test_each_open_switch_is_printed_with_its_removal_release(key, value):
    out = io.StringIO()
    found = cs.warn_at_boot({key: value}, stream=out)
    banner = out.getvalue()
    assert [s["key"] for s in found] == [key]
    assert "WARNING" in banner and key in banner and "re-opens:" in banner
    assert "removed in 0.14.0" in banner and cs.REMOVED_IN == "0.14.0"
    assert len(banner.splitlines()) >= 6  # a banner, not a line


def test_all_three_open_are_all_named():
    env = {"VEXA_WS_SCOPE_MODE": "legacy", "VEXA_WEBHOOK_STORED_URL_POLICY": "v012",
           "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S": "600"}
    out = io.StringIO()
    assert len(cs.warn_at_boot(env, stream=out)) == 3
    assert "3 0.12 compatibility switch(es) OPEN" in out.getvalue()


def test_the_production_boot_calls_it():
    import inspect

    from meeting_api import __main__ as entry

    src = inspect.getsource(entry.build_production_app)
    assert "warn_at_boot()" in src
