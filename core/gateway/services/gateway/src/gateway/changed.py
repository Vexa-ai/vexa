"""changed.py — what an old-style call is told, so whoever made it can fix its own call.

A request in a form this release no longer takes is refused with one machine-readable frame:

    {"type": "error", "error": "<code>", "changed_in": "0.13.4",
     "do_instead": "<one line>", "docs": "https://docs.vexa.ai/changes-0-13-4-auth"}

A configuration key this release no longer reads is named at boot (``config_key_removed``) and has
no effect.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

#: The release whose auth change these refusals describe.
CHANGED_IN = "0.13.4"
#: The page that says what changed and what to do instead.
DOCS = "https://docs.vexa.ai/changes-0-13-4-auth"

#: How a websocket sends its key.
WS_KEY_HOW = ("send the key in the x-api-key header, or (a browser) in the subprotocol list: "
              "new WebSocket(url, ['vexa.v1', 'vexa.key.' + key])")


def refusal(error: str, do_instead: str) -> dict:
    """The error frame an old-style request is answered with."""
    return {"type": "error", "error": error, "changed_in": CHANGED_IN,
            "do_instead": do_instead, "docs": DOCS}


#: What a key /ws refuses for its scopes needs instead.
SCOPE_HOW = ("use a key that holds the bot or tx scope (every key the quickstart mints holds one); "
             "GET /auth/me lists your key's scopes")


#: A websocket that names its key in the URL (``?api_key=``); close 4401 follows.
QUERY_KEY_REFUSAL = refusal("api_key_query_removed", WS_KEY_HOW)

#: Configuration keys the gateway does not read, each with what holds now.
REMOVED_KEYS = {
    "VEXA_WS_SCOPE_MODE": "/ws always takes the REST routes' key scopes",
}


def warn_removed_keys_at_boot(env: "dict | None" = None, stream=None) -> list:
    """Name every removed key this environment still sets, on stderr and as ``config_key_removed``.
    Returns the keys named. Never raises: a removed key never stops the gateway."""
    env = os.environ if env is None else env
    found = [k for k in REMOVED_KEYS if (env.get(k) or "").strip()]
    if not found:
        return found
    out: Optional[object] = sys.stderr if stream is None else stream
    for k in found:
        out.write(f"WARNING: {k} is not read since {CHANGED_IN} ({REMOVED_KEYS[k]}); unset it. {DOCS}\n")
    out.flush()
    try:
        from .obs import log_event

        for k in found:
            log_event("config_key_removed", audience="system", level="warning", span="boot",
                      fields={"key": k, "changed_in": CHANGED_IN, "docs": DOCS})
    except Exception:  # noqa: BLE001 — the line above already said it
        pass
    return found
