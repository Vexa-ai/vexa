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
    # The identity DOOR must be named too. `ADMIN_API` is no longer a module constant with a
    # host-port default — flows-no-agents made it a lazy door that RAISES when
    # VEXA_FLOWS_ADMIN_API_URL is unset, on the grounds that a default names whatever else is
    # listening on this machine. Naming it here keeps that ruling intact instead of restoring a
    # default. (Until R-P7 the unset case was worse than a refusal: the f-string raised, a broad
    # `except` turned it into the defaults, and an UNCONFIGURED read was indistinguishable from an
    # unreachable one and from a real preference. There is no broad `except` in that function now.)
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api.test")
    calls = []

    def fake(method, url, headers=None, body=None, timeout=None):
        calls.append((method, url, dict(headers or {})))
        return 200, {"timezone": "Europe/Lisbon", "mail_minutes": False,
                     "mail_join": False, "mail_rsvp": True, "mail_prep": True}

    monkeypatch.setattr(common, "http", fake)
    common.forget_person_settings()
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


def test_an_unreachable_identity_is_a_retry_and_never_a_default(monkeypatch):
    """CHANGED DELIBERATELY (R-P7), and the old assertion is worth keeping in view: this used to
    require that an unreachable identity answer the DEFAULTS, on the argument that *"a mail
    preference that fails OPEN is a person who gets mail they turned off; one that fails CLOSED is
    a person who silently stops getting minutes"*.

    Both halves of that are true, and both answer the wrong question. A transport failure is not a
    fact about what somebody prefers, and the choice was never between the two — the third option
    is to not answer yet. A retryable failure mails nothing wrong now and mails the right thing in
    ten minutes, which is what `retryable` is for and what every other reach in this brick already
    does with an unreachable door.

    It mattered more than one wrong mail because the answer was CACHED with no expiry: a single
    refused connection during a rolling restart pinned that person to defaults for the life of the
    worker, while the identity row said something else the whole time."""
    monkeypatch.setenv("INTERNAL_API_SECRET", "s" * 40)
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api.test")
    common.forget_person_settings()
    monkeypatch.setattr(common, "http", lambda *a, **k: (0, None))
    with pytest.raises(common.SettingsUnavailable) as e:
        common.setting("57", "mail_minutes")
    assert e.value.retryable is True
    assert "57" in str(e.value)
    from flows import StepError
    assert isinstance(e.value, StepError), "an uncaught preference read still fails its step"


def test_a_failed_read_is_never_cached(monkeypatch):
    """THE HALF THAT OUTLIVED THE MAIL. The old shape cached whatever it decided, including the
    defaults it decided on a failure — so the wrong answer was permanent, not momentary."""
    monkeypatch.setenv("INTERNAL_API_SECRET", "s" * 40)
    monkeypatch.setenv("VEXA_FLOWS_ADMIN_API_URL", "http://admin-api.test")
    common.forget_person_settings()
    answers = [(503, {"detail": "identity restarting"}),
               (200, {"timezone": "Europe/Lisbon", "mail_minutes": False})]
    monkeypatch.setattr(common, "http", lambda *a, **k: answers.pop(0))
    with pytest.raises(common.SettingsUnavailable):
        common.setting("57", "timezone")
    assert common.setting("57", "timezone") == "Europe/Lisbon", "the failure was remembered"


def test_a_setting_identity_simply_does_not_hold_is_still_the_default(identity, monkeypatch):
    """The half a blanket refusal would break. Identity ANSWERING, with no value for a key, is a
    person who has never touched it — and the default is the documented answer for exactly that."""
    monkeypatch.setattr(common, "http", lambda *a, **k: (200, {"timezone": "UTC"}))
    common.forget_person_settings()
    assert common.setting("57", "mail_rsvp") is True
    assert common.setting("57", "mail_join") is False


# ── the cache has a lifetime (R-P7) ──────────────────────────────────────────────────────────────
def test_a_second_read_inside_the_window_does_not_ask_again(identity):
    """What the cache is FOR: a step reads two or three of these in a row, and they cannot change
    between two lines of the same function."""
    common.forget_person_settings()
    for _ in range(5):
        common.setting("57", "timezone")
    assert len(identity) == 1


def test_the_cache_expires_so_a_changed_preference_takes_effect(identity, monkeypatch):
    """THE REGRESSION. The entry had no expiry and nothing ever cleared it — its own comment said
    "for the length of one step" — so in a worker, which lives for days, the first answer for a uid
    was the last one that person ever got. Turning your minutes mail off did not take effect until
    a deploy, and nothing was visible anywhere: the cache is invisible and the identity row said
    the right thing the whole time."""
    common.forget_person_settings()
    t = [1_000_000.0]
    monkeypatch.setattr(common.time, "time", lambda: t[0])
    common.setting("57", "timezone")
    assert len(identity) == 1
    t[0] += common.SETTINGS_TTL_S / 2
    common.setting("57", "timezone")
    assert len(identity) == 1, "still inside the window"
    t[0] += common.SETTINGS_TTL_S
    common.setting("57", "timezone")
    assert len(identity) == 2, "the cached answer outlived its TTL and was asked again"


def test_the_ttl_is_short_enough_to_be_a_step_and_not_a_deployment():
    """A number, asserted, because the whole defect was a lifetime nobody had written down."""
    assert 1 <= common.SETTINGS_TTL_S <= 300


def test_an_unknown_key_is_none_not_an_invention(identity):
    assert common.setting("57", "make_it_funnier") is None


def test_the_workspace_file_is_no_longer_read():
    """Asserted against the source: the `.settings.json` read is gone, not merely unused."""
    src = (Path(__file__).resolve().parents[1] / "src" / "flows_steps" / "common.py").read_text()
    body = src.split("def person_settings(")[1].split("\ndef ")[0]
    assert ".settings.json" not in body
    assert "ws_file(" not in body
