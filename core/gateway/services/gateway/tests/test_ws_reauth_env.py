"""WS_REAUTH_INTERVAL_SEC is read from the environment (default 15 s), so prod can lengthen it without a rebuild."""
import importlib

import gateway.multiplex as mux


def test_default_is_15_seconds(monkeypatch):
    monkeypatch.delenv("WS_REAUTH_INTERVAL_SEC", raising=False)
    assert importlib.reload(mux).WS_REAUTH_INTERVAL_SEC == 15.0


def test_env_sets_the_interval(monkeypatch):
    monkeypatch.setenv("WS_REAUTH_INTERVAL_SEC", "120")
    try:
        assert importlib.reload(mux).WS_REAUTH_INTERVAL_SEC == 120.0
    finally:
        monkeypatch.delenv("WS_REAUTH_INTERVAL_SEC", raising=False)
        importlib.reload(mux)
