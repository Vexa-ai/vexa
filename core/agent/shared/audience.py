"""audience.py — who reads each of a running unit's mounts NOW, where its worker can read it (D6, SC4).

The worker's commit hold (`llm/private_writes`) keeps a session that read the person's private data
from committing into a mount other people read. It cannot read the membership store itself, so
agent-api tells it. The dispatch's stamp (``VEXA_MOUNT_READERS``) and each turn's signed stream entry
(``readers``) are as fresh as the turn; a member added while the turn runs would not be counted until
the next one. This key closes that gap: agent-api writes it at every dispatch AND again whenever a
roster changes, and the worker reads it at the moment it commits.

Keys (agent-api the one writer; the worker's Redis user may only read its own unit's key,
``workload_redis``):

* ``vexa:audience:unit:<unit>`` — JSON ``{"readers": {mount path: [readers] | null},
  "revoked": [mount path, …], "unknown": bool}``. ``revoked`` names a mount the subject no longer
  holds by membership (removed, or made a viewer of a mount it writes): nothing of the turn is
  committed there, and the worker stops its turn, puts the mount back and ends. A later dispatch
  that no longer mounts it keeps it named, for the worker still holding it. ``unknown: true`` is what agent-api writes when it could not re-read a roster:
  every mount is then held.
* ``vexa:audience:ws:<workspace id>`` — the set of units whose mounts include that shared workspace,
  so a roster change knows whose key to rewrite.

Reading: the key present → it decides; the key absent (no Redis at dispatch, a unit older than this
key) → the turn's stamp decides; the key unreadable or ``unknown`` → every mount is held.
"""
from __future__ import annotations

import json
from typing import Optional

PREFIX = "vexa:audience"
#: How long a unit's key and a workspace's unit set outlive the last dispatch that wrote them.
TTL_SEC = 24 * 3600


def unit_key(unit: str) -> str:
    return f"{PREFIX}:unit:{unit}"


def index_key(workspace_id: str) -> str:
    return f"{PREFIX}:ws:{workspace_id}"


def encode(readers: dict, *, revoked=(), unknown: bool = False) -> str:
    return json.dumps({"readers": readers or {}, "revoked": sorted({str(p) for p in revoked or ()}),
                       "unknown": bool(unknown)})


def decode(raw) -> Optional[dict]:
    """``{"readers": {path: list | None}, "revoked": set, "unknown": bool}``; None when ``raw`` is
    empty; ``{"unknown": True}`` when it does not parse (held, never trusted)."""
    if raw is None or raw == "" or raw == b"":
        return None
    try:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (ValueError, UnicodeDecodeError):
        return {"readers": {}, "revoked": set(), "unknown": True}
    if not isinstance(data, dict) or not isinstance(data.get("readers", {}), dict):
        return {"readers": {}, "revoked": set(), "unknown": True}
    readers = {str(k): ([str(r) for r in v] if isinstance(v, list) else None)
               for k, v in (data.get("readers") or {}).items()}
    revoked = {str(p) for p in data.get("revoked") or [] if isinstance(p, str)}
    return {"readers": readers, "revoked": revoked, "unknown": bool(data.get("unknown"))}
