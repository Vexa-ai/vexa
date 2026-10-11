"""The deployment default for the captured-signal tape — the last tier of the resolver.

user ``diagnostics.capture_signal`` > platform setting > ``VEXA_CAPTURE_SIGNAL_DEFAULT``. The Helm
chart sets the last tier ``false`` so an enterprise install tapes nothing unless an operator says
so; compose and Lite leave it unset and keep tapes ON. Pure: no docker, no DB.
"""
import pytest

from admin_api.app.main import _resolve_capture_signal, capture_signal_default


def test_the_deployment_default_off_means_nothing_is_taped_when_nothing_is_configured(monkeypatch):
    # Deny: the old resolver returned True here whatever the deployment said.
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "false")
    assert _resolve_capture_signal({}, {}) is False
    assert _resolve_capture_signal({"diagnostics": {}}, {"capture_signal": ""}) is False
    # An unrecognized stored value falls through to the deployment default, not to ON.
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": "flase"}}, {}) is False


def test_explicit_settings_still_outrank_the_deployment_default(monkeypatch):
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "false")
    assert _resolve_capture_signal({}, {"capture_signal": "true"}) is True
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": "true"}}, {}) is True
    monkeypatch.setenv("VEXA_CAPTURE_SIGNAL_DEFAULT", "true")
    assert _resolve_capture_signal({}, {"capture_signal": "false"}) is False


def test_unset_keeps_the_compose_and_lite_default_on(monkeypatch):
    monkeypatch.delenv("VEXA_CAPTURE_SIGNAL_DEFAULT", raising=False)
    assert _resolve_capture_signal({}, {}) is True


@pytest.mark.parametrize("raw,expected", [("", True), ("true", True), ("on", True),
                                          ("false", False), ("0", False), ("OFF", False)])
def test_the_switch_reads_the_boolean_vocabulary(raw, expected):
    assert capture_signal_default(raw) is expected


@pytest.mark.parametrize("raw", ["flase", "disabled"])
def test_a_typo_in_the_switch_is_refused(raw):
    with pytest.raises(ValueError, match="VEXA_CAPTURE_SIGNAL_DEFAULT"):
        capture_signal_default(raw)
