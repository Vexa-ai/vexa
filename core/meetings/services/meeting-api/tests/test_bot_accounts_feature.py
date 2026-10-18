"""browser_bots is an unproven feature: a release install (``VEXA_FEATURES`` empty) does not serve it.

Off, every public ``/bot-accounts…`` route answers 403 ``feature_disabled`` (the live-view socket is
closed 4403) and the ``/internal/…`` routes are untouched; ``all`` or ``browser_bots`` serves them.
The parse is agent-api's (``core/agent/control_plane/features.py`` ``install_defaults``), copied —
read here by path only.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from starlette.websockets import WebSocketDisconnect

from meeting_api.bot_accounts import feature
from test_bot_accounts import OWNER, Rig

AGENT_FEATURES = (pathlib.Path(__file__).resolve().parents[4] / "agent" / "control_plane" / "features.py")

PUBLIC = [
    ("GET", "/bot-accounts"),
    ("POST", "/bot-accounts/sessions"),
    ("GET", "/bot-accounts/sessions/s1"),
    ("DELETE", "/bot-accounts/sessions/s1"),
    ("GET", "/bot-accounts/sessions/s1/view/vnc.html"),
    ("POST", f"/bot-accounts/{'a' * 32}/verify"),
    ("DELETE", f"/bot-accounts/{'a' * 32}"),
]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "https://stt.example.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "tok-test")
    monkeypatch.setenv("ADMIN_TOKEN", "bot-accounts-test-admin-token")
    for k in ("BOT_AUTHENTICATED", "ADMIN_API_URL"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.mark.parametrize("raw,on", [
    (None, False), ("", False), ("all", True), ("browser_bots", True), ("crm, browser_bots", True),
    ("crm", False), ("all,-browser_bots", False), ("-browser_bots,all", True), ("nonsense", False),
])
def test_the_parse_is_agent_apis(raw, on):
    assert feature.switched_on(feature.FEATURE, {} if raw is None else {"VEXA_FEATURES": raw}) is on


def test_parity_with_the_registry_it_copies():
    if not AGENT_FEATURES.is_file():
        pytest.skip("agent-api is not in this tree")
    text = AGENT_FEATURES.read_text()
    assert re.search(r'^ENV = "VEXA_FEATURES"$', text, re.M)
    assert re.search(r'^REASON = "feature_disabled"$', text, re.M)
    block = re.search(r'"browser_bots": \{(.*?)\n    \}', text, re.S)
    assert block and '"scope": "install"' in block.group(1)
    assert feature.SUMMARY in block.group(1)


@pytest.mark.parametrize("method,path", PUBLIC)
def test_off_by_default_every_public_route_refuses(env, method, path):
    env.delenv("VEXA_FEATURES", raising=False)
    rig = Rig()
    r = rig.client.request(method, path, headers=rig.as_(OWNER), json={"platform": "google"})
    assert r.status_code == 403, r.text
    assert r.json() == {"detail": feature.refusal()}
    assert rig.runtime.specs == []          # nothing was spawned


def test_off_the_live_view_socket_closes_4403(env):
    env.setenv("VEXA_FEATURES", "")
    rig = Rig()
    with pytest.raises(WebSocketDisconnect) as e:
        with rig.client.websocket_connect("/bot-accounts/sessions/s1/vnc", headers=rig.as_(OWNER)):
            pass
    assert e.value.code == 4403


def test_off_the_internal_routes_are_not_the_feature(env):
    env.setenv("VEXA_FEATURES", "")
    rig = Rig()
    r = rig.client.put("/internal/bot-accounts/sessions/s1/profile",
                       headers={"Authorization": "Bearer nope"}, json={"files": []})
    assert r.status_code != 403 or r.json().get("detail", {}) != feature.refusal()


@pytest.mark.parametrize("raw", ["all", "browser_bots"])
def test_switched_on_it_is_served(env, raw):
    env.setenv("VEXA_FEATURES", raw)
    rig = Rig()
    r = rig.client.get("/bot-accounts", headers=rig.as_(OWNER))
    assert r.status_code == 200, r.text
    assert rig.open().status_code == 201
