"""Configuration keys meeting-api does not read, and the line it prints at boot while one is still set.

A key set here has no effect. Every boot with one set names it on stderr and in the structured log
(``config_key_removed``), with what holds instead and the page that says what to do.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import List, Optional

#: The release whose change these keys describe.
CHANGED_IN = "0.13.4"
#: The page that says what changed and what to do instead.
DOCS = "https://docs.vexa.ai/changes-0-13-4-auth"

#: Each key meeting-api does not read, with what holds now.
REMOVED_KEYS = {
    "VEXA_WS_SCOPE_MODE": ("the /ws subscribe hop takes any valid key; meeting ownership, "
                           "workspace and share decide each meeting"),
    "VEXA_WEBHOOK_STORED_URL_POLICY": "every webhook delivery is held to the outbound-url.v1 guard",
    "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S": "a transcript entry is stored only when it is signed",
}

log = logging.getLogger(__name__)


def set_keys(env: Optional[dict] = None) -> List[str]:
    """The removed keys this environment still sets (to anything)."""
    env = os.environ if env is None else env
    return [k for k in REMOVED_KEYS if (env.get(k) or "").strip()]


def warn_at_boot(env: Optional[dict] = None, stream=None) -> List[str]:
    """Name every removed key still set, on stderr and as ``config_key_removed``. Never raises."""
    found = set_keys(env)
    if not found:
        return found
    out = sys.stderr if stream is None else stream
    for k in found:
        out.write(f"WARNING: {k} is not read since {CHANGED_IN} ({REMOVED_KEYS[k]}); unset it. {DOCS}\n")
    out.flush()
    try:
        from .obs import log_event

        for k in found:
            log_event("config_key_removed", audience="system", level="warning", span="boot",
                      fields={"key": k, "changed_in": CHANGED_IN, "docs": DOCS})
    except Exception:  # noqa: BLE001 — the line above already said it
        log.warning("removed configuration keys set: %s", ", ".join(found))
    return found
