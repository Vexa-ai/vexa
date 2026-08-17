"""Calendar connection value object stored inside the identity-owned user document.

Feed URLs are credentials.  Only ``internal_connections`` includes them; every
user-facing representation goes through ``masked_connection``.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException, status

MAX_CALENDAR_CONNECTIONS = 10


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


def new_connection(*, name: str, ics_url: str, auto_join: bool = True,
                   bot_name: str = "Vexa") -> dict:
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name is required")
    if len(cleaned_name) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name too long")
    return {
        "id": str(uuid4()),
        "name": cleaned_name,
        "ics_url": validate_ics_url(ics_url),
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
    url = connection.get("ics_url") or ""
    host = urlparse(url).hostname or ""
    return {
        "id": connection["id"],
        "name": connection.get("name") or "Calendar",
        "ics_url_set": bool(url),
        "ics_url_masked": f"{host}/…{url[-4:]}" if url else None,
        "auto_join": bool(connection.get("auto_join", True)),
        "bot_name": connection.get("bot_name") or "Vexa",
        "enabled": bool(connection.get("enabled", True)),
    }


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


def internal_connections(data: dict, user_id: int) -> list[dict]:
    """Flatten one user's connections for the secret-gated meeting-api edge.

    Three shapes cross the hop.  A LIVE connection carries its feed URL and syncs normally.  A
    DELETED one is a tombstone: no URL, and the sweep parses it as an empty feed so its managed
    rows retire.  A DISABLED one (``enabled: false``) is a tombstone too — a paused calendar must
    leave no meeting armed — and re-enabling re-imports on the next sweep.
    """
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
                "ics_url": connection["ics_url"],
                "auto_join": bool(connection.get("auto_join", True)),
            })
    return out
