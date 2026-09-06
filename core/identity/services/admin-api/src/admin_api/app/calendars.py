"""Calendar connection value object stored inside the identity-owned user document.

Feed URLs and refresh tokens are credentials.  Only ``internal_connections`` includes them; every
user-facing representation goes through ``masked_connection``.

A connection names its ``provider``.  ``ics`` is the original: the user pastes a secret feed URL.
``google`` and ``microsoft`` are OAuth-backed and carry a refresh token instead — **encrypted at
rest** through ``field_crypto``, never stored in the clear.  The two kinds differ in exactly one
more place than you would expect: an ICS connection is mirrored into the legacy singular
``calendar_ics_url`` keys for old clients, and an OAuth one is not, because those keys mean "a feed
URL" to every reader that predates this.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException, status

MAX_CALENDAR_CONNECTIONS = 10

PROVIDER_ICS = "ics"
OAUTH_PROVIDERS = frozenset({"google", "microsoft"})
REFRESH_TOKEN_FIELD = "calendar_refresh_token"


def validate_bot_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bot_name is required")
    if len(name) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bot_name too long")
    return name


def validate_ics_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ics_url is required")
    if len(url) > 2048:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="ics_url too long")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="ics_url must be an http(s) URL")
    if "/calendar/embed" in (parsed.path or "").lower():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=("that's the calendar's embed page, not its feed - in Google Calendar "
                    "open Settings -> Integrate calendar and copy the 'Secret address in "
                    "iCal format' (ends in .ics)"),
        )
    return url


def _legacy_id(user_id: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"vexa:user:{user_id}:calendar:legacy"))


def connections_from_data(data: dict, user_id: int, *, include_deleted: bool = False) -> list[dict]:
    raw = data.get("calendar_connections")
    if isinstance(raw, list):
        connections = [dict(item) for item in raw if isinstance(item, dict) and item.get("id")]
    else:
        legacy_url = data.get("calendar_ics_url")
        connections = ([{
            "id": _legacy_id(user_id),
            "name": "Calendar",
            "ics_url": legacy_url,
            "auto_join": bool(data.get("calendar_auto_join", True)),
            "bot_name": data.get("calendar_bot_name") or "Vexa",
            "enabled": True,
        }] if legacy_url else [])
    return connections if include_deleted else [c for c in connections if not c.get("deleted")]


def _validate_name(name: str) -> str:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name is required")
    if len(cleaned_name) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name too long")
    return cleaned_name


def new_connection(*, name: str, ics_url: str, auto_join: bool = True,
                   bot_name: str = "Vexa") -> dict:
    return {
        "id": str(uuid4()),
        "name": _validate_name(name),
        "provider": PROVIDER_ICS,
        "ics_url": validate_ics_url(ics_url),
        "auto_join": bool(auto_join),
        "bot_name": validate_bot_name(bot_name),
        "enabled": True,
    }


def new_oauth_connection(*, name: str, provider: str, refresh_token: str,
                         account_email: str = "", provider_calendar_id: str = "primary",
                         auto_join: bool = True, bot_name: str = "Vexa",
                         box=None) -> dict:
    """An OAuth-backed connection. The refresh token is encrypted HERE, or the call is refused.

    ``box`` is the user's ``field_crypto.UserSecretBox``. It is optional in the signature only so
    this module stays importable on a deployment with no KEK — but a missing box while creating an
    OAuth connection is a **hard stop**, never a fallback to plaintext. A feed URL in the clear
    exposes one calendar's contents; a refresh token in the clear is ongoing, silent read access
    until somebody revokes it, and the two do not deserve the same treatment.
    """
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"provider must be one of {', '.join(sorted(OAUTH_PROVIDERS))}")
    if not (refresh_token or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="refresh_token is required")
    if box is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("calendar sign-in is unavailable on this deployment: no secret-encryption key "
                    "is configured, and a refresh token is never stored unencrypted"),
        )
    return {
        "id": str(uuid4()),
        "name": _validate_name(name),
        "provider": provider,
        "refresh_token": box.encrypt(REFRESH_TOKEN_FIELD, refresh_token.strip()),
        "account_email": (account_email or "").strip().lower() or None,
        "provider_calendar_id": (provider_calendar_id or "primary").strip(),
        "auto_join": bool(auto_join),
        "bot_name": validate_bot_name(bot_name),
        "enabled": True,
    }


def store_connections(data: dict, connections: list[dict]) -> dict:
    """Persist the plural authority and mirror its first active row for old clients."""
    out = dict(data)
    out["calendar_connections"] = connections
    active = next((c for c in connections if not c.get("deleted") and c.get("ics_url")), None)
    if active:
        out["calendar_ics_url"] = active["ics_url"]
        out["calendar_auto_join"] = bool(active.get("auto_join", True))
    else:
        out.pop("calendar_ics_url", None)
        out.pop("calendar_auto_join", None)
    return out


def masked_connection(connection: dict) -> dict:
    """The user-facing shape. Built by ALLOWLIST, never by deleting secrets from a copy.

    A denylist ("copy the connection, pop the token") leaks by default: the next field anyone adds
    is exposed until somebody remembers to remove it. This returns only named keys, so a new secret
    is invisible until it is deliberately published — and there is a test that a refresh token
    cannot appear here.
    """
    url = connection.get("ics_url") or ""
    host = urlparse(url).hostname or ""
    provider = connection.get("provider") or PROVIDER_ICS
    masked = {
        "id": connection["id"],
        "name": connection.get("name") or "Calendar",
        "provider": provider,
        "ics_url_set": bool(url),
        "ics_url_masked": f"{host}/…{url[-4:]}" if url else None,
        "auto_join": bool(connection.get("auto_join", True)),
        "bot_name": connection.get("bot_name") or "Vexa",
        "enabled": bool(connection.get("enabled", True)),
    }
    if provider in OAUTH_PROVIDERS:
        # Which account is connected is the useful thing to show — it is how a user with two Google
        # accounts tells the connections apart. The token itself has no masked form: unlike a feed
        # URL there is no recognisable tail worth showing, so nothing about it is published beyond
        # whether the connection is authorised at all.
        masked["account_email"] = connection.get("account_email")
        masked["connected"] = bool(connection.get("refresh_token"))
    return masked


def legacy_connection_id(connections: list[dict], user_id: int) -> Optional[str]:
    """Which connection owns the meeting rows stamped by the singular (pre-plural) feed.

    Those rows carry ``data.calendar_uid`` and no ``calendar_sources``, so they name no
    connection.  Exactly ONE connection may claim them, or every other calendar's sweep would
    read them as its own and cancel them: the connection synthesized from the legacy keys when
    one exists, otherwise the first connection in the list (the one the singular feed migrated
    into).  ``None`` when the user has no connections at all."""
    synthesized = _legacy_id(user_id)
    if any(connection.get("id") == synthesized for connection in connections):
        return synthesized
    return connections[0]["id"] if connections else None


def internal_connections(data: dict, user_id: int, *, cipher=None) -> list[dict]:
    """Flatten one user's connections for the secret-gated meeting-api edge.

    Three shapes cross the hop.  A LIVE connection carries its credential and syncs normally.  A
    DELETED one is a tombstone: no credential, and the sweep parses it as an empty feed so its
    managed rows retire.  A DISABLED one (``enabled: false``) is a tombstone too — a paused calendar
    must leave no meeting armed — and re-enabling re-imports on the next sweep.

    An OAuth connection's refresh token is **decrypted here**, on the way out, and crosses only this
    internal hop — exactly as the secret feed URL already does.  Decryption stays on this side of
    the wall so the whole decrypt-then-use path is one grep in one service.

    A connection whose token will not decrypt is skipped, not raised on.  This function takes the
    CIPHER rather than an already-opened box on purpose: opening the box is itself a decrypt (it
    unwraps the user's data key) and so is itself a place that throws.  Taking the box would have
    moved that failure to the caller — the config sweep — where one user's rotated KEK blanks the
    whole list and stops every other user's calendar.  The unreadable connection surfaces as
    ``unreadable`` so the caller can say "reconnect" instead of silently syncing nothing.
    """
    from .field_crypto import SecretCryptoError

    box = cipher.open_user(user_id, data) if cipher is not None else None
    connections = connections_from_data(data, user_id, include_deleted=True)
    legacy_id = legacy_connection_id(connections, user_id)
    out = []
    for connection in connections:
        entry = {
            "user_id": user_id,
            "calendar_id": connection["id"],
            "calendar_name": connection.get("name") or "Calendar",
            "bot_name": connection.get("bot_name") or "Vexa",
        }
        if connection["id"] == legacy_id:
            entry["legacy"] = True
        if connection.get("deleted"):
            out.append({**entry, "deleted": True})
        elif not connection.get("enabled", True):
            out.append({**entry, "deleted": False, "paused": True})
        elif connection.get("ics_url"):
            out.append({
                **entry,
                "provider": PROVIDER_ICS,
                "ics_url": connection["ics_url"],
                "auto_join": bool(connection.get("auto_join", True)),
            })
        elif connection.get("provider") in OAUTH_PROVIDERS and connection.get("refresh_token"):
            try:
                token = (box.decrypt(REFRESH_TOKEN_FIELD, connection["refresh_token"])
                         if box is not None else None)
            except SecretCryptoError:
                token = None
            if not token:
                out.append({**entry, "provider": connection["provider"], "unreadable": True})
                continue
            out.append({
                **entry,
                "provider": connection["provider"],
                "refresh_token": token,
                "provider_calendar_id": connection.get("provider_calendar_id") or "primary",
                "account_email": connection.get("account_email"),
                "auto_join": bool(connection.get("auto_join", True)),
            })
    return out
