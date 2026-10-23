"""The 0.12 compatibility switches meeting-api reads, and the banner it prints at boot while one is open.

Each switch ships closed. Opening one re-admits a 0.12 behaviour for an upgrade that still needs it,
and is a residual risk for as long as it stays open, so every boot with one open says so loudly on
stderr and in the structured log (``compat_switch_open``), with the release that removes it.

==========================================  ======================  ===========  ==========
switch                                      open when               removed in   log line
==========================================  ======================  ===========  ==========
``VEXA_WS_SCOPE_MODE``                      ``legacy``              0.14.0       ``ws_scope_legacy_allowed``
``VEXA_WEBHOOK_STORED_URL_POLICY``          ``v012``                0.14.0       ``webhook_stored_url_v012``
``VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S`` a number above ``0``    0.14.0       ``segment_entry_legacy_admitted``
==========================================  ======================  ===========  ==========
"""
from __future__ import annotations

import logging
import os
import sys
from typing import List, Optional

from .collector import legacy_unsigned
from .route_scopes import WS_SCOPE_MODE_ENV
from .webhooks import stored_url_policy

#: The release that removes every switch below.
REMOVED_IN = "0.14.0"

log = logging.getLogger(__name__)


def open_switches(env: Optional[dict] = None) -> List[dict]:
    """The switches this environment opens, each as ``{key, value, reopens}``."""
    env = os.environ if env is None else env
    found = []
    if (env.get(WS_SCOPE_MODE_ENV) or "").strip().lower() == "legacy":
        found.append({"key": WS_SCOPE_MODE_ENV, "value": "legacy",
                      "reopens": "any valid key, a bot-only one included, streams its owner's transcripts over /ws"})
    if stored_url_policy.policy(env) == "v012":
        found.append({"key": stored_url_policy.POLICY_ENV, "value": "v012",
                      "reopens": "per-client webhooks may reach 100.64/10 and the documentation and benchmarking ranges"})
    window = legacy_unsigned.window_seconds(env)
    if window > 0:
        found.append({"key": legacy_unsigned.WINDOW_ENV, "value": str(window),
                      "reopens": f"for {window} s after this start, unsigned transcript entries of meetings a 0.12 bot "
                                 "was already in are admitted"})
    return found


def banner(switches: List[dict], service: str = "meeting-api") -> str:
    rule = "#" * 96
    lines = [f"WARNING: {service} runs with {len(switches)} 0.12 compatibility switch(es) OPEN.", ""]
    for s in switches:
        lines += [f"  {s['key']}={s['value']}", f"    re-opens: {s['reopens']}"]
    lines += ["",
              f"Each is a residual risk while it is set, and each is removed in {REMOVED_IN}. Unset it once the",
              "0.12 callers it serves have moved: https://docs.vexa.ai/upgrade-from-0-12#compatibility-switches"]
    return "\n".join([rule, *(f"##  {line}".rstrip() for line in lines), rule]) + "\n"


def warn_at_boot(env: Optional[dict] = None, stream=None) -> List[dict]:
    """Print the banner on stderr and log one ``compat_switch_open`` per open switch. Never raises."""
    switches = open_switches(env)
    if not switches:
        return switches
    out = sys.stderr if stream is None else stream
    out.write(banner(switches))
    out.flush()
    try:
        from .obs import log_event

        for s in switches:
            log_event("compat_switch_open", audience="system", level="warning", span="boot",
                      fields={"key": s["key"], "value": s["value"], "removed_in": REMOVED_IN})
    except Exception:  # noqa: BLE001 — the banner above already said it
        log.warning("compat switches open: %s", ", ".join(s["key"] for s in switches))
    return switches
