"""livekit_rooms — agent-api's two uses of the deployment's LiveKit server (ADR-0048 decision 6).

* **A participant's room token.** The session is agent-api's, so agent-api decides who joins its
  call: a session participant gets a short-lived HS256 access token for that one room (a viewer's
  token cannot publish). The token is a LiveKit access token: ``iss`` the API key, ``sub`` the
  participant, ``video`` the grant.
* **A room's data channel.** A page shown to everyone in a call, and its highlights, go to every
  participant as one reliable data message (``send_data``, topic ``vexa.show``); nothing is stored.
* **A room's metadata.** meeting-api creates every room with metadata naming its owner and session
  (the transcription notice rides there too). Binding a call to a session reads it back from the
  server, so a call is bound only to the session its starter named, by its starter.

Configured by ``LIVEKIT_URL`` / ``LIVEKIT_INTERNAL_URL`` / ``LIVEKIT_API_KEY`` /
``LIVEKIT_API_SECRET`` — the same names meeting-api reads. Unset ⇒ ``from_settings`` is None.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional, Protocol

MIN_SECRET_LEN = 32
PARTICIPANT_TTL_SEC = 600
ADMIN_TTL_SEC = 60


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(claims: dict, secret: str) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode("utf-8"), f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


class RoomReader(Protocol):
    def room(self, name: str) -> Optional[dict]:
        """The live room by name — ``{"name", "metadata": dict, "num_participants"}`` — or None."""
        ...


@dataclass(frozen=True)
class LiveKit:
    url: str
    api_key: str
    api_secret: str
    internal_url: str = ""

    @classmethod
    def from_settings(cls, settings) -> Optional["LiveKit"]:
        url = (getattr(settings, "livekit_url", "") or "").strip()
        if not url:
            return None
        secret = settings.livekit_api_secret.get_secret_value()
        if not url.startswith(("ws://", "wss://")):
            raise ValueError("LIVEKIT_URL must be a ws:// or wss:// address")
        if not settings.livekit_api_key or len(secret) < MIN_SECRET_LEN:
            raise ValueError(f"LIVEKIT_API_KEY is empty or LIVEKIT_API_SECRET is shorter than {MIN_SECRET_LEN}")
        return cls(url=url, api_key=settings.livekit_api_key, api_secret=secret,
                   internal_url=(settings.livekit_internal_url or "").strip())

    @property
    def http_url(self) -> str:
        u = (self.internal_url or self.url).rstrip("/")
        for ws, http in (("wss://", "https://"), ("ws://", "http://")):
            if u.startswith(ws):
                return http + u[len(ws):]
        return u

    def participant_token(self, *, room: str, identity: str, name: str = "", can_publish: bool = True,
                          ttl_sec: int = PARTICIPANT_TTL_SEC, now: Optional[float] = None) -> str:
        t = int(time.time() if now is None else now)
        claims = {"iss": self.api_key, "sub": identity, "nbf": t, "exp": t + int(ttl_sec),
                  "video": {"room": room, "roomJoin": True, "canSubscribe": True,
                            "canPublish": bool(can_publish), "canPublishData": bool(can_publish)}}
        if name:
            claims["name"] = name
        return sign(claims, self.api_secret)

    def _admin_token(self, room: str) -> str:
        t = int(time.time())
        return sign({"iss": self.api_key, "sub": "vexa-agent-api", "nbf": t, "exp": t + ADMIN_TTL_SEC,
                     "video": {"room": room, "roomAdmin": True, "roomList": True}}, self.api_secret)

    def room(self, name: str) -> Optional[dict]:
        """The live room by name, from the room service (Twirp ``ListRooms``), or None."""
        import httpx

        resp = httpx.post(f"{self.http_url}/twirp/livekit.RoomService/ListRooms",
                          json={"names": [name]}, timeout=10.0,
                          headers={"Authorization": f"Bearer {self._admin_token(name)}"})
        resp.raise_for_status()
        for r in (resp.json() or {}).get("rooms") or []:
            if r.get("name") == name:
                out = {"name": name, "metadata": parse_metadata(r.get("metadata")),
                       "num_participants": int(r.get("num_participants") or 0)}
                if out["num_participants"]:
                    out["humans"] = self._humans(name)
                return out
        return None

    def _humans(self, room: str) -> int:
        """The PEOPLE in a room: every participant but the hidden ones (the call's listener)."""
        import httpx

        try:
            resp = httpx.post(f"{self.http_url}/twirp/livekit.RoomService/ListParticipants",
                              json={"room": room}, timeout=10.0,
                              headers={"Authorization": f"Bearer {self._admin_token(room)}"})
            resp.raise_for_status()
            ps = (resp.json() or {}).get("participants") or []
        except Exception:  # noqa: BLE001 — unknown: count everyone, never end a call on a failed read
            return 10 ** 6
        return sum(1 for p in ps if not ((p.get("permission") or {}).get("hidden")))

    def delete_room(self, room: str) -> bool:
        """End a call's room (Twirp ``DeleteRoom``): everyone in it is disconnected."""
        import httpx

        try:
            resp = httpx.post(f"{self.http_url}/twirp/livekit.RoomService/DeleteRoom",
                              json={"room": room}, timeout=10.0,
                              headers={"Authorization": f"Bearer {self._admin_token(room)}"})
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    def remove_participant(self, room: str, identity: str) -> bool:
        """Take one person out of a live room (Twirp ``RemoveParticipant``): their connection ends at
        once. True when the server removed them; False when they were not there or the server could
        not be reached (their room token still expires within ``PARTICIPANT_TTL_SEC``)."""
        import httpx

        try:
            resp = httpx.post(f"{self.http_url}/twirp/livekit.RoomService/RemoveParticipant",
                              json={"room": room, "identity": str(identity)}, timeout=10.0,
                              headers={"Authorization": f"Bearer {self._admin_token(room)}"})
        except httpx.HTTPError:
            return False
        return resp.status_code < 400


    def send_data(self, room: str, payload: dict, *, topic: str) -> bool:
        """Send one reliable data message to everyone in a live room (Twirp ``SendData``) — the call's
        ephemeral events (a page shown to everyone, a highlight). Nothing is stored. True when the
        server took it."""
        import httpx

        body = {"room": room, "data": base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode("ascii"),
                "kind": "RELIABLE", "topic": topic}
        try:
            resp = httpx.post(f"{self.http_url}/twirp/livekit.RoomService/SendData", json=body, timeout=10.0,
                              headers={"Authorization": f"Bearer {self._admin_token(room)}"})
        except httpx.HTTPError:
            return False
        return resp.status_code < 400


def parse_metadata(raw) -> dict:
    try:
        meta = json.loads(raw or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def room_ended(room: Optional[dict]) -> bool:
    """Has this call's meeting ended? meeting-api marks the room's metadata ``ended`` the moment the
    call's meeting is terminal (its listener is gone) and removes the room when nobody is in it. A
    room still standing with that mark is a call that is over: starting the call again must make a
    fresh meeting, never rejoin this one (live app.dev 2026-10-18: "transcription does not start the
    second time")."""
    return bool(((room or {}).get("metadata") or {}).get("ended"))


def http_share_redeemer(meeting_api_url: str, internal_secret: str):
    """Redeem a call's transcript share AS the participant joining it: meeting-api
    ``POST /transcripts/share/accept`` over the internal tier, naming the participant. Returns a
    callable ``(subject, email, token) -> bool``; False when meeting-api refuses or is unreachable."""
    base = (meeting_api_url or "").rstrip("/")

    def _redeem(subject: str, email: str, token: str) -> bool:
        if not base or not internal_secret or not subject:
            return False
        import httpx

        headers = {"X-User-Id": str(subject), "X-Internal-Secret": internal_secret}
        if email:
            headers["X-User-Email"] = email
        try:
            resp = httpx.post(f"{base}/transcripts/share/accept", json={"token": token},
                              headers=headers, timeout=10.0)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    return _redeem
