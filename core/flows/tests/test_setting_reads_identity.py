"""`setting(uid, key)` reads IDENTITY, not a file in the agent domain.

WHAT THIS REPLACES. `setting()` fetched `.settings.json` through
`GET {AGENT_API}/api/workspace/file` — flows reaching into the agent domain for a fact about a
PERSON. Under the independence ruling (2026-09-02) a domain's doors are identity, runtime and
itself, so that call is a violation twice over: it is flows→agent, and it means a deployment
without the agent domain has nobody with a timezone or a mail preference. Every mail this engine
sends is gated on one of these values, so "no agent domain" silently became "mail everybody
everything, in UTC".

`bot_name` left too, but not to here: it is a fact about the BOT, so it lives in the store meetings
already reads (`users.data.calendar_bot_name` → `/internal/users/{id}/bot-context`) and meeting-api
resolves it on every spawn path. No flow reads one any more.

No network: the identity edge is replaced at the seam, which is this suite's idiom.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows_steps.common as common  # noqa: E402


@pytest.fixture()
def identity(monkeypatch):
    """The identity edge, recorded. Returns the call log.

    The internal secret is set because a flows deployment without one genuinely cannot call the
    internal tier — `require_internal_secret` refusing is correct and is not weakened here."""
    monkeypatch.setenv("INTERNAL_API_SECRET", "s" * 40)
    calls = []

    def fake(method, url, headers=None, body=None, timeout=None):
        calls.append((method, url, dict(headers or {})))
        return 200, {"timezone": "Europe/Lisbon", "mail_minutes": False,
                     "mail_join": False, "mail_rsvp": True, "mail_prep": True}

    monkeypatch.setattr(common, "http", fake)
    monkeypatch.setattr(common, "_person_settings_cache", {}, raising=False)
    return calls


def test_a_setting_is_read_from_identity(identity):
    assert common.setting("57", "timezone") == "Europe/Lisbon"
    method, url, headers = identity[0]
    assert method == "GET"
    assert "/internal/users/57/settings" in url
    assert url.startswith(common.ADMIN_API), "flows must ask identity, not the gateway"
    assert "X-Internal-Secret" in headers or "X-Admin-API-Key" in headers


def test_no_person_fact_reaches_the_agent_domain(identity):
    """The whole point. A domain's doors are identity, runtime and itself."""
    for key in ("timezone", "mail_minutes", "mail_join", "mail_rsvp", "mail_prep"):
        common.setting("57", key)
    assert all(common.AGENT_API not in url for _m, url, _h in identity)


def test_a_bot_name_is_not_something_this_module_reads_at_all(identity, monkeypatch):
    """`bot_name` LEFT. A bot default is a fact about the bot, and meeting-api resolves it from
    identity's bot-context on every spawn path — so no flow reads one, from anywhere. Reading it
    here is what gave one fact three stores, and the same person's bot two different names."""
    seen = []
    monkeypatch.setattr(common, "ws_file", lambda uid, path, slug=None: seen.append(path) or None)
    assert common.setting("57", "bot_name") is None
    assert seen == [], "no workspace file is touched for a bot name"
    src = (Path(__file__).resolve().parents[1] / "src" / "flows_steps" / "meeting.py").read_text()
    assert '"bot_name"' not in src, "a step still names a bot; meetings decides that now"


def test_the_stored_value_wins_over_the_default(identity):
    assert common.setting("57", "mail_minutes") is False


def test_an_unreachable_identity_falls_back_to_the_documented_default(monkeypatch):
    """A mail preference that fails OPEN is a person who gets mail they turned off; one that fails
    CLOSED is a person who silently stops getting minutes. The default IS the documented answer, and
    it is what a person who has never touched a setting already gets."""
    monkeypatch.setenv("INTERNAL_API_SECRET", "s" * 40)
    monkeypatch.setattr(common, "_person_settings_cache", {}, raising=False)
    monkeypatch.setattr(common, "http", lambda *a, **k: (0, None))
    assert common.setting("57", "mail_minutes") is True
    assert common.setting("57", "timezone") == ""


def test_an_unknown_key_is_none_not_an_invention(identity):
    assert common.setting("57", "make_it_funnier") is None


def test_the_workspace_file_is_no_longer_read():
    """Asserted against the source: the `.settings.json` read is gone, not merely unused."""
    src = (Path(__file__).resolve().parents[1] / "src" / "flows_steps" / "common.py").read_text()
    body = src.split("def person_settings(")[1].split("\ndef ")[0]
    assert ".settings.json" not in body
    assert "ws_file(" not in body
