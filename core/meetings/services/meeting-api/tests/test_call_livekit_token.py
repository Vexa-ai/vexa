"""A LiveKit access token: an HS256 JWT signed with the server's API secret (#1783, ADR-0048 d6).

The token says who joins (``sub``), which room (``video.room``), what they may do, and expires soon.
A participant's token publishes and subscribes; the listener's is hidden and subscribe-only; an
admin token (room service calls) names the room and grants nothing else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from meeting_api.calls.livekit import (
    LiveKitConfig,
    admin_token,
    listener_token,
    participant_token,
)

CFG = LiveKitConfig(url="wss://calls.example.test", api_key="APIkey1", api_secret="s" * 32)


def _decode(token: str, secret: str) -> dict:
    head, body, sig = token.split(".")
    want = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    got = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    assert hmac.compare_digest(want, got), "signature does not verify"
    assert json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4))) == {"alg": "HS256", "typ": "JWT"}
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_a_participant_token_joins_one_room_publishes_and_expires_soon():
    tok = participant_token(CFG, room="call-abc", identity="u:7", name="Ada", ttl_sec=600, now=1_000)
    claims = _decode(tok, CFG.api_secret)
    assert claims["iss"] == "APIkey1"
    assert claims["sub"] == "u:7"
    assert claims["name"] == "Ada"
    assert claims["nbf"] <= 1_000 < claims["exp"] == 1_600
    assert claims["video"] == {"room": "call-abc", "roomJoin": True, "canPublish": True,
                               "canSubscribe": True, "canPublishData": True}


def test_the_listener_is_hidden_and_cannot_publish():
    claims = _decode(listener_token(CFG, room="call-abc", ttl_sec=3600, now=1_000), CFG.api_secret)
    video = claims["video"]
    assert video["hidden"] is True and video["canSubscribe"] is True
    assert video["canPublish"] is False and video["canPublishData"] is False
    assert video["room"] == "call-abc"
    assert claims["sub"].startswith("vexa-listener")


def test_an_admin_token_is_scoped_to_its_room():
    claims = _decode(admin_token(CFG, room="call-abc", now=1_000), CFG.api_secret)
    assert claims["video"] == {"room": "call-abc", "roomAdmin": True, "roomCreate": True}
    assert claims["exp"] - claims["nbf"] <= 120


def test_a_short_secret_is_refused():
    with pytest.raises(ValueError):
        LiveKitConfig(url="wss://x.test", api_key="k", api_secret="short").validate()


def test_the_config_is_read_from_the_environment(monkeypatch):
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert LiveKitConfig.from_env() is None
    monkeypatch.setenv("LIVEKIT_URL", "wss://calls.example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIkey1")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 32)
    cfg = LiveKitConfig.from_env()
    assert cfg is not None and cfg.url == "wss://calls.example.test"
    # The server's own address for the listener and room calls defaults to the public one.
    assert cfg.internal_url == "wss://calls.example.test"
    monkeypatch.setenv("LIVEKIT_INTERNAL_URL", "ws://livekit:7880")
    assert LiveKitConfig.from_env().internal_url == "ws://livekit:7880"
    assert LiveKitConfig.from_env().http_url == "http://livekit:7880"
