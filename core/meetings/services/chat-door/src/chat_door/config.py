"""``DoorConfig`` — the chat door's configuration, read from env, secret-safe.

One rule governs this module: **the signing key is never rendered.** It is held in a
``_SigningKey`` wrapper whose ``__repr__``/``__str__`` return a fingerprint (the first 8 hex
chars of ``sha256(key)``), never the material. That means a stray ``print(config)``, a
FastAPI validation-error dump, or a traceback frame cannot leak it — the failure mode the
credential-leak advisory flagged for the BYOT story.

Env surface (all optional in dev; ``CHAT_DOOR_SIGNING_KEY`` is generated per-process when
absent, which is honest for dev and useless for anything else — links do not survive a
restart, and the startup log says so).

| Var | Default | Meaning |
|---|---|---|
| ``CHAT_DOOR_SIGNING_KEY`` | *generated* | HMAC key for magic links + sessions |
| ``CHAT_DOOR_BASE_URL`` | ``http://localhost:8080`` | public origin used to build links |
| ``CHAT_DOOR_MEETINGS_URL`` | ``http://gateway:8000`` | meeting API we consume as a client |
| ``CHAT_DOOR_MEETINGS_API_KEY`` | *unset* | ``X-API-Key`` forwarded to that API |
| ``CHAT_DOOR_STORE_DIR`` | ``./.chat-door-store`` | where user rows + personal docs live |
| ``CHAT_DOOR_RECORDS_DIR`` | *unset* | dev-only: read records off disk instead of the API (see :mod:`chat_door.local_records`) |
| ``CHAT_DOOR_LINK_TTL_SECONDS`` | ``604800`` (7d) | magic-link lifetime |
| ``CHAT_DOOR_SESSION_TTL_SECONDS`` | ``86400`` (1d) | session-cookie lifetime |
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LINK_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_SESSION_TTL_SECONDS = 24 * 3600


class SigningKey:
    """Opaque HMAC key. ``bytes(key)`` is the only way to reach the material."""

    __slots__ = ("_material", "_generated")

    def __init__(self, material: bytes, *, generated: bool = False) -> None:
        if not material:
            raise ValueError("signing key must be non-empty")
        self._material = material
        self._generated = generated

    def __bytes__(self) -> bytes:
        return self._material

    @property
    def generated(self) -> bool:
        """True when nobody supplied a key and we minted an ephemeral one (dev only)."""
        return self._generated

    @property
    def fingerprint(self) -> str:
        """Stable, non-reversible id for logs: ``sha256(key)[:8]``."""
        return hashlib.sha256(self._material).hexdigest()[:8]

    def __repr__(self) -> str:  # pragma: no cover - exercised via test_never_prints_key
        return f"<SigningKey fp={self.fingerprint}>"

    __str__ = __repr__


@dataclass(frozen=True)
class DoorConfig:
    signing_key: SigningKey
    base_url: str = "http://localhost:8080"
    meetings_url: str = "http://gateway:8000"
    meetings_api_key: str | None = field(default=None, repr=False)
    store_dir: Path = Path(".chat-door-store")
    link_ttl_seconds: int = DEFAULT_LINK_TTL_SECONDS
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    records_dir: Path | None = None

    def __repr__(self) -> str:
        # `meetings_api_key` is repr=False above; restate the whole repr so no field is
        # accidentally rendered by a future edit.
        return (
            f"DoorConfig(signing_key={self.signing_key!r}, base_url={self.base_url!r}, "
            f"meetings_url={self.meetings_url!r}, meetings_api_key=<redacted>, "
            f"store_dir={str(self.store_dir)!r}, records_dir={str(self.records_dir)!r}, "
            f"link_ttl_seconds={self.link_ttl_seconds}, "
            f"session_ttl_seconds={self.session_ttl_seconds})"
        )


def load_config(env: dict[str, str] | None = None) -> DoorConfig:
    """Build a :class:`DoorConfig` from ``env`` (defaults to ``os.environ``)."""
    e = dict(os.environ if env is None else env)
    raw = (e.get("CHAT_DOOR_SIGNING_KEY") or "").strip()
    if raw:
        key = SigningKey(raw.encode("utf-8"))
    else:
        key = SigningKey(secrets.token_bytes(32), generated=True)
    return DoorConfig(
        signing_key=key,
        base_url=(e.get("CHAT_DOOR_BASE_URL") or "http://localhost:8080").rstrip("/"),
        meetings_url=(e.get("CHAT_DOOR_MEETINGS_URL") or "http://gateway:8000").rstrip("/"),
        meetings_api_key=(e.get("CHAT_DOOR_MEETINGS_API_KEY") or None),
        store_dir=Path(e.get("CHAT_DOOR_STORE_DIR") or ".chat-door-store"),
        records_dir=Path(e["CHAT_DOOR_RECORDS_DIR"]) if e.get("CHAT_DOOR_RECORDS_DIR") else None,
        link_ttl_seconds=int(e.get("CHAT_DOOR_LINK_TTL_SECONDS") or DEFAULT_LINK_TTL_SECONDS),
        session_ttl_seconds=int(
            e.get("CHAT_DOOR_SESSION_TTL_SECONDS") or DEFAULT_SESSION_TTL_SECONDS
        ),
    )
