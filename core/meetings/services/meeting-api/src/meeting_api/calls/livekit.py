"""The LiveKit server's two doors, as meeting-api uses them (#1783, ADR-0048 decision 6).

* **Access tokens.** A LiveKit access token is an HS256 JWT signed with the server's API secret:
  ``iss`` the API key, ``sub`` the participant identity, ``video`` the grant. Minted here with the
  standard library, short-lived, for exactly one room.
* **Room service.** Twirp over HTTP (``POST /twirp/livekit.RoomService/<Method>``, JSON body,
  ``Authorization: Bearer <admin token>``): create a room with its metadata (the transcription
  notice), list who is in it, delete it when the call ends.

The server itself is an optional deployable (compose profile ``calls``, Helm ``livekit.enabled``).
Without ``LIVEKIT_URL`` / ``LIVEKIT_API_KEY`` / ``LIVEKIT_API_SECRET`` a deployment has no calls, and
the call routes answer ``503 calls_not_deployed``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

#: LiveKit refuses an API secret shorter than 32 characters; so do we, at boot.
MIN_SECRET_LEN = 32
#: A participant's token: long enough to join and reconnect inside one call, no longer.
DEFAULT_PARTICIPANT_TTL_SEC = 600
#: An admin token exists for one room-service call.
ADMIN_TTL_SEC = 60
LISTENER_IDENTITY = "vexa-listener"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(claims: dict, secret: str) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode("utf-8"), f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


@dataclass(frozen=True)
class LiveKitConfig:
    """Where the server is and the key pair that signs for it.

    ``url`` is what a browser connects to (the public ``wss://`` address); ``internal_url`` is what
    meeting-api and the listener use inside the deployment (defaults to ``url``)."""

    url: str
    api_key: str
    api_secret: str
    internal_url_override: Optional[str] = None

    @property
    def internal_url(self) -> str:
        return self.internal_url_override or self.url

    @property
    def http_url(self) -> str:
        """The room service's HTTP base: the internal address with ``ws`` → ``http``."""
        u = self.internal_url.rstrip("/")
        if u.startswith("wss://"):
            return "https://" + u[len("wss://"):]
        if u.startswith("ws://"):
            return "http://" + u[len("ws://"):]
        return u

    def validate(self) -> "LiveKitConfig":
        if not self.url.startswith(("ws://", "wss://")):
            raise ValueError("LIVEKIT_URL must be a ws:// or wss:// address")
        if not self.api_key:
            raise ValueError("LIVEKIT_API_KEY is empty")
        if len(self.api_secret) < MIN_SECRET_LEN:
            raise ValueError(f"LIVEKIT_API_SECRET must be at least {MIN_SECRET_LEN} characters")
        return self

    @classmethod
    def from_env(cls) -> Optional["LiveKitConfig"]:
        """The deployment's LiveKit, or None when calls are not deployed (no LIVEKIT_URL). A URL
        with a missing or short key is a boot refusal, not a silent absence."""
        url = (os.getenv("LIVEKIT_URL") or "").strip()
        if not url:
            return None
        return cls(
            url=url,
            api_key=(os.getenv("LIVEKIT_API_KEY") or "").strip(),
            api_secret=(os.getenv("LIVEKIT_API_SECRET") or "").strip(),
            internal_url_override=(os.getenv("LIVEKIT_INTERNAL_URL") or "").strip() or None,
        ).validate()


def _claims(cfg: LiveKitConfig, *, identity: str, video: dict, ttl_sec: int, now: Optional[float],
            name: Optional[str] = None, metadata: Optional[str] = None) -> dict:
    t = int(time.time() if now is None else now)
    claims: dict[str, Any] = {"iss": cfg.api_key, "sub": identity, "nbf": t, "exp": t + int(ttl_sec),
                              "video": video}
    if name:
        claims["name"] = name
    if metadata:
        claims["metadata"] = metadata
    return claims


def participant_token(cfg: LiveKitConfig, *, room: str, identity: str, name: Optional[str] = None,
                      ttl_sec: int = DEFAULT_PARTICIPANT_TTL_SEC, now: Optional[float] = None,
                      metadata: Optional[str] = None) -> str:
    """A person's token: join this one room, publish (audio, video), subscribe, send data."""
    video = {"room": room, "roomJoin": True, "canPublish": True, "canSubscribe": True,
             "canPublishData": True}
    return _sign(_claims(cfg, identity=identity, video=video, ttl_sec=ttl_sec, now=now, name=name,
                         metadata=metadata), cfg.api_secret)


def listener_token(cfg: LiveKitConfig, *, room: str, ttl_sec: int, now: Optional[float] = None) -> str:
    """The transcription listener's token: hidden from the participant list, subscribe only."""
    video = {"room": room, "roomJoin": True, "hidden": True, "canSubscribe": True,
             "canPublish": False, "canPublishData": False}
    return _sign(_claims(cfg, identity=f"{LISTENER_IDENTITY}-{room}", video=video, ttl_sec=ttl_sec,
                         now=now, name="Vexa transcription"), cfg.api_secret)


def presenter_token(cfg: LiveKitConfig, *, room: str, name: str, ttl_sec: int = DEFAULT_PARTICIPANT_TTL_SEC,
                    now: Optional[float] = None) -> str:
    """A demo call's scripted speaker (``POST /demo-meetings``): join this one room and publish audio;
    hears nothing and sends no data. Minted with the deployment's own LiveKit key, never a person's."""
    video = {"room": room, "roomJoin": True, "canPublish": True, "canPublishSources": ["microphone"],
             "canSubscribe": False, "canPublishData": False}
    return _sign(_claims(cfg, identity=f"vexa-demo-{room}-{name.lower()}", video=video, ttl_sec=ttl_sec,
                         now=now, name=name), cfg.api_secret)


def admin_token(cfg: LiveKitConfig, *, room: str, now: Optional[float] = None) -> str:
    """A room-service token for one room: create it, read it, end it."""
    video = {"room": room, "roomAdmin": True, "roomCreate": True}
    return _sign(_claims(cfg, identity="vexa-meeting-api", video=video, ttl_sec=ADMIN_TTL_SEC, now=now),
                 cfg.api_secret)


class RoomService:
    """The Twirp room service over httpx (an injected AsyncClient in tests)."""

    def __init__(self, cfg: LiveKitConfig, http=None) -> None:
        self._cfg = cfg
        self._http = http

    async def _call(self, method: str, room: str, body: dict) -> dict:
        import httpx

        url = f"{self._cfg.http_url}/twirp/livekit.RoomService/{method}"
        headers = {"Authorization": f"Bearer {admin_token(self._cfg, room=room)}",
                   "Content-Type": "application/json"}
        if self._http is not None:
            resp = await self._http.post(url, json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"livekit {method} answered {resp.status_code}")
        return resp.json() if resp.content else {}

    async def create_room(self, room: str, *, metadata: dict, empty_timeout_sec: int,
                          departure_timeout_sec: int, max_participants: int) -> dict:
        return await self._call("CreateRoom", room, {
            "name": room,
            "metadata": json.dumps(metadata, separators=(",", ":")),
            "empty_timeout": int(empty_timeout_sec),
            "departure_timeout": int(departure_timeout_sec),
            "max_participants": int(max_participants),
        })

    async def update_metadata(self, room: str, metadata: dict) -> dict:
        return await self._call("UpdateRoomMetadata", room, {
            "room": room, "metadata": json.dumps(metadata, separators=(",", ":"))})

    async def list_participants(self, room: str) -> list[dict]:
        return list((await self._call("ListParticipants", room, {"room": room})).get("participants") or [])

    async def delete_room(self, room: str) -> None:
        await self._call("DeleteRoom", room, {"room": room})
