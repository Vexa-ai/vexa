"""A configuration key meeting-api does not read is named at every boot that still sets it, with the
release it changed in and the page that says what to do; it has no effect. Unset, the boot is silent."""
from __future__ import annotations

import inspect
import io

import pytest

from meeting_api import removed_config as rc

KEYS = ("VEXA_WS_SCOPE_MODE", "VEXA_WEBHOOK_STORED_URL_POLICY", "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S",
        # ADR-0052 §3 (s2d): the auto-join sweep's own settings; Flows' booked_bot is the clock
        "AUTO_JOIN_SWEEP_INTERVAL_S", "AUTO_JOIN_LEAD_S", "AUTO_JOIN_GRACE_S")


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


def test_all_set_are_all_named():
    assert rc.warn_at_boot({k: "x" for k in KEYS}, stream=io.StringIO()) == list(KEYS)
    assert set(rc.REMOVED_KEYS) == set(KEYS)


def test_the_production_boot_calls_it():
    from meeting_api import __main__ as entry

    src = inspect.getsource(entry.build_production_app)
    assert "from .removed_config import warn_at_boot" in src and "warn_at_boot()" in src


def test_the_sweeps_keys_name_what_holds_instead():
    """ADR-0052 §3: each removed sweep key names its replacement, and none is read any more."""
    from pathlib import Path

    assert "BOOKED_POD_LEAD_S" in rc.REMOVED_KEYS["AUTO_JOIN_LEAD_S"]
    assert "BOOKED_JOIN_LEAD_S" in rc.REMOVED_KEYS["AUTO_JOIN_LEAD_S"]
    assert "BOOKED_GRACE_S" in rc.REMOVED_KEYS["AUTO_JOIN_GRACE_S"]
    assert "booked_bot" in rc.REMOVED_KEYS["AUTO_JOIN_SWEEP_INTERVAL_S"]
    src = Path(rc.__file__).parent
    for path in src.rglob("*.py"):
        if path.name == "removed_config.py":
            continue
        text = path.read_text()
        for key in ("AUTO_JOIN_SWEEP_INTERVAL_S", "AUTO_JOIN_LEAD_S", "AUTO_JOIN_GRACE_S"):
            assert f'"{key}"' not in text, f"{path.name} still reads {key}"
