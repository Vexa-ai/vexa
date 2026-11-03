"""workspace_principal.py — the numeric principal a room's turn runs as (PRD 0.13.4 D2).

identity (admin-api) keeps one principal of kind ``workspace`` per workspace (``principals``,
MIGRATION-0009): its id is a dedicated account that owns nothing of a person's, and it names the
account its spend ``bills_to``. Before agent-api mints a room turn's delegation token with
``VEXA_ROOM_WS_PRINCIPAL`` on, it asks identity for that id here — ``POST
/internal/principals/workspace`` on the internal tier, idempotent — and caches the answer briefly.

FAILS CLOSED: identity unreachable or refusing means no id, and the dispatcher then mints no
toolbelt for the turn rather than falling back to the owner's account (`dispatch.build_unit_env`).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PATH = "/internal/principals/workspace"
_CACHE: dict = {}
_TTL_S = 300.0


def _post(url: str, secret: str, body: dict, *, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(f"{url.rstrip('/')}{PATH}", data=json.dumps(body).encode(),
                                 headers={"X-Internal-Secret": secret, "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def resolve(settings, workspace: str, bills_to: str, *, post: Optional[Callable] = None) -> Optional[int]:
    """The workspace's principal id, or None when identity cannot answer (the caller fails closed)."""
    url = (getattr(settings, "admin_api_url", "") or "").strip()
    try:
        secret = settings.internal_api_secret.get_secret_value()
    except Exception:  # noqa: BLE001
        secret = ""
    try:
        payer = int(str(bills_to))
    except (TypeError, ValueError):
        return None
    if not url or not secret or not str(workspace).strip():
        return None
    key = (url, str(workspace), payer)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    try:
        out = (post or _post)(url, secret, {"workspace_id": str(workspace), "bills_to": payer})
        pid = int(out["id"]) if out.get("kind") == "workspace" and out.get("status") == "active" else None
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("workspace principal for %s could not be resolved (%s): the room turn runs "
                       "without its toolbelt", workspace, type(e).__name__)
        return None
    if pid is not None:
        _CACHE[key] = (now + _TTL_S, pid)
    return pid
