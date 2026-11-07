"""features.py — which unproven features this install serves (``VEXA_FEATURES``), as the edge sees them.

The edge answers two install-scope switches, at boot, before ``FastApiMCP`` derives the surface:

  * ``browser_bots`` off ⇒ its three built-in tools (:data:`BOT_ACCOUNT_TOOLS`) are not registered,
    so ``tools/list`` does not name them (meeting-api refuses their routes anyway);
  * ``crm`` off ⇒ no assembled tool of domain ``crm`` is bound (crm-api lists none and refuses).

Agent-domain tools are not filtered here: agent-api's served manifest already leaves out the tools of
every feature that is off for it, and a second filter would be a second answer.

The registry and the parse rules are agent-api's, ``core/agent/control_plane/features.py``
(``install_defaults``); a domain may not import another, so this is a copy of that parse, held to it
by ``tests/test_features.py``:

    empty or unset  every feature off (the release default)
    ``all``         every feature on (a dev stack)
    ``a,b``         just the named ones on
    ``all,-a``      a ``-name`` after ``all`` takes one back
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from .manifest import Assembly

ENV = "VEXA_FEATURES"
BROWSER_BOTS = "browser_bots"
CRM = "crm"
#: The edge's own tools that belong to ``browser_bots`` (operation ids in app.py).
BOT_ACCOUNT_TOOLS = frozenset({"browser_session_open", "bot_accounts_list", "bot_account_verify"})
#: Assembled domains that are a feature of their own.
FEATURE_DOMAINS = {CRM: CRM}


def switched_on(name: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """``name``'s answer from ``VEXA_FEATURES``, by agent-api's ``install_defaults`` rules."""
    raw = str((env if env is not None else os.environ).get(ENV) or "")
    on = False
    for token in (t.strip().lower() for t in raw.split(",")):
        if not token:
            continue
        value = not token.startswith("-")
        token = token.lstrip("-+").strip()
        if token in ("all", name):
            on = value
    return on


def drop_switched_off_domains(assembly: Assembly, env: Optional[Mapping[str, str]] = None) -> Assembly:
    """``assembly`` without the tools of a domain whose feature is off (in place, and returned)."""
    off = {domain for domain, feature in FEATURE_DOMAINS.items() if not switched_on(feature, env)}
    if off:
        assembly.tools = [t for t in assembly.tools if t.domain not in off]
        for domain in off:
            assembly.by_domain.pop(domain, None)
    return assembly
